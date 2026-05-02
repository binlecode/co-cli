# Co CLI — Session Memory

> Knowledge artifacts: [memory-knowledge.md](memory-knowledge.md). Canon recall: [memory-canon.md](memory-canon.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md). Compaction mechanics: [compaction.md](compaction.md). Dream-cycle mining, merge, decay, archive: [dream.md](dream.md).

## 1. Session Transcripts

Session transcripts are append-only JSONL files under `sessions_dir` with lexicographically sortable filenames:

```text
YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl
```

Each JSONL line is one of:

- a message row serialized through `ModelMessagesTypeAdapter`
- a `session_meta` control row written at the start of a branched child transcript
- a `compact_boundary` control row, honored on load for files above the precompact threshold

`persist_session_history()` is the only transcript persistence primitive:

```text
if history was replaced OR persisted_message_count > len(messages):
    new_path = new_session_path(sessions_dir)
    write session_meta(parent_session=<old filename>, reason=<reason>)
    append full compacted history to new_path
    return new_path
else:
    append only messages[persisted_message_count:]
    return existing session_path
```

Behavioral rules:

- Individual transcript files are never rewritten or truncated.
- History replacement never mutates the parent transcript; it branches to a child transcript.
- `CoSessionState.persisted_message_count` is the only durability cursor.
- `load_transcript()` skips malformed lines and `session_meta` rows, honors `compact_boundary` skips for files above 5 MB, and refuses to load transcripts above 50 MB.

## 2. Session Lifecycle, Commands, and Spill Files

Startup restore is path-only. `restore_session()` picks the latest `*.jsonl` by filename and sets `deps.session.session_path`; `_chat_loop()` begins with empty in-memory `message_history`. Resuming history is explicit.

Session command behavior:

| Command | Behavior |
| --- | --- |
| `/resume` | `list_sessions()` + interactive picker → `load_transcript(selected.path)`; adopts history and updates `session_path` |
| `/new` | If history is empty, prints "Nothing to rotate"; else assigns a fresh `session_path` and clears in-memory history |
| `/clear` | Clears in-memory history; transcript files untouched |
| `/compact` | Replaces in-memory history with compacted transcript; next write branches to a child session |
| `/sessions [keyword]` | Lists session summaries, optionally filtered by title substring |

Oversized tool results spill to disk. `tool_output()` checks the effective threshold (`ToolInfo.max_result_size` or `config.tools.result_persist_chars`). When exceeded, `persist_if_oversized()` writes to:

```text
tool-results/{sha256[:16]}.txt
```

The model sees a `<persisted-output>` placeholder with tool name, file path, total size, a 2,000-char preview, and guidance to page the full file. Spill files are content-addressed and idempotent. Session files and spill files are `chmod 0o600`.

## 3. Sessions Recall Channel

Sessions are indexed as `source='session'` chunks by `MemoryStore`:

```text
index_session(path):
    parse uuid8 and created_at from filename
    chunk_session(path) → list[SessionChunk]
    content_hash = sha256(joined chunk texts)
    if hash unchanged AND chunk_count > 0: return  # hash-skip
    index doc row (source='session', path=uuid8, kind='session')
    index_chunks(source='session', doc_path=uuid8, chunks)

sync_sessions(sessions_dir, exclude=current_session):
    for each *.jsonl except excluded:
        index_session(path)
    remove_stale('session', current_uuid8s)
```

`session_chunker.py` pipeline:

- `extract_messages(path)` → `list[ExtractedMessage]` — parses JSONL, skips control lines and noise parts
- `flatten_session(messages)` → `(flat_lines, line_map)` — role-prefixed lines: `User:`, `Assistant:`, `Tool[name](call)`, `Tool[name](return):`
- `chunk_flattened(flat_lines, line_map)` → `list[SessionChunk]` — sliding-window token chunks, each tracking `start_jsonl_line` / `end_jsonl_line`

`init_session_index()` runs at bootstrap. On first run after migration it removes the obsolete `session-index.db` if present.

`memory_search()` operates in two modes:

**Browse mode** (empty query): returns recent-session metadata — session ID, date, title, file size — with zero LLM cost. Excludes the current session.

**Search mode** (keyword query): dispatches sessions, artifacts, and canon channels in parallel.
- Sessions: `MemoryStore.search(sources=['session'], limit=15)` → dedup to one best chunk per unique session → cap at 3 (`_SESSIONS_CHANNEL_CAP`)
- To drill into a specific turn: `memory_read_session_turn(session_id, start_line, end_line)` — verbatim JSONL lines, capped at 200 lines / 16 KB

Result shape: `{channel: "sessions", session_id, when, source, chunk_text, start_line, end_line, score}`

The active session is excluded from the bootstrap sync. Episodic search covers already-indexed transcripts, not the live in-progress session.

## 4. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | age-decay parameter used in recall scoring |
| `knowledge.session_chunk_tokens` | `CO_KNOWLEDGE_SESSION_CHUNK_TOKENS` | `400` | session chunk size in tokens |
| `knowledge.session_chunk_overlap` | `CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP` | `80` | session chunk overlap in tokens |

### Paths

| Path | Env Var | Default | Description |
| --- | --- | --- | --- |
| `sessions_dir` | — | `~/.co-cli/sessions/` | user-global transcript directory |
| `tool_results_dir` | — | `~/.co-cli/tool-results/` | spill directory for oversized tool results |
| `memory_db_path` | — | `~/.co-cli/co-cli-search.db` | unified retrieval DB (shared with knowledge artifacts) |

## 5. Files

| File | Purpose |
| --- | --- |
| `co_cli/memory/session.py` | session filename parsing, generation, and latest-session discovery |
| `co_cli/memory/transcript.py` | transcript append/load, child-session branching, and control records |
| `co_cli/memory/session_browser.py` | session listing and picker metadata for `/resume` and `/sessions` |
| `co_cli/memory/session_chunker.py` | session transcript chunking pipeline: `flatten_session()`, `chunk_flattened()`, `chunk_session()` |
| `co_cli/memory/indexer.py` | JSONL line parser: `ExtractedMessage`, `extract_messages()` |
| `co_cli/tools/memory/read.py` | `memory_read_session_turn()` — verbatim JSONL turn reader |
| `co_cli/tools/tool_io.py` | oversized tool-result spill, preview placeholders, and size warnings |
| `co_cli/bootstrap/core.py` | `restore_session()`, `init_session_index()` — startup session and index bootstrap |
| `co_cli/main.py` | `_finalize_turn()` — session persistence bridge and session-end dream trigger |
| `co_cli/commands/core.py` | `/resume`, `/new`, `/clear`, `/compact`, `/sessions` command handlers |

## 6. Test Gates

| Property | Test file |
| --- | --- |
| Session restore picks the most recent transcript | `tests/test_flow_bootstrap_session.py` |
