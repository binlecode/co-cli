# Co CLI — Sessions

> Peer tier: [memory.md](memory.md) (long-term declarative artifacts). Compaction (in-place transcript rewrite): [compaction.md](compaction.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md).

Foundation spec for the session tier — append-only transcripts of past conversations, recalled via file-based lexical (ripgrep) search with line citations.

Session is one of five operational tiers in the agent loop: **doctrine** ([personality.md](personality.md), identity), **tools** ([tools.md](tools.md), capability), **skills** ([skills.md](skills.md), procedure), **memory** ([memory.md](memory.md) — long-term declarative artifacts), and **session** (this file — past conversation transcripts). Session and memory are peer tiers with distinct domain logic, mutation models, and lifecycle policies. Memory is curated and hybrid-indexed; session transcripts are uncurated and searched lexically over the raw files — no index, no chunk pipeline, no embeddings.

## 1. Functional Architecture

Sessions hold append-only JSONL transcripts of past conversations. Each session is uniquely identified by an 8-char UUID suffix embedded in its filename. Mutation is append-only via `persist_session_history()`; compaction rewrites the file in place but preserves the session UUID.

There is no LLM call on the recall path — session_search returns lexically-matched, line-cited hits directly. Compaction-driven rewrites are the only mutation surface beyond append.

### Storage

- Path: `~/.co-cli/sessions/*.jsonl` (the `sessions_dir` workspace path on `CoDeps`).
- Format: one message per JSONL line; pydantic-ai history is the source of truth.
- Filename: `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl`. The 8-char UUID suffix is the canonical session identifier.
- Mutation: append-only via `persist_session_history()`; rewritten in place on compaction (see [compaction.md](compaction.md)).
- Retention: opt-in age-based pruning by the dream daemon's housekeeping pass — transcripts older than `dream.session_retention_days` are deleted (`0`, the default, disables it). The live session is appended every turn so its mtime stays recent and an age cutoff never selects it. See [dream.md](dream.md).

## 2. Architecture layers

```
co_cli/tools/session/    Agent surface — session_search, session_view
        ↓
co_cli/session/          Domain — SessionStore (file-based, no index) + _search.py (ripgrep)
        ↓
                         ~/.co-cli/sessions/*.jsonl (raw transcript files)
```

The session domain owns transcript extraction, JSONL line tracking, and lexical search. There is no index, chunk pipeline, or embedding — `SessionStore` holds no `IndexStore` reference. (Memory and canon keep using the shared `IndexStore`; see [memory.md §2](memory.md).)

## 3. Core Logic

### Lexical search

Sessions are searched in place over the raw `*.jsonl` files — no write-time indexing.

| Property | Value |
| --- | --- |
| Source value | `'session'` (the `source` field on each hit) |
| Search entry point | `SessionStore.search(query, limit, *, is_regex=False)` → `search_sessions(sessions_dir, query, limit, *, is_regex=False)` (`co_cli/session/_search.py`); returns a `SessionSearchResult` (`hits` + optional `error`) |
| Match engine | `rg --fixed-strings --ignore-case --no-config` over `sessions_dir/*.jsonl` (literal default); regex mode (`is_regex=True`) drops `--fixed-strings` so the query is a pattern. Python line-scan fallback when `rg` is absent |
| Match semantics | literal default — case-insensitive substring of the raw, on-disk JSONL line (pydantic-core `dump_json` writes literal UTF-8, so unicode/accented/CJK queries match raw). Regex mode matches a case-insensitive pattern instead, `re.compile`-validated up front (invalid pattern → `SessionSearchResult.error`, never dispatched, never an empty "no results") |
| Snippet | matched line → `extract_messages()` → the retained part whose content contains the query; a match landing only on a structural JSON key/value is dropped |
| Citation | `start_line == end_line ==` the matched JSONL line (1-indexed) |
| Ranking | `(match_count desc, recency desc)`; `score` is the match count (synthetic) |
| `path` value | the 8-char UUID suffix (not a filesystem path), parsed from the filename |

### Compaction interplay

Compaction rewrites the live session JSONL in place when context pressure crosses the spill threshold (see [compaction.md](compaction.md)). After rewrite:

- The session UUID stays the same — filename does not change.
- ripgrep reads the rewritten file directly, so search reflects post-compaction content with no re-sync step.
- Older sessions are unaffected; compaction is per-session.

### Recall pipeline

`_search_sessions(ctx, query, span, is_regex)` routes through:

```
_search_sessions(query, is_regex) → SessionStore.search(query, limit=15, is_regex=is_regex)
    → dedup to _SESSIONS_CHANNEL_CAP = 3 unique sessions
    → exclude the current session by UUID
```

The tool exposes literal (`query=`) and regex (`pattern=`) search; the two are mutually exclusive (both supplied → `tool_error`). A non-empty `pattern` routes through the same channel with `is_regex=True`. An invalid regex returns a `tool_error` carrying the compile error, never a silent empty result.

Browse mode (`session_search()` with neither `query` nor `pattern`) skips the search path and returns recent-session metadata (id, date, title, file size) via `_browse_recent()`. The current session is excluded in both modes.

### Durable token-usage ledger

Provider-reported token usage is recorded to a durable append-only ledger at `~/.co-cli/usage.jsonl` (the `USAGE_LOG` config constant; `usage_log_path` on `CoDeps`). This is **write-only observational accounting** — it never feeds compaction triggers or the status-line context-% (those stay on the realtime `current_request_tokens_estimate`; see [core-loop.md](core-loop.md)).

- **Capture.** Token counts come from the provider-reported usage (`result.usage()` / `RunUsage` — ground truth, never `chars/4`), accumulated into a turn-scoped `UsageAccumulator` shared by reference across `fork_deps` so subagent and compaction-summarizer tokens roll into the active turn. Capture happens **once per run at the run-result boundary** via `record_usage(deps, usage)`: the orchestrator turn records its final cumulative `latest_usage` in the `run_turn_owned` `finally` block (covering every return path — success, cap-stop, error, interrupt), `run_standalone` records each task-agent run once, and the direct `llm_call` path records each call once. Because `RunUsage` is cumulative within a turn, recording once at the boundary — not per model request — is what avoids double-counting.
- **Per-turn flush.** At the turn boundary the turn's totals are appended as one JSON line, then the accumulator is reset (see [core-loop.md](core-loop.md)). One line per turn; append-only, no read-modify-write, no TTL (matches the transcript no-TTL policy).
- **Record shape.** `{turn_ended_at, origin, session_id, input_tokens, output_tokens}`. `origin` is `"session"` (a real turn, keyed by the 8-char session id) or `"daemon"` (dream-daemon model spend, `session_id` null — see [dream.md](dream.md)). Cross-process appends from the session and daemon processes are atomic: each line is well under `PIPE_BUF` and writes use `O_APPEND`.
- **Reporting.** `/usage` (no arg) sums the current session's `origin="session"` lines; `/usage week|month|total` sums a rolling window (`turn_ended_at` cutoff) split into Session / Daemon / Total, with daemon counted toward the total but never folded into the session figure. See [tui.md](tui.md).

## 4. Config

Session search is file-based and has no configurable settings — there are no chunk, embedding, or backend knobs. The `memory.*` retrieval settings in [memory.md §3](memory.md) govern the memory/canon hybrid index only; sessions ignore them.

Session *retention* is the one configurable lifecycle knob: `dream.session_retention_days` (`CO_DREAM_SESSION_RETENTION_DAYS`, default `0` = disabled) caps transcript age. It lives in `DreamSettings` because the dream daemon's housekeeping pass enforces it; see [dream.md §Config](dream.md).

## 5. Public Interface

### Model-callable tools

| Symbol | Source | Contract |
| --- | --- | --- |
| `session_search(ctx, query, pattern, limit=3)` | `co_cli/tools/session/recall.py` | Async tool — line-cited recall over past sessions; `query` literal, `pattern` regex (mutually exclusive); current session excluded; neither supplied → recent-session browse |
| `session_view(ctx, session_id, start_line, end_line)` | `co_cli/tools/session/view.py` | Async tool — verbatim JSONL line-range reader by uuid8; `tool_error` for unknown id |

Result shape for `session_search`:

```python
{
    "session_id": <uuid8>,
    "when": <ISO date prefix>,
    "source": "session",
    "chunk_text": <readable matched-part content>,
    "start_line": <JSONL line>,
    "end_line": <JSONL line>,
    "score": <match count>,
}
```

### Domain API

| Symbol | Source | Contract |
| --- | --- | --- |
| `SessionStore(config, sessions_dir)` | `co_cli/session/store.py` | File-based domain store — no IndexStore reference |
| `SessionStore.search(query, limit, *, is_regex=False)` | `co_cli/session/store.py` | Lexical (default) or regex (`is_regex`) ripgrep recall over transcript files; returns a `SessionSearchResult` |
| `SessionStore.count()` | `co_cli/session/store.py` | Number of `*.jsonl` transcript files on disk |
| `search_sessions(sessions_dir, query, limit, *, is_regex=False)` | `co_cli/session/_search.py` | ripgrep (Python-fallback) search → `SessionSearchResult`; literal default, regex when `is_regex` |
| `SessionSearchResult` | `co_cli/session/_search.py` | Search outcome: `hits` (ranked `SessionHit` list) + `error` (non-None only on an invalid regex; distinct from empty `hits`) |
| `SessionHit` | `co_cli/session/_search.py` | Result record: `path` (uuid8), `snippet`, `start_line`, `end_line`, `created_at`, `source`, `score` |

### Persistence and extraction helpers

| Symbol | Source | Contract |
| --- | --- | --- |
| `persist_session_history(...)` | `co_cli/session/persistence.py` | Append-only writer; on compaction rewrites the file in place |
| `append_messages(path, messages)` | `co_cli/session/persistence.py` | Tail-append used by `_finalize_turn` |
| `load_transcript(path)` | `co_cli/session/persistence.py` | Reads a JSONL transcript back into pydantic-ai messages |
| `UsageAccumulator` | `co_cli/observability/usage.py` | Turn-scoped token tally (`input_tokens`/`output_tokens`, `add`/`reset`); fork-shared by reference |
| `record_usage(deps, usage)` | `co_cli/observability/usage.py` | Best-effort bump of `deps.usage_accumulator` from a provider usage object |
| `append_turn(ledger_path, *, origin, session_id, input_tokens, output_tokens, turn_ended_at)` | `co_cli/session/usage.py` | Best-effort append of one ledger line; no-op when both counts are 0 |
| `aggregate(ledger_path, *, since, session_id, origin)` | `co_cli/session/usage.py` | Streams the ledger → `UsageWindow` (Session / Daemon / Total totals + distinct-session count) |
| `session_filename(created_at, session_id)` | `co_cli/session/filename.py` | Builds `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl` |
| `parse_session_filename(name)` | `co_cli/session/filename.py` | Parses session filename into `(uuid8, timestamp)` |
| `find_latest_session(sessions_dir)` | `co_cli/session/filename.py` | Returns most recent transcript path by filename order |
| `new_session_path(sessions_dir)` | `co_cli/session/filename.py` | Mints a fresh transcript path; does not create the file |
| `extract_messages(path)` | `co_cli/session/transcript.py` | JSONL line parser: `ExtractedMessage` records with `line_index` |

### Bootstrap-side entry points

| Symbol | Source | Contract |
| --- | --- | --- |
| `restore_session(deps, frontend) -> Path` | `co_cli/bootstrap/core.py` | Picks the most recent transcript; sets `deps.session.session_path` |

### Session browser (used by `/sessions` and `/resume`)

| Symbol | Source | Contract |
| --- | --- | --- |
| `SessionSummary` | `co_cli/session/browser.py` | Dataclass — `session_id`, `created_at`, `title`, `file_size`, `path` |
| `list_sessions(sessions_dir)` | `co_cli/session/browser.py` | Returns picker metadata for past transcripts |
| `format_file_size(size)` | `co_cli/session/browser.py` | Pretty file-size formatter used by the picker |

## 6. Files

| File | Purpose |
| --- | --- |
| `co_cli/session/store.py` | `SessionStore` — file-based domain store (no index) |
| `co_cli/session/_search.py` | ripgrep lexical search + Python fallback; `SessionHit`, `search_sessions()` |
| `co_cli/session/filename.py` | filename parsing/generation, latest-session discovery |
| `co_cli/session/persistence.py` | transcript append/load, in-place rewrite on compaction |
| `co_cli/session/usage.py` | durable token-usage ledger: `ORIGIN_*`, `UsageTotals`, `UsageWindow`, `append_turn`, `aggregate` |
| `co_cli/observability/usage.py` | realtime turn-scoped accumulator: `UsageAccumulator`, `record_usage` |
| `co_cli/session/browser.py` | session listing and picker metadata |
| `co_cli/session/transcript.py` | JSONL line parser: `ExtractedMessage`, `extract_messages()` |
| `co_cli/tools/session/recall.py` | `session_search` — recall and browse modes |
| `co_cli/tools/session/view.py` | `session_view` — verbatim line-range reader |
| `co_cli/bootstrap/core.py:restore_session` | bootstrap-side most-recent-session restore |

## 7. Test Gates

| Property | Test file |
| --- | --- |
| Session restore picks the most recent transcript | `tests/test_flow_session_persistence.py` |
| Lexical session recall surfaces uuid8 + line citations; structural-key matches dropped | `tests/test_flow_session_search.py` |
| Compaction rewrites session in place; search reflects rewritten content | `tests/test_flow_compaction_session_rewrite.py` |
| `session_view` targeted glob locates correct file by UUID suffix | `tests/test_flow_session_view.py` |
