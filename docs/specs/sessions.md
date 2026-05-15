# Co CLI — Memory: Sessions Channel

> Foundation: [memory.md](memory.md). Compaction (in-place transcript rewrite): [compaction.md](compaction.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md).

This doc owns the sessions channel — append-only transcripts of past conversations, chunked at write time and recalled via FTS5 BM25 with line citations. There is no LLM call on the recall path; "summarized sessions" is a deferred enhancement, not the current behavior.

## 1. Functional Architecture

### Storage

- Path: `~/.co-cli/sessions/*.jsonl` (the `sessions_dir` workspace path on `CoDeps`).
- Format: one message per JSONL line; pydantic-ai history is the source of truth.
- Filename: `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl`. The 8-char UUID suffix is the canonical session identifier.
- Mutation: append-only via `persist_session_history()`; rewritten in place on compaction (see [compaction.md](compaction.md)).

## 2. Core Logic

### Chunking and Indexing

Sessions are chunked at write time and stored under `source='session'` in the unified `chunks_fts` index.

| Property | Value |
| --- | --- |
| Source value | `'session'` |
| Sync entry point | `MemoryStore.index_session(session_path)` / `MemoryStore.sync_sessions(sessions_dir, exclude=current)` |
| Chunk pipeline | `flatten_session()` → role-prefixed lines → `chunk_flattened()` sliding-window token chunks |
| Chunk size | `knowledge.session_chunk_tokens` (default 400) |
| Chunk overlap | `knowledge.session_chunk_overlap` (default 80) |
| `doc_path` value | the 8-char UUID suffix (not a filesystem path) — keeps the key stable across renames |
| Hash skip | SHA256 over flattened text; unchanged sessions are not re-chunked |
| Stale removal | `remove_stale('session', current_uuid8s)` — no directory filter because `doc_path` is a UUID, not a path |

### Bootstrap sync

`init_session_index(deps, current_session_path)` runs after `create_deps()`. It calls `sync_sessions(sessions_dir, exclude=current_session_path)` so the in-progress transcript is never indexed mid-session. On first run after migration it deletes the obsolete `session-index.db` (the chunks pipeline supersedes it).

### Compaction Interplay

Compaction rewrites the live session JSONL in place when context pressure crosses the spill threshold (see [compaction.md](compaction.md)). After rewrite:

- The session UUID stays the same — filename does not change.
- `index_session()` re-chunks the file on the next sync (content hash will have changed).
- Older sessions are unaffected; compaction is per-session.

The chunks pipeline is the only persistence layer that needs to react to compaction — the FTS index is rebuildable from the rewritten transcript on demand.

### Recall pipeline

`_search_sessions(ctx, query, span)` routes through:

```
_search_sessions(query) → MemoryStore.search(query, sources=['session'], limit=15)
    → dedup to _SESSIONS_CHANNEL_CAP = 3 unique sessions
    → exclude the current session by UUID
```

Browse mode (`session_search(query='')`) skips the FTS path and returns recent-session metadata (id, date, title, file size) via `_browse_recent()`. The current session is excluded in both modes.

## 3. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.session_chunk_tokens` | `CO_KNOWLEDGE_SESSION_CHUNK_TOKENS` | `400` | session chunk size in tokens |
| `knowledge.session_chunk_overlap` | `CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP` | `80` | session chunk overlap in tokens |

Session-wide retrieval settings (backend selection, reranker URL, embedding provider) live in [memory.md §3](memory.md).

## 4. Public Interface

### Model-callable tools

| Symbol | Source | Contract |
| --- | --- | --- |
| `session_search(ctx, query, limit=3)` | `co_cli/tools/memory/recall.py` | Async tool — BM25-ranked chunk recall over past sessions; current session excluded; empty query → recent-session browse |
| `session_view(ctx, session_id, start_line, end_line)` | `co_cli/tools/memory/view.py` | Async tool — verbatim JSONL line-range reader by uuid8; `tool_error` for unknown id |

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

### Persistence and chunking helpers

| Symbol | Source | Contract |
| --- | --- | --- |
| `persist_session_history(messages, session_path, history_compacted=False)` | `co_cli/memory/transcript.py` | Append-only writer; on `history_compacted=True` rewrites the file in place |
| `append_messages(path, messages) -> None` | `co_cli/memory/transcript.py` | Positional tail-append used by `_finalize_turn` |
| `load_transcript(path) -> list[ModelMessage]` | `co_cli/memory/transcript.py` | Reads a JSONL transcript back into pydantic-ai messages |
| `session_filename(created_at, session_id) -> str` | `co_cli/memory/session.py` | Builds `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl` |
| `parse_session_filename(name) -> tuple[str, datetime] \| None` | `co_cli/memory/session.py` | Parses a session filename into `(uuid8, timestamp)`; returns `None` on malformed input |
| `find_latest_session(sessions_dir) -> Path \| None` | `co_cli/memory/session.py` | Returns the most-recent transcript path by filename order |
| `new_session_path(sessions_dir) -> Path` | `co_cli/memory/session.py` | Mints a fresh transcript path; does not create the file |
| `flatten_session(messages) -> tuple[str, list[tuple[int, int]]]` | `co_cli/memory/session_chunker.py` | Converts message history into role-prefixed text plus per-line bounds |
| `chunk_flattened(text, line_bounds, chunk_tokens, overlap_tokens) -> list[Chunk]` | `co_cli/memory/session_chunker.py` | Sliding-window token chunker with line bounds |
| `chunk_session(session_path, chunk_tokens, overlap_tokens) -> list[Chunk]` | `co_cli/memory/session_chunker.py` | One-shot file → chunks pipeline |
| `ExtractedMessage`, `extract_messages(path) -> Iterator[ExtractedMessage]` | `co_cli/memory/indexer.py` | JSONL line parser used by chunker and recall |

### Bootstrap-side entry points

| Symbol | Source | Contract |
| --- | --- | --- |
| `restore_session(deps, frontend) -> Path` | `co_cli/bootstrap/core.py` | Picks the most recent transcript; sets `deps.session.session_path` |
| `init_session_index(deps, current_session_path, frontend) -> None` | `co_cli/bootstrap/core.py` | Syncs all past transcripts into the FTS index; excludes the live session |

### Session browser (used by `/sessions` and `/resume`)

| Symbol | Source | Contract |
| --- | --- | --- |
| `SessionSummary` | `co_cli/memory/session_browser.py` | Dataclass — `session_id`, `created_at`, `title`, `file_size`, `path` |
| `list_sessions(sessions_dir) -> list[SessionSummary]` | `co_cli/memory/session_browser.py` | Returns picker metadata for past transcripts |
| `format_file_size(size) -> str` | `co_cli/memory/session_browser.py` | Pretty file-size formatter used by the picker |

## 5. Files

| File | Purpose |
| --- | --- |
| `co_cli/memory/session.py` | session filename parsing/generation, latest-session discovery, `parse_session_filename` |
| `co_cli/memory/transcript.py` | transcript append/load, control records |
| `co_cli/memory/session_browser.py` | session listing and picker metadata for `/resume` and `/sessions` |
| `co_cli/memory/session_chunker.py` | chunking pipeline: `flatten_session()`, `chunk_flattened()`, `chunk_session()` |
| `co_cli/memory/indexer.py` | JSONL line parser: `ExtractedMessage`, `extract_messages()` |
| `co_cli/memory/memory_store.py:index_session,sync_sessions` | write-time indexing into `chunks_fts` under `source='session'` |
| `co_cli/tools/memory/recall.py:_search_sessions,_browse_recent` | `session_search` — recall and browse modes for the sessions channel |
| `co_cli/tools/memory/view.py:session_view` | verbatim line-range reader |
| `co_cli/bootstrap/core.py:init_session_index,restore_session` | bootstrap-side session sync and most-recent-session restore |

## 6. Test Gates

| Property | Test file |
| --- | --- |
| Session restore picks the most recent transcript | `tests/test_flow_session_persistence.py` |
| Session chunk indexing and recall surface line bounds | `tests/test_flow_session_search.py` |
| Compaction rewrites session in place; re-chunked on next sync | `tests/test_flow_compaction_session_rewrite.py` |
| `session_view` targeted glob locates correct file by UUID suffix | `tests/test_flow_session_view.py` |
