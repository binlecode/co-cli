# Co CLI — Sessions

> Peer tier: [memory.md](memory.md) (long-term declarative artifacts). Compaction (in-place transcript rewrite): [compaction.md](compaction.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md).

Foundation spec for the session tier — append-only transcripts of past conversations, chunked at write time and recalled via FTS5 BM25 with line citations.

Session is one of five operational tiers in the agent loop: **doctrine** ([personality.md](personality.md), identity), **tools** ([tools.md](tools.md), capability), **skills** ([skills.md](skills.md), procedure), **memory** ([memory.md](memory.md) — long-term declarative artifacts), and **session** (this file — past conversation transcripts). Session and memory are peer tiers sharing the same index infrastructure but with distinct domain logic, mutation models, and lifecycle policies.

## 1. Functional Architecture

Sessions hold append-only JSONL transcripts of past conversations. Each session is uniquely identified by an 8-char UUID suffix embedded in its filename. Mutation is append-only via `persist_session_history()`; compaction rewrites the file in place but preserves the session UUID.

There is no LLM call on the recall path — session_search returns BM25 chunk-cited hits directly. Compaction-driven rewrites are the only mutation surface beyond append.

### Storage

- Path: `~/.co-cli/sessions/*.jsonl` (the `sessions_dir` workspace path on `CoDeps`).
- Format: one message per JSONL line; pydantic-ai history is the source of truth.
- Filename: `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl`. The 8-char UUID suffix is the canonical session identifier.
- Mutation: append-only via `persist_session_history()`; rewritten in place on compaction (see [compaction.md](compaction.md)).

## 2. Architecture layers

```
co_cli/tools/session/    Agent surface — session_search, session_view
        ↓
co_cli/session/          Domain — SessionStore (uuid8 doc_path, append-only sync, current-session exclusion)
        ↓
co_cli/index/            Shared infrastructure facade (IndexStore) — see memory.md §2
        ↓
                         SQLite + FTS5 + optional sqlite-vec
```

The session domain owns transcript extraction, role-prefixed flattening, JSONL line tracking, and append-only sync policy. The chunker emits canonical `Chunk` records directly into the shared index — no session-specific chunk type at the storage boundary.

## 3. Core Logic

### Chunking and indexing

Sessions are chunked at write time and stored under `source='session'` in the shared FTS index.

| Property | Value |
| --- | --- |
| Source value | `'session'` |
| Sync entry point | `SessionStore.index_session(session_path)` / `SessionStore.sync(sessions_dir, exclude=current)` |
| Chunk pipeline | `extract_messages()` → `flatten_session()` (role-prefixed lines) → `chunk_flattened()` (sliding-window token chunks) |
| Chunk size | `memory.session_chunk_tokens` (default 400) |
| Chunk overlap | `memory.session_chunk_overlap` (default 80) |
| `doc_path` value | the 8-char UUID suffix (not a filesystem path) — stable across renames |
| Hash skip | SHA256 over flattened text; unchanged sessions are not re-chunked |
| Stale removal | `IndexStore.remove_stale('session', current_uuid8s)` — no directory filter; `doc_path` is a UUID |

### Bootstrap sync

`init_session_index(deps, current_session_path)` runs after `create_deps()`. It calls `session_store.sync(sessions_dir, exclude=current_session_path)` so the in-progress transcript is never indexed mid-session.

### Compaction interplay

Compaction rewrites the live session JSONL in place when context pressure crosses the spill threshold (see [compaction.md](compaction.md)). After rewrite:

- The session UUID stays the same — filename does not change.
- `index_session()` re-chunks the file on the next sync (content hash will have changed).
- Older sessions are unaffected; compaction is per-session.

The shared FTS index is rebuildable from the rewritten transcript on demand.

### Recall pipeline

`_search_sessions(ctx, query, span)` routes through:

```
_search_sessions(query) → SessionStore.search(query, limit=15)
    → dedup to _SESSIONS_CHANNEL_CAP = 3 unique sessions
    → exclude the current session by UUID
```

Browse mode (`session_search(query='')`) skips the FTS path and returns recent-session metadata (id, date, title, file size) via `_browse_recent()`. The current session is excluded in both modes.

## 4. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `memory.session_chunk_tokens` | `CO_MEMORY_SESSION_CHUNK_TOKENS` | `400` | session chunk size in tokens |
| `memory.session_chunk_overlap` | `CO_MEMORY_SESSION_CHUNK_OVERLAP` | `80` | session chunk overlap in tokens |

Session-wide retrieval settings (backend selection, reranker URL, embedding provider) live in [memory.md §3](memory.md) since both tiers share the index infrastructure.

## 5. Public Interface

### Model-callable tools

| Symbol | Source | Contract |
| --- | --- | --- |
| `session_search(ctx, query, limit=3)` | `co_cli/tools/session/recall.py` | Async tool — BM25-ranked chunk recall over past sessions; current session excluded; empty query → recent-session browse |
| `session_view(ctx, session_id, start_line, end_line)` | `co_cli/tools/session/view.py` | Async tool — verbatim JSONL line-range reader by uuid8; `tool_error` for unknown id |

Result shape for `session_search`:

```python
{
    "session_id": <uuid8>,
    "when": <ISO date prefix>,
    "source": "session",
    "chunk_text": <FTS5 snippet>,
    "start_line": <JSONL line>,
    "end_line": <JSONL line>,
    "score": <BM25>,
}
```

### Domain API

| Symbol | Source | Contract |
| --- | --- | --- |
| `SessionStore(index, config)` | `co_cli/session/store.py` | Domain store composing IndexStore — owns session indexing policy |
| `SessionStore.index_session(session_path)` | `co_cli/session/store.py` | Idempotent — content-hash skip; doc_path = uuid8 |
| `SessionStore.sync(sessions_dir, exclude=None)` | `co_cli/session/store.py` | Hash-based sync of all transcripts; excludes the live session |
| `SessionStore.search(query, limit)` | `co_cli/session/store.py` | Ranked recall over indexed sessions |
| `SessionStore.count()` | `co_cli/session/store.py` | Number of indexed sessions |

### Persistence and chunking helpers

| Symbol | Source | Contract |
| --- | --- | --- |
| `persist_session_history(...)` | `co_cli/session/persistence.py` | Append-only writer; on compaction rewrites the file in place |
| `append_messages(path, messages)` | `co_cli/session/persistence.py` | Tail-append used by `_finalize_turn` |
| `load_transcript(path)` | `co_cli/session/persistence.py` | Reads a JSONL transcript back into pydantic-ai messages |
| `session_filename(created_at, session_id)` | `co_cli/session/filename.py` | Builds `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl` |
| `parse_session_filename(name)` | `co_cli/session/filename.py` | Parses session filename into `(uuid8, timestamp)` |
| `find_latest_session(sessions_dir)` | `co_cli/session/filename.py` | Returns most recent transcript path by filename order |
| `new_session_path(sessions_dir)` | `co_cli/session/filename.py` | Mints a fresh transcript path; does not create the file |
| `flatten_session(messages)` | `co_cli/session/chunker.py` | Converts message history into role-prefixed text |
| `chunk_flattened(...)` | `co_cli/session/chunker.py` | Sliding-window token chunker returning `Chunk` records |
| `chunk_session(session_path, ...)` | `co_cli/session/chunker.py` | One-shot file → chunks pipeline |
| `extract_messages(path)` | `co_cli/session/transcript.py` | JSONL line parser |

### Bootstrap-side entry points

| Symbol | Source | Contract |
| --- | --- | --- |
| `restore_session(deps, frontend) -> Path` | `co_cli/bootstrap/core.py` | Picks the most recent transcript; sets `deps.session.session_path` |
| `init_session_index(deps, current_session_path, frontend)` | `co_cli/bootstrap/core.py` | Syncs all past transcripts; excludes the live session |

### Session browser (used by `/sessions` and `/resume`)

| Symbol | Source | Contract |
| --- | --- | --- |
| `SessionSummary` | `co_cli/session/browser.py` | Dataclass — `session_id`, `created_at`, `title`, `file_size`, `path` |
| `list_sessions(sessions_dir)` | `co_cli/session/browser.py` | Returns picker metadata for past transcripts |
| `format_file_size(size)` | `co_cli/session/browser.py` | Pretty file-size formatter used by the picker |

## 6. Files

| File | Purpose |
| --- | --- |
| `co_cli/session/store.py` | `SessionStore` — domain store over IndexStore |
| `co_cli/session/filename.py` | filename parsing/generation, latest-session discovery |
| `co_cli/session/persistence.py` | transcript append/load, in-place rewrite on compaction |
| `co_cli/session/browser.py` | session listing and picker metadata |
| `co_cli/session/chunker.py` | role-prefixed flattening + sliding-window chunking; returns `Chunk` |
| `co_cli/session/transcript.py` | JSONL line parser: `ExtractedMessage`, `extract_messages()` |
| `co_cli/tools/session/recall.py` | `session_search` — recall and browse modes |
| `co_cli/tools/session/view.py` | `session_view` — verbatim line-range reader |
| `co_cli/bootstrap/core.py:init_session_index,restore_session` | bootstrap-side session sync and most-recent-session restore |

## 7. Test Gates

| Property | Test file |
| --- | --- |
| Session restore picks the most recent transcript | `tests/test_flow_session_persistence.py` |
| Session chunk indexing and recall surface line bounds | `tests/test_flow_session_search.py` |
| Compaction rewrites session in place; re-chunked on next sync | `tests/test_flow_compaction_session_rewrite.py` |
| `session_view` targeted glob locates correct file by UUID suffix | `tests/test_flow_session_view.py` |
