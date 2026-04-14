# Plan: Session History FTS Search

**Task type: code-feature**

---

## Context

co-cli stores every conversation as a JSONL file under `.co-cli/sessions/` (format:
`YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl`). The agent currently has no way to recall what was
discussed in past sessions. The existing "memory" module extracts distilled facts — that
is a separate, independent concern.

**Build-order dependency:** This plan must be implemented **after**
`unified-agent-builder` (`2026-04-13-150000`) is fully shipped. It targets the
post-builder architecture: tool registration goes into `co_cli/agent/_native_toolset.py`
(not the deleted `co_cli/agent.py`), and deps propagation uses `make_agent_deps`
(not the renamed `make_subagent_deps`).

**Current state validation (post-agent-builder):**
- No `session_index` module exists in `co_cli/`.
- No `session_search` tool in `co_cli/tools/`.
- `CoDeps` has no `session_index` handle.
- `co_cli/config/_core.py` defines `SEARCH_DB` and `LOGS_DB` but no session index path.
- The session-file-rename plan (`2026-04-12-085311`) is fully delivered — sessions use
  the `YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl` format; `CoSessionState.session_path: Path`
  is the canonical session identifier.
- `co_cli/agent/` package exists; tool registration is in `_native_toolset.py`.
- `make_agent_deps` in `co_cli/deps.py` is the subagent deps factory (renamed from
  `make_subagent_deps` by unified-agent-builder).

---

## Problem & Outcome

**Problem:** The agent cannot recall past conversations — it has no tool to search session
transcripts for prior context, decisions, or discussed topics.

**Failure cost:** Users must re-explain context already covered in earlier sessions. The
agent cannot independently answer "what did we decide about X last week?" without human
re-narration.

**Outcome:** A `session_search` tool is available in the agent toolset. The agent can
search `.co-cli/sessions/` by keyword, receive ranked excerpts from matching sessions, and
surface relevant past context to the user.

---

## Scope

**In:**
- FTS5 keyword search over session message content (user + assistant turns only)
- Incremental sync: detect new/changed sessions at startup and index them
- `session_search` tool returning ranked session excerpts with metadata
- Project-local index DB (`.co-cli/session-index.db`)

**Out:**
- Embedding / semantic search (future)
- LLM-based summarization of results (future — return raw excerpts for now)
- Cross-project session search
- Tool-call / tool-return content indexing (noise; user+assistant turns are sufficient)
- Session deletion from index (future; index is rebuildable)

---

## Behavioral Constraints

- Index is derived and rebuildable: deleting `.co-cli/session-index.db` and restarting
  rebuilds cleanly from `.co-cli/sessions/*.jsonl`.
- The currently-active session is excluded from sync. Its path is resolved only after
  `restore_session` runs; `_init_session_index` is therefore called in `main.py`
  after `restore_session`, passing the resolved `session_path` as the exclusion target.
- Change detection uses file size: transcripts are append-only, so a size increase means
  new messages. Re-index the full session on size change.
- If `sessions_dir` does not exist or is empty, `sync_sessions` is a no-op and the
  tool remains available but returns an empty result set.
- If the session index is unavailable (DB error, disk full), startup degrades gracefully:
  log a warning, set `deps.session_index = None`, and continue — the tool returns a
  graceful empty-result message rather than crashing.
- The `session_search` tool is DEFERRED visibility (discovered via `search_tools`, not
  injected every turn).
- `SessionIndex.__init__` executes `PRAGMA journal_mode=WAL` to handle concurrent
  readers across multiple sessions opening the same project.

---

## JSONL Message Format

Each line in a session file is `ModelMessagesTypeAdapter.dump_json([msg])`, where `msg`
is either a `ModelRequest` or `ModelResponse`:

```
ModelRequest  (kind="request"):
  parts with part_kind in: "user-prompt", "tool-return", "system-prompt", "retry-prompt"

ModelResponse (kind="response"):
  parts with part_kind in: "text", "tool-call", "thinking"

Special marker (not a ModelMessage): {"type":"compact_boundary"}
```

**Index only:** `part_kind="user-prompt"` (role=user) and `part_kind="text"` (role=assistant).
Timestamp: from part's `timestamp` field if present, else from the enclosing message's
`timestamp` field.

---

## High-Level Design

```
.co-cli/sessions/
  2026-04-10-T120000Z-abc12345.jsonl
  2026-04-12-T090000Z-def67890.jsonl

Startup (after restore_session):
  _init_session_index(deps, current_session_path)
    → SessionIndex(db_path=deps.sessions_dir.parent / "session-index.db")
    → store.sync_sessions(sessions_dir, exclude=current_session_path)
    → deps.session_index = store

Agent turn (on demand):
  session_search(ctx, query, limit=3)
    → ctx.deps.session_index.search(query, limit)
    → list[SessionSearchResult]
    → tool_output(results)
```

**Database schema** (`.co-cli/session-index.db`):

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,   -- uuid8 from filename
    session_path TEXT NOT NULL,      -- absolute path string
    created_at   TEXT NOT NULL,      -- ISO timestamp parsed from filename
    file_size    INTEGER NOT NULL,   -- for change detection
    indexed_at   TEXT NOT NULL       -- ISO timestamp of last index run
);

CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    line_index   INTEGER NOT NULL,   -- 0-based line position in JSONL
    part_index   INTEGER NOT NULL,   -- 0-based within extracted parts of this line
    role         TEXT NOT NULL,      -- 'user' | 'assistant'
    content      TEXT NOT NULL,
    timestamp    TEXT,               -- ISO string or NULL
    UNIQUE(session_id, line_index, part_index)
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    tokenize='porter unicode61',
    content=messages,
    content_rowid=id
);
-- auto-maintain triggers: messages_ai, messages_ad, messages_au
```

Re-indexing a session: DELETE FROM messages WHERE session_id=? then bulk INSERT.

**Search result type:**

```python
@dataclass
class SessionSearchResult:
    session_id: str
    session_path: str
    created_at: str
    role: str
    snippet: str    # FTS5 snippet() output
    score: float    # 1.0 / (1.0 + abs(bm25_rank))
```

`search()` deduplicates to one result per session (highest-scoring message per session),
returns top-N sessions sorted by score descending.

---

## Implementation Plan

### TASK-1 — Session index module

**files:**
- `co_cli/session_index/__init__.py` (docstring only)
- `co_cli/session_index/_extractor.py`
- `co_cli/session_index/_store.py`
- `tests/test_session_index.py`

**done_when:** `uv run pytest tests/test_session_index.py -x` passes, covering:
1. `extract_messages()` returns only user+assistant parts; skips tool-call, tool-return,
   system-prompt, thinking, and compact-boundary lines
2. `SessionIndex.index_session()` populates sessions + messages tables; FTS row count
   matches indexed message count
3. `SessionIndex.search("query")` returns `SessionSearchResult` list with correct
   session_id and non-empty snippet
4. `sync_sessions()` skips sessions whose file_size is unchanged; re-indexes on size
   increase
5. `sync_sessions(exclude=path)` omits the excluded path

**success_signal:** N/A (internal module)

**prerequisites:** none

---

### TASK-2 — Bootstrap wiring

**files:**
- `co_cli/deps.py` — add `session_index: SessionIndex | None = None` field; add
  `session_index=base.session_index` in `make_agent_deps` return
- `co_cli/bootstrap/core.py` — add `_init_session_index(deps, current_session_path, frontend)`
- `co_cli/main.py` — call `_init_session_index` after `restore_session` returns
- `tests/test_bootstrap.py` — add test for session index initialization

**done_when:** `uv run pytest tests/test_bootstrap.py -x` passes including a new test that:
- writes one JSONL session file (with real `ModelRequest`/`ModelResponse` via `append_messages`)
  into a temp sessions dir
- calls `_init_session_index(deps, current_session_path=Path("nonexistent"), frontend)`
  directly
- asserts `isinstance(deps.session_index, SessionIndex)` (not just `is not None`)
- asserts `deps.session_index.search("keyword from JSONL content")` returns one result

**success_signal:** N/A (internal wiring)

**prerequisites:** [TASK-1]

---

### TASK-3 — `session_search` tool + agent registration

**files:**
- `co_cli/tools/session_search.py`
- `co_cli/agent/_native_toolset.py` — import + `_register_tool(..., visibility=_deferred_visible)`

**done_when:** `uv run pytest tests/test_session_search_tool.py -x` passes, covering:
1. Tool returns `ToolReturn` with session results when `session_index` has indexed data
2. Tool returns graceful message when `deps.session_index is None`
3. Tool is registered with DEFERRED visibility:
   `assert "session_search" in deps.tool_index`
   `assert deps.tool_index["session_search"].visibility == VisibilityPolicyEnum.DEFERRED`

**success_signal:** Agent responds to "search my sessions for pytest" with a snippet
from a past session transcript.

**prerequisites:** [TASK-1, TASK-2]

---

## Testing

All tests use real SQLite, real JSONL files written via `append_messages()` from
`co_cli/context/transcript.py`, and real `tmp_path` dirs. No mocks. Session JSONL
fixtures created programmatically using `pydantic_ai.messages` constructors.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev session-history-search`
