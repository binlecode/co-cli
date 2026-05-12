# Co CLI — Memory: Sessions Channel

> Foundation: [memory.md](memory.md). Compaction (in-place transcript rewrite): [compaction.md](compaction.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md).

This doc owns the sessions channel — append-only transcripts of past conversations, chunked at write time and recalled via FTS5 BM25 with line citations. There is no LLM call on the recall path; "summarized sessions" is a deferred enhancement, not the current behavior.

## 1. Storage

- Path: `~/.co-cli/sessions/*.jsonl` (the `sessions_dir` workspace path on `CoDeps`).
- Format: one message per JSONL line; pydantic-ai history is the source of truth.
- Filename: `YYYY-MM-DD-THHMMSSZ-<uuid8>.jsonl`. The 8-char UUID suffix is the canonical session identifier.
- Mutation: append-only via `persist_session_history()`; rewritten in place on compaction (see [compaction.md](compaction.md)).

## 2. Chunking and Indexing

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

## 3. Recall

`memory_search` with a non-empty query routes through `_search_sessions(ctx, query, span)`:

```
_search_sessions(query) → MemoryStore.search(query, sources=['session'], limit=15)
    → dedup to _SESSIONS_CHANNEL_CAP = 3 unique sessions
    → exclude the current session by UUID
```

Returns no session hits when `memory_store` is `None`. **No LLM call** on the recall path — hits are BM25-ranked chunk snippets with line citations.

Result shape:

```python
{
    "channel": "sessions",
    "session_id": <uuid8>,
    "when": <ISO date prefix>,
    "source": "session",
    "chunk_text": <FTS5 snippet>,
    "start_line": <JSONL line>,
    "end_line": <JSONL line>,
    "score": <BM25>,
}
```

Browse mode (`memory_search(query='')`) skips the FTS path and returns recent-session metadata (id, date, title, file size) via `_browse_recent()`. The current session is excluded in both modes.

## 4. Verbatim Reader

`memory_read_session_turn(session_id, start_line, end_line)` lives at `co_cli/tools/memory/read.py`. Given a `session_id` (uuid8) and a JSONL line range, it returns the verbatim lines from disk.

The tool is **not currently registered** in the foreground native toolset. Rationale: `memory_search` already surfaces session chunks with line bounds; a follow-up `memory_read_session_turn` call would expand one chunk to verbatim JSONL — useful for citations but expensive if called speculatively. Registering it is a one-line addition to `_native_toolset.py`; intentionally withheld until there is evidence that chunk snippets alone are insufficient.

The source-only state is deliberate, not an accident — see the foundation spec's note on channel-specific readers ([memory.md §5](memory.md)).

## 5. Compaction Interplay

Compaction rewrites the live session JSONL in place when context pressure crosses the spill threshold (see [compaction.md](compaction.md)). After rewrite:

- The session UUID stays the same — filename does not change.
- `index_session()` re-chunks the file on the next sync (content hash will have changed).
- Older sessions are unaffected; compaction is per-session.

The chunks pipeline is the only persistence layer that needs to react to compaction — the FTS index is rebuildable from the rewritten transcript on demand.

## 6. Files

| File | Purpose |
| --- | --- |
| `co_cli/memory/session.py` | session filename parsing/generation, latest-session discovery, `parse_session_filename` |
| `co_cli/memory/transcript.py` | transcript append/load, control records |
| `co_cli/memory/session_browser.py` | session listing and picker metadata for `/resume` and `/sessions` |
| `co_cli/memory/session_chunker.py` | chunking pipeline: `flatten_session()`, `chunk_flattened()`, `chunk_session()` |
| `co_cli/memory/indexer.py` | JSONL line parser: `ExtractedMessage`, `extract_messages()` |
| `co_cli/memory/memory_store.py:index_session,sync_sessions` | write-time indexing into `chunks_fts` under `source='session'` |
| `co_cli/tools/memory/recall.py:_search_sessions,_browse_recent` | recall and browse modes for the sessions channel |
| `co_cli/tools/memory/read.py:memory_read_session_turn` | verbatim line-range reader (source-only; not registered) |
| `co_cli/bootstrap/core.py:init_session_index,restore_session` | bootstrap-side session sync and most-recent-session restore |

## 7. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.session_chunk_tokens` | `CO_KNOWLEDGE_SESSION_CHUNK_TOKENS` | `400` | session chunk size in tokens |
| `knowledge.session_chunk_overlap` | `CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP` | `80` | session chunk overlap in tokens |

Session-wide retrieval settings (backend selection, reranker URL, embedding provider) live in [memory.md §6](memory.md).

## 8. Test Gates

| Property | Test file |
| --- | --- |
| Session restore picks the most recent transcript | `tests/test_flow_session_persistence.py` |
| Session chunk indexing and recall surface line bounds | `tests/test_flow_memory_recall.py` |
| Compaction rewrites session in place; re-chunked on next sync | `tests/test_flow_compaction_session_rewrite.py` |
| `memory_read_session_turn` targeted glob locates correct file by UUID suffix | `tests/test_flow_memory_recall.py` |
