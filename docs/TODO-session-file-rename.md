# TODO: Session File Rename

**Slug:** `session-file-rename`
**Task type:** `code-refactor`
**Post-ship:** `/sync-doc`

---

## Context

Each co-cli session currently produces two files in `.co-cli/sessions/`:

| File | Purpose |
|------|---------|
| `{uuid}.json` | Metadata: `session_id`, `created_at`, `last_used_at`, `compaction_count` |
| `{uuid}.jsonl` | Messages: append-only JSONL transcript |

The `.json` file is redundant:
- `session_id` — already the filename stem
- `created_at` — can be embedded in the filename as a sortable timestamp prefix
- `last_used_at` — filesystem `mtime` is the authoritative source; `find_latest_session` already sorts by `mtime`
- `compaction_count` — derivable from the JSONL by counting `{"type":"compact_boundary"}` lines

Proposed single-file naming format:

```
YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl
```

Example:

```
2026-04-11-T142305Z-550e8400.jsonl
```

- Date + time prefix: lexicographically sortable, human-readable in `ls`, no special characters
- 8-char UUID suffix: collision requires same-second start AND matching first 8 hex chars of UUID4 (1-in-4-billion per second for a single-user CLI — practically impossible)
- Single file per session: no metadata sidecar

---

## Problem & Outcome

**Problem:** Every session writes a `.json` metadata sidecar whose four fields are either redundant with the filename, derivable from filesystem `mtime`, or computable from the JSONL content.

**Problem:** `session_data: dict` is threaded through `_chat_loop` → `_run_foreground_turn` → `_finalize_turn` solely to call `touch_session`, `save_session`, and `increment_compaction` — all of which become no-ops after the sidecar is removed.

**Failure cost:** None critical, but doubles the file count in `.co-cli/sessions/`, adds a second write path on every turn, and requires `session_data` to be passed through the entire call stack.

**Outcome:** After this delivery:
- Each session is a single `.jsonl` file, self-describing by name
- `session.py` is deleted entirely
- `session_data: dict` is removed from `_finalize_turn`, `_run_foreground_turn`, and the chat loop
- `CoSessionState.session_id: str` becomes `CoSessionState.session_path: Path`
- `transcript.py` API accepts `Path` directly — no session ID threading

---

## Scope

In scope:
- Rename JSONL files to `YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl` format
- Delete `session.py` and all `.json` sidecar read/write paths
- Replace `CoSessionState.session_id: str` with `CoSessionState.session_path: Path`
- Remove `session_data: dict` from `_finalize_turn`, `_run_foreground_turn`, and `_chat_loop` — functions that returned `tuple[list[ModelMessage], dict]` return `list[ModelMessage]`
- Update `transcript.py`: `append_messages`, `write_compact_boundary`, `load_transcript` accept `Path` directly
- Update `main.py`: remove `touch_session`, `save_session`, `increment_compaction`, `load_session` call sites; `session_data` dict removed throughout
- Update `bootstrap/core.py`: `restore_session` returns `Path` (not `dict`); remove `save_session` call for new sessions (JSONL path created on first `append_transcript`)
- Update `commands/_commands.py`: `/new` creates a new session path; `/resume` matches by 8-char prefix; remove any `.json` reads
- Update `session_browser.py`: parse `created_at` from filename prefix (already globs `*.jsonl`, sort by name instead of mtime)
- Migration: on startup, rename existing `{uuid}.jsonl` + `{uuid}.json` pairs to new format; delete `.json` sidecar
- Tests: `tests/test_context_session.py` (new), update `tests/test_context_transcript.py`, update `tests/test_bootstrap.py`

Out of scope: changing the JSONL message format, compaction logic, or transcript load/skip logic.

---

## Behavioral Constraints

- `session_path` on `CoSessionState` is the single source of truth for session identity — no separate `session_id` string field
- Display short ID is `path.stem[-8:]` (the UUID suffix from the filename)
- `find_latest_session` returns a `Path` by sorting `glob("*.jsonl")` lexicographically — no `stat()` needed
- `last_used_at` for display is `path.stat().st_mtime`
- `compaction_count` is counted from JSONL boundary lines on demand (session browser only) — never tracked in-memory per turn
- `touch_session` is deleted — `mtime` is updated naturally by each `append_transcript` write; no explicit touch needed
- `save_session` for new sessions is deleted — the JSONL file is created on the first `append_transcript` call, not at session creation time
- Migration runs once on startup before session load: detect old-format files (UUID-only stem), rename JSONL, delete `.json`; non-destructive (rename, not rewrite)
- If a `.jsonl` exists with no paired `.json` (partially migrated), treat as already migrated

---

## High-Level Design

### Filename construction

```
def session_filename(created_at: datetime, session_id: str) -> str:
    ts = created_at.strftime("%Y-%m-%d-T%H%M%SZ")
    return f"{ts}-{session_id[:8]}.jsonl"
```

### Session identity from filename

```
def parse_session_filename(name: str) -> tuple[str, datetime] | None:
    # name: "2026-04-11-T142305Z-550e8400.jsonl"
    # returns (uuid8_prefix, created_at)
    # full UUID recovered from first ModelRequest in JSONL when needed
```

### find_latest_session

```
def find_latest_session(sessions_dir: Path) -> Path | None:
    files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)
    return files[0] if files else None
```

Lexicographic sort = chronological order. No `stat()` call.

### CoSessionState change

```
# Before
session_id: str = ""

# After
session_path: Path = field(default_factory=Path)
```

Display short ID: `deps.session.session_path.stem[-8:]`
Transcript path: `deps.session.session_path` (passed directly to transcript functions)

### _finalize_turn simplification

```
# Before
async def _finalize_turn(..., session_data: dict, ...) -> tuple[list[ModelMessage], dict]:
    next_session = touch_session(session_data)
    save_session(deps.sessions_dir, next_session)
    append_transcript(deps.sessions_dir, deps.session.session_id, new_messages)
    return next_history, next_session

# After
async def _finalize_turn(...) -> list[ModelMessage]:
    append_transcript(deps.session.session_path, new_messages)
    return next_history
```

### Migration (bootstrap, before session load)

```
for json_path in sessions_dir.glob("*.json"):
    uuid_stem = json_path.stem
    if not _is_valid_uuid(uuid_stem):
        continue
    jsonl_path = sessions_dir / f"{uuid_stem}.jsonl"
    if not jsonl_path.exists():
        continue
    session = json.loads(json_path.read_text())
    created_at = datetime.fromisoformat(session["created_at"])
    new_name = session_filename(created_at, uuid_stem)
    jsonl_path.rename(sessions_dir / new_name)
    json_path.unlink()
```

---

## Implementation Plan

### ✓ DONE — TASK-1: New filename helpers + migration + CoSessionState

**files:** `co_cli/context/session.py`, `co_cli/deps.py`, `co_cli/bootstrap/core.py`

- Replace `session.py` contents with: `session_filename`, `parse_session_filename`,
  `find_latest_session` (returns `Path | None`), `migrate_session_files`
- Delete from `session.py`: `new_session`, `save_session`, `load_session`, `touch_session`,
  `increment_compaction`
- `deps.py`: replace `CoSessionState.session_id: str = ""` with
  `session_path: Path = field(default_factory=Path)`
- `bootstrap/core.py`: add `migrate_session_files` call before session load; update
  `restore_session` to return `Path`; remove `save_session` call for new sessions —
  new session path is passed to `deps.session.session_path` without creating the file
  (first `append_transcript` creates it)

`done_when:` `uv run pytest tests/test_context_session.py -x` passes, covering:
- `test_session_filename_format`: output matches `YYYY-MM-DD-THHMMSSz-{8chars}.jsonl`
- `test_session_filename_sortable`: two filenames from sequential datetimes sort lexicographically = chronologically
- `test_find_latest_session_returns_path`: glob on `tmp_path` with two new-format files returns the most recent
- `test_find_latest_session_empty`: returns `None` on empty dir
- `test_migrate_renames_jsonl_and_deletes_json`: old-format pair in `tmp_path` → migration → new filename exists, `.json` gone
- `test_migrate_skips_already_migrated`: new-format `.jsonl` with no paired `.json` is untouched

---

### ✓ DONE — TASK-2: transcript.py path API + chat loop cleanup

**files:** `co_cli/context/transcript.py`, `co_cli/main.py`

**transcript.py:**
- `append_messages(path: Path, messages)` — remove `sessions_dir` + `session_id` parameters
- `write_compact_boundary(path: Path)` — same
- `load_transcript(path: Path)` — same

**main.py — `_finalize_turn`:**
- Remove `session_data: dict` parameter
- Delete `touch_session` and `save_session` calls (`main.py:118-119`)
- `append_transcript(deps.sessions_dir, deps.session.session_id, ...)` →
  `append_transcript(deps.session.session_path, ...)`
- Return type: `list[ModelMessage]` (not `tuple[list[ModelMessage], dict]`)

**main.py — `_run_foreground_turn`:**
- Remove `session_data` parameter; return `list[ModelMessage]`

**main.py — `_chat_loop`:**
- Remove `session_data =` assignment and all passing of `session_data` through the loop
- Remove `load_session` call on session rotation (`main.py:223-227`)
- Remove `increment_compaction` + `save_session` on `compaction_applied` (`main.py:230-231`)
  — `write_compact_boundary` call remains (JSONL boundary marker still needed)
- Transcript hint (`main.py:188`): `deps.sessions_dir / f"{deps.session.session_id}.jsonl"` →
  `deps.session.session_path.exists()`
- Imports: remove `increment_compaction`, `load_session`, `save_session`, `touch_session`

`done_when:` `uv run pytest tests/test_context_transcript.py -x` passes with path-based API;
`uv run pytest tests/ -x` green.

---

### ✓ DONE — TASK-3: session_browser + commands

**files:** `co_cli/context/session_browser.py`, `co_cli/commands/_commands.py`

**session_browser.py** — minimal change (already globs `*.jsonl`, already sorts):
- `session_id = path.stem` → `session_id = path.stem[-8:]` (UUID suffix)
- `last_modified` sort: change from mtime sort to filename lexicographic sort
- Add `created_at` field to `SessionSummary` parsed from filename prefix
- `compaction_count`: count `{"type":"compact_boundary"}` lines in head read (bounded,
  reuse the existing `max_bytes=4096` read where possible; scan full file only if needed)

**commands/_commands.py:**
- `/new`: replace `new_session()` + `save_session()` with `new_session_path(deps.sessions_dir)`
  assigned to `deps.session.session_path`
- `/resume`: match sessions by `path.stem[-8:].startswith(prefix)` instead of reading `.json`
- Remove any remaining `.json` glob or `load_session` imports

`done_when:` `uv run pytest tests/ -x` green; manual `uv run co sessions` lists sessions
with correct `created_at` and short ID.

---

## Testing

All tests in `tests/`. No mocks. `tmp_path` for all filesystem writes.

| Test file | What it covers |
|-----------|----------------|
| `tests/test_context_session.py` (new) | Filename helpers, `find_latest_session`, migration |
| `tests/test_context_transcript.py` | Path-based API for `append_messages`, `load_transcript`, `write_compact_boundary` |
| `tests/test_bootstrap.py` | `restore_session` returns `Path`; `deps.session.session_path` set correctly |

---

## Files

| File | Tasks |
|------|-------|
| `co_cli/context/session.py` | 1 (rewrite — delete old functions, add new helpers) |
| `co_cli/deps.py` | 1 (`session_id` → `session_path` on `CoSessionState`) |
| `co_cli/bootstrap/core.py` | 1 (migration call, `restore_session` returns `Path`) |
| `co_cli/context/transcript.py` | 2 (path-based API) |
| `co_cli/main.py` | 2 (`session_data` removed throughout, import cleanup) |
| `co_cli/context/session_browser.py` | 3 (filename parse, minimal change) |
| `co_cli/commands/_commands.py` | 3 (`/new`, `/resume` updated) |
| `tests/test_context_session.py` | 1 (new) |
| `tests/test_context_transcript.py` | 2 (update to path API) |
| `tests/test_bootstrap.py` | 1 (update `restore_session` assertions) |

---

## Delivery order

1. TASK-1 — filename helpers + migration + `CoSessionState` field (foundational)
2. TASK-2 — transcript path API + `session_data` removal from chat loop (largest cleanup)
3. TASK-3 — session browser + commands (minimal, mostly display)

Ship as a single commit after all three pass the full suite.

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `evals/_deps.py:47` | `CoSessionState(session_id=session_id_override)` — `session_id` no longer exists on `CoSessionState`; field was replaced by `session_path: Path`. This breaks every eval that calls `make_eval_deps()`. Runtime `TypeError` on first eval run. | blocking | TASK-1 |
| `evals/eval_bootstrap_flow.py:113,136` | `deps.session.session_id` accessed — field removed. `AttributeError` at runtime. | blocking | TASK-1 |
| `evals/eval_compaction_quality.py:167,1172,1525,1866` | `CoSessionState(session_id=...)` — same stale field; four call sites. | blocking | TASK-1 |
| `tests/test_transcript.py:116-143` | `test_list_sessions_sorted_by_mtime` — test name and docstring claim mtime ordering; uses `time.sleep(0.05)` to force mtime separation. `list_sessions` now sorts by filename lexicographically. The `time.sleep` is dead code and the test rationale is wrong. Passes coincidentally because `test-session-b > test-session-a` lexicographically. The test must be updated to document the actual sorting contract (filename-lexicographic) and drop the sleep. | minor | TASK-3 |
| `tests/test_transcript.py:135-136` | `summaries[0].session_id == "test-session-b"` — for legacy-format filenames (no timestamp prefix), `session_id = path.stem` (full stem), so assertion still holds. Not broken, but the intent conflicts with the new convention where `session_id` is an 8-char UUID suffix. | minor | TASK-3 |
| `evals/_deps.py:30,38` | Comment `"session_id is routed to CoSessionState"` and the `session_id_override` variable are dead after the field removal but the `overrides.pop("session_id", "eval")` still silently discards the kwarg instead of raising. Callers continue to pass `session_id=` without any error, masking the stale API. | minor | TASK-1 |

**Overall: 3 blocking / 3 minor**

Blocking items are all in `evals/` — the delivered tasks only listed `tests/` files for update, leaving `evals/` with stale `session_id` usage. The eval files are not test files (policy: evals run standalone, not under pytest), so they did not surface in the test suite gate. They must be fixed before the next eval run.

---

## Delivery Summary — 2026-04-12

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_context_session.py -x` passes | ✓ pass |
| TASK-2 | `uv run pytest tests/test_context_transcript.py -x` and full suite green | ✓ pass |
| TASK-3 | `uv run pytest tests/ -x` green | ✓ pass |

**Tests:** full suite — all passed
**Independent Review:** 3 blocking / 3 minor — all fixed (evals stale `session_id` refs, test mtime sort dead code)
**Doc Sync:** fixed — DESIGN-context.md section 2.3 rewritten (single-file format, `session_path`, no sidecar); DESIGN-core-loop.md `_finalize_turn` steps updated; DESIGN-flow-bootstrap.md Step 12 rewritten; Files sections updated across all three docs

**Overall: DELIVERED**
Session sidecar eliminated. Every session is now a single `YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl` file. `CoSessionState.session_path: Path` replaces `session_id: str`. `session_data` dict removed from the entire call stack. Migration runs once on startup.
