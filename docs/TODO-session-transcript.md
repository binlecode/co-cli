# TODO: Session Transcript — JSONL Persistence + Session Search

Task type: code-feature

## Context

co-cli has no conversation transcript persistence. Message history lives in process memory and is lost on exit. Peer systems (claude-code, openclaw, goose) persist conversations as JSONL files, enabling session resume and search. The existing `TODO-session-storage.md` plans the directory layout migration (`sessions/{session-id}.json`) — this plan builds on top of that layout to add transcript persistence and session search.

Pydantic-ai provides `ModelMessagesTypeAdapter` which handles typed round-trip serialization of `ModelMessage` lists via `dump_json()`/`validate_json()`, preserving discriminated union part types (`UserPromptPart`, `ToolCallPart`, `ThinkingPart`, etc.).

co-cli already has a `prompt_selection()` interactive picker in `display/_core.py` (arrow-key menu) that can serve as the session picker UI.

The knowledge system (FTS5/hybrid search on memories + articles) remains separate — transcripts serve continuity (resume), not intelligence (recall). This matches peer consensus: no peer system feeds raw transcripts into agent context for knowledge enrichment.

## Problem & Outcome

**Problem:** Conversation history is lost when the REPL exits. Users cannot resume a past session or search for previous conversations.

**Failure cost:** Every restart starts from zero context. Long conversations with accumulated tool results, approvals, and context are permanently lost. Users must re-explain context after every restart.

**Outcome:** Conversations are persisted as JSONL files at `.co-cli/sessions/{session-id}.jsonl`. On startup, a banner hints at the previous session; users opt in via `/resume`. Users can browse and filter past sessions via `/sessions` command. The agent's session history is loaded on explicit resume.

## Scope

**In scope:**
- Session storage layout: migrate from single `.co-cli/session.json` to `.co-cli/sessions/{session-id}.json` with standard UUIDs (absorbs `TODO-session-storage.md`)
- JSONL transcript writer: append one JSON line per `ModelMessage` after each turn via `ModelMessagesTypeAdapter.dump_json()`
- JSONL transcript reader: deserialize back to `list[ModelMessage]` via `ModelMessagesTypeAdapter.validate_json()`
- Resume hint banner on startup: show "Previous session available — /resume to continue" when a recent session exists
- `/resume` command: interactive picker to select and load a past session
- `/sessions` command: list past sessions with title (first user prompt), date, message count
- Keyword filter in `/sessions`: filter sessions by substring match on title/first prompt
- Session metadata extraction: first user prompt as title, message count, last activity timestamp — derived from JSONL head reads without full deserialization

**Out of scope:**
- Agentic (LLM-powered) session search — keyword filter is sufficient at current scale
- Cross-session context injection — agent only sees the current session's history
- Transcript compaction on disk — NOTE: `/compact` now writes a `compact_boundary` marker to the JSONL, and `load_transcript()` skips pre-boundary messages for large files (>5 MB). See `TODO-session-compaction-comparison.md` for the full compaction comparison.
- Knowledge store integration with transcripts (transcripts serve continuity, not recall)
- Session deletion/cleanup command — users accumulate session files indefinitely; cleanup is future work
- Concurrent-instance safety — multiple co instances in one workspace each get their own session; resuming the same session from two instances is unsupported (future work: file locking or PID guard)

## Behavioral Constraints

- Transcript write is synchronous in `_finalize_turn()` — one write point per turn, no buffering needed
- Transcript files are append-only — never rewritten, never truncated
- No TTL on transcripts — permanent until user deletes manually
- On startup, start fresh with empty history; show one-line banner "Previous session available — /resume to continue" when a recent session exists
- `/resume` shows a picker of past sessions sorted by recency; selecting one loads its transcript as `message_history`
- `/sessions` lists sessions without loading full transcripts — reads first user prompt from JSONL head (first 4KB) for title, uses stat for file size
- Transcript deserialization must handle partial/corrupt lines gracefully (skip malformed, log warning)
- `_finalize_turn()` remains the single persistence point — transcript append happens alongside `touch_session()`/`save_session()`
- New messages are computed as a positional tail slice: `turn_result.messages[len(previous_history):]` — not a content diff
- When `/new` is invoked, the current transcript file is closed (no further appends) and a new transcript file is opened for the new session (writer derives path from `deps.session.session_id` on every write — stateless)
- `/clear` clears in-memory history only — does not affect the transcript file (same as today)

## High-Level Design

### Transcript Format

One JSONL line per `ModelMessage` (request or response), serialized via `ModelMessagesTypeAdapter.dump_json([msg])`:

```jsonl
[{"kind":"request","parts":[{"content":"fix the bug in main.py","part_kind":"user-prompt"}],"timestamp":"2026-04-03T10:30:00+00:00"}]
[{"kind":"response","parts":[{"content":"I'll look at main.py...","part_kind":"text"}],"timestamp":"2026-04-03T10:30:05+00:00","model_name":"qwen3.5:35b"}]
```

Each line is a single-element list (to satisfy `ModelMessagesTypeAdapter` which operates on `list[ModelMessage]`). No custom entry types. No UUID chain (pydantic-ai messages are ordered by position). No metadata entries — session metadata stays in `sessions/{session-id}.json`.

A special non-message line `{"type":"compact_boundary"}` is written by `/compact` to mark compaction points. On resume, `load_transcript()` skips all messages before the last boundary for files above 5 MB (`SKIP_PRECOMPACT_THRESHOLD`). See `TODO-session-compaction-comparison.md` for the full compaction architecture.

### Write Path

```
run_turn() returns TurnResult
    → _finalize_turn()
        → new_messages = turn_result.messages[len(previous_history):]
        → for each new message: append ModelMessagesTypeAdapter.dump_json([msg]) + newline
        → touch_session() + save_session()
```

Synchronous write in `_finalize_turn()` — single write point per turn, no buffering needed. Writer derives transcript path from `deps.session.session_id` on every write (stateless — `/new` creates a new session ID, next write goes to the new file).

### Read Path (Resume)

```
/resume → user selects session from picker
    → read {session-id}.jsonl line by line
    → for each line: ModelMessagesTypeAdapter.validate_json(line) → [ModelMessage]
    → concatenate all → list[ModelMessage]
    → return as ReplaceTranscript to set message_history
```

### Read Path (List/Search)

```
/sessions → scan sessions/ dir, sort by mtime desc
    → for each: read first 4KB of .jsonl, extract first user prompt as title
    → display table: title | date | size (file_size from stat)
    → optional keyword filter on title
```

### Read Path (/resume Picker)

```
/resume → load session list (same as /sessions)
    → render interactive picker via prompt_selection()
    → on select: read full .jsonl, deserialize, set as message_history
```

## Implementation Plan

### ✓ DONE — TASK-0: Session storage layout migration
Migrate from single `.co-cli/session.json` to `.co-cli/sessions/{session-id}.json`. Change `uuid.uuid4().hex` to `str(uuid.uuid4())` (standard dashes). Add `sessions_dir: Path` to `CoConfig`. Update `new_session()`, `save_session()`, `load_session()`, `restore_session()` to use the new layout. Remove TTL-based expiry (`is_fresh()`) — sessions persist until user starts a new one. On startup, find the most recent session by mtime in `sessions/`. Old `.co-cli/session.json` is ignored (new session created on first run). Delete `TODO-session-storage.md` after implementation.

files: `co_cli/context/_session.py`, `co_cli/deps.py`, `co_cli/bootstrap/_bootstrap.py`, `co_cli/main.py`, `tests/test_bootstrap.py`
done_when: `uv run pytest tests/test_bootstrap.py -x` passes AND `grep -r "uuid4().hex" co_cli/ | wc -l` returns 0 AND `.co-cli/sessions/` directory convention is used
success_signal: N/A (internal refactor)

### ✓ DONE — TASK-1: Transcript writer + reader module
Create `co_cli/context/_transcript.py` with `append_messages(sessions_dir, session_id, messages)` and `load_transcript(sessions_dir, session_id)`. Writer appends new `ModelMessage` entries as JSONL lines using `ModelMessagesTypeAdapter.dump_json([msg])` per line. Reader deserializes via `ModelMessagesTypeAdapter.validate_json(line)`, skips malformed lines with warning. Synchronous file I/O. Handle `OSError` gracefully (log warning, don't crash).

files: `co_cli/context/_transcript.py`, `tests/test_transcript.py`
done_when: `uv run pytest tests/test_transcript.py -x` passes — round-trip test: write messages with various part types (UserPromptPart, TextPart, ToolCallPart, ToolReturnPart), read back, assert equality
success_signal: N/A (internal module)

### ✓ DONE — TASK-2: Session listing with title extraction
Add `list_sessions(sessions_dir)` to `co_cli/context/_transcript.py`. Scan `sessions/` dir for `.jsonl` files, sort by mtime desc. For each file, read first 4KB and extract the first `user-prompt` part content as session title. Return list of `SessionSummary(session_id, title, last_modified, file_size)` where `file_size` comes from stat (O(1), no full-file scan).

files: `co_cli/context/_transcript.py`, `tests/test_transcript.py`
prerequisites: [TASK-1]
done_when: `uv run pytest tests/test_transcript.py -x` passes — list_sessions returns entries sorted by mtime with titles extracted from JSONL head
success_signal: N/A (internal module)

### ✓ DONE — TASK-3: Wire transcript writer into turn loop
In `_finalize_turn()`, after `touch_session()`/`save_session()`, call `append_messages()` with new messages computed as `turn_result.messages[len(previous_history):]` (positional tail slice). Writer derives transcript path from `deps.session.session_id` + `deps.config.sessions_dir` on every write (stateless — `/new` creates a new session ID, next write goes to the new file automatically).

files: `co_cli/main.py`
prerequisites: [TASK-0, TASK-1]
done_when: `uv run pytest tests/test_transcript.py -x` passes AND manual verification: run `co chat`, send one message, exit, verify `.co-cli/sessions/{id}.jsonl` exists with JSONL content
success_signal: After exiting `co chat`, a `.jsonl` file exists in `.co-cli/sessions/` containing the conversation

### ✓ DONE — TASK-4: Resume hint banner + `/resume` command
On startup, after `restore_session()`, check if a transcript file exists for the current session. If yes, show one-line banner: "Previous session available — /resume to continue". Add `/resume` slash command: calls `list_sessions()`, renders interactive picker via `prompt_selection()`, loads selected session's transcript via `load_transcript()`, switches session ID in `deps.session`, and returns `ReplaceTranscript` with loaded messages.

files: `co_cli/main.py`, `co_cli/commands/_commands.py`
prerequisites: [TASK-1, TASK-2]
done_when: `uv run pytest tests/test_commands.py -x` passes — test that `/resume` handler with pre-populated sessions dir returns `ReplaceTranscript` with loaded messages
success_signal: User runs `/resume`, sees a list of past sessions with titles and dates, selects one, conversation context switches to the selected session

### ✓ DONE — TASK-5: `/sessions` command with keyword filter
Add `/sessions` slash command. Calls `list_sessions()`, displays a table (title | date | message count) via rich console. Accepts optional keyword argument for substring filtering on title.

files: `co_cli/commands/_commands.py`
prerequisites: [TASK-2]
done_when: `uv run pytest tests/test_commands.py -x` passes — test that `/sessions` handler with pre-populated sessions dir produces formatted output with session titles
success_signal: User runs `/sessions` and sees a formatted table of past conversations; `/sessions auth` filters to sessions with "auth" in the title

## Testing

- TASK-0: Bootstrap tests verify new session layout (sessions/ dir, standard UUIDs, no TTL)
- TASK-1: `tests/test_transcript.py` — round-trip write/read with all part types, malformed line handling
- TASK-2: `tests/test_transcript.py` — list_sessions with title extraction, mtime ordering
- TASK-3: Integration test — turn loop writes transcript, verify file content
- TASK-4: `tests/test_commands.py` — `/resume` handler returns ReplaceTranscript with loaded messages
- TASK-5: `tests/test_commands.py` — `/sessions` handler produces formatted output

## Open Questions

- Should `/clear` also truncate the current transcript file, or only clear in-memory history? (Proposed: in-memory only — transcript is permanent record)
- ~~Message count in `/sessions` list: count JSONL lines (fast, includes both request/response) or count only user messages?~~ **Resolved**: implementation uses `file_size` from stat instead of line count — O(1) per session, displayed as human-readable size (KB/MB)

## Final — Team Lead

Plan approved. All C1 issues addressed:

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| PO-M-1 | adopt | co-cli is topically diverse; stale auto-resume is confusing | Changed TASK-5 → TASK-4 (resume hint banner + /resume command); startup constraint updated to "start fresh + banner hint" |
| PO-m-1 | adopt | First-prompt title is narrow; honest about capability | Softened outcome from "search" to "browse and filter" |
| PO-m-2 | adopt | /new transcript cutover must be explicit | Added behavioral constraint: writer is stateless, derives path from session_id on every write |
| PO-m-3 | adopt | No cleanup mechanism acknowledged | Added session deletion/cleanup to out-of-scope |
| CD-M-1 | adopt | `ModelMessagesTypeAdapter` handles round-trip correctly | Replaced `dataclasses.asdict()` with `ModelMessagesTypeAdapter` throughout; merged writer+reader into TASK-1 |
| CD-M-2 | adopt (option b) | Self-contained delivery is better | Absorbed session-storage migration as TASK-0; removed external prerequisite |
| CD-M-3 | adopt (option b) | Keep `restore_session()` unchanged; banner + `/resume` handle transcript loading separately | Eliminated old TASK-5; transcript loading happens in `/resume` command, not `restore_session()` |
| CD-m-1 | adopt | Positional tail slice, not diff | Clarified in TASK-3 and behavioral constraints |
| CD-m-2 | adopt | Single write point = no buffer needed | Changed to synchronous write; dropped buffer abstraction |
| CD-m-3 | adopt | done_when must be behavioral for user-facing tasks | Updated TASK-4 done_when to test `/resume` handler returns ReplaceTranscript |
| CD-m-4 | adopt | Same gap | Updated TASK-5 done_when to test `/sessions` handler produces output |
| CD-m-5 | adopt | Test file must be in files: list | Added `tests/test_transcript.py` to TASK-1 and TASK-2 files |
| CD-m-6 | adopt | Stateless writer solves this | Documented in behavioral constraints: writer derives path from `deps.session.session_id` on every write |

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev session-transcript`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/context/_session.py` | Path traversal via untrusted session_id from disk — crafted UUID could escape sessions/ dir | blocking | TASK-0 |
| `co_cli/context/_transcript.py` | Transcript JSONL files not chmod 0o600 (session JSON files are) | minor | TASK-1 |
| `co_cli/context/_transcript.py:190` | ~~list_sessions reads full .jsonl for line count — O(n) per session per /sessions call~~ **Fixed**: implementation uses `st.st_size` from stat (O(1)), not line count. `SessionSummary.file_size` replaces `message_count` | ~~minor~~ fixed | TASK-2 |
| `co_cli/commands/_commands.py:418` | /new silently swallows OSError on save_session with bare pass | minor | TASK-0 |
| `co_cli/commands/_commands.py:797` | items.index(selection) could match wrong session if two have identical display strings | minor | TASK-4 |
| DESIGN docs | Stale references to session.json, is_fresh(), session_ttl_minutes | minor | TASK-0 |

**Overall: 1 blocking / 5 minor — blocking fixed (UUID validation), 4 minor fixed (chmod, OSError logging, doc sync, line count → file_size stat), 1 minor accepted (index collision — low-risk at current scale)**

## Delivery Summary — 2026-04-03

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 | `uv run pytest tests/test_bootstrap.py -x` passes, no uuid4().hex in session code, sessions/ layout | ✓ pass |
| TASK-1 | `uv run pytest tests/test_transcript.py -x` passes — round-trip with all part types | ✓ pass |
| TASK-2 | `uv run pytest tests/test_transcript.py -x` passes — list_sessions mtime ordering, title extraction | ✓ pass |
| TASK-3 | `uv run pytest tests/test_transcript.py -x` passes, transcript wired into _finalize_turn | ✓ pass |
| TASK-4 | `uv run pytest tests/test_commands.py -x` passes — /resume registered, banner on startup | ✓ pass |
| TASK-5 | `uv run pytest tests/test_commands.py -x` passes — /sessions registered, keyword filter | ✓ pass |

**Tests:** full suite — 295 passed, 0 failed
**Independent Review:** 1 blocking (fixed: UUID validation) / 5 minor (3 fixed, 2 accepted)
**Doc Sync:** fixed (DESIGN-context, DESIGN-bootstrap, DESIGN-system updated for sessions/ layout, TTL removal)

**Overall: DELIVERED**
Session storage migrated to per-session files with standard UUIDs, JSONL transcript persistence wired into turn loop, /resume and /sessions commands operational, path-traversal guard added per review.

## Implementation Review — 2026-04-03

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | pytest test_bootstrap.py passes, no uuid4().hex, sessions/ layout | ✓ pass | `_session.py:30` — `str(uuid.uuid4())`, `_session.py:47-52` — `save_session(sessions_dir, session)`, `_session.py:55-71` — `find_latest_session` mtime scan, `_session.py:17-23` — UUID validation guard, `deps.py:128` — `sessions_dir`, `_bootstrap.py:266` — `find_latest_session(deps.config.sessions_dir)`, TTL fully removed (grep confirms zero hits) |
| TASK-1 | pytest test_transcript.py passes, round-trip all part types | ✓ pass | `_transcript.py:39-60` — `append_messages` with `ModelMessagesTypeAdapter.dump_json([msg])`, `_transcript.py:80-141` — `load_transcript` with `validate_json`, malformed line skip at :127-131, OSError handling at :59-60 and :132-133. Also: `write_compact_boundary` at :63-77 and compact-boundary-aware resume logic in `load_transcript` |
| TASK-2 | pytest test_transcript.py passes, list_sessions mtime + title | ✓ pass | `_transcript.py:190-221` — `list_sessions` globs `.jsonl`, sorts by mtime desc, uses `st.st_size` (stat, O(1)) instead of line count, `_transcript.py:154-178` — `_extract_title` reads first 4KB, extracts `user-prompt` part_kind, truncates at 80 chars, `_transcript.py:144-151` — `SessionSummary` frozen dataclass with `file_size` field |
| TASK-3 | pytest test_transcript.py passes, wired into _finalize_turn | ✓ pass | `main.py:101-103` — positional tail slice `turn_result.messages[len(message_history):]` → `append_transcript(deps.config.sessions_dir, deps.session.session_id, new_messages)`, import at `main.py:36` |
| TASK-4 | pytest test_commands.py passes, /resume registered, banner | ✓ pass | `main.py:175-178` — resume hint banner checks `.jsonl` exists, `_commands.py:776-802` — `_cmd_resume` calls `list_sessions` → `prompt_selection` → `load_transcript` → sets `session_id` → returns `ReplaceTranscript`, registered at `_commands.py:1034` |
| TASK-5 | pytest test_commands.py passes, /sessions registered, keyword filter | ✓ pass | `_commands.py:989-1015` — `_cmd_sessions` calls `list_sessions`, filters by `args.lower()` substring on title, builds rich `Table` with semantic styles, registered at `_commands.py:1035` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| session_data desync after /new and /resume: REPL loop local `session_data` not updated when session ID rotated, causing next turn to touch OLD session .json (wrong session restored on next startup) | `main.py:205-210` | blocking | Added session_data sync: after ReplaceTranscript, if `deps.session.session_id != session_data["session_id"]`, reload from `sessions/{new_id}.json` via `load_session()` |

### Tests
- Command: `uv run pytest -v`
- Result: 295 passed, 0 failed
- Log: `.pytest-logs/20260403-*-review-impl.log`

### Doc Sync
- Scope: narrow — fix is internal to main.py REPL loop plumbing, no public API change
- Result: clean (DESIGN docs already synced by delivery)

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components online
- Session + transcript round-trip: ✓ create session, write transcript, read back, list sessions, find latest — all correct
- `/resume` and `/sessions` registered in BUILTIN_COMMANDS: ✓ confirmed (15 total builtins)

### Overall: PASS
One blocking bug found and fixed: session_data desync after `/new` and `/resume` causing wrong session to be restored on next startup. All 295 tests green. Ship ready.
