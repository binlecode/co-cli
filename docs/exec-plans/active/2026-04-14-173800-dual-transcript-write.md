# Plan: Dual Transcript Writing (JSONL + Markdown)

Task type: code-feature

## Context
Currently, `co-cli` uses `.jsonl` files as the single source of truth for session transcripts. This preserves exact API state fidelity (Pydantic objects, tool schema boundaries) for session resuming. However, `session_search` depends entirely on `session-index.db` (SQLite FTS5). If SQLite fails to initialize, session search degrades completely because `jsonl` is not human-readable or safely greppable. To unify our degradation policy (where memory and articles fall back to `grep_recall`), we need to decouple transcript *resumability* (JSONL) from transcript *searchability/readability* (Markdown).

## Problem & Outcome
**Problem:** If `session-index.db` is unavailable, the agent loses all ability to search past sessions because `jsonl` cannot be cleanly grepped.
**Failure cost:** Users in restrictive environments (no SQLite FTS5) or with locked/corrupted DBs lose the session search feature entirely, violating our graceful degradation principle. 
**Outcome:** The system dual-writes transcripts: `.jsonl` for exact API resuming, and a beautifully formatted `.md` file for human readability and greppability. If the FTS5 index is unavailable, the `session_search` tool degrades gracefully to a `ripgrep`-style or `grep_recall` search over the `.md` transcripts. 

## Scope
1. Update `co_cli/context/transcript.py` to append-write to `{uuid}.md` alongside `{uuid}.jsonl`.
2. Format the `.md` write to clearly label timestamp, role, and content to support regex context extraction during `grep`.
3. Update `session_search` tool to fall back to `grep_recall` on the `.md` files if `ctx.deps.session_index` is None.
4. Support session branching (compaction) in the Markdown files (writing a header link to the parent `.md` session).

## Behavioral Constraints
- **JSONL is the authority:** The system must never attempt to parse or resume a session from the `.md` file. 
- **Side-effect safety:** A failure to write the `.md` file (e.g. disk quota) must *not* fail the turn. It should log a warning but allow the JSONL write and the turn to succeed.
- **Append-only:** The Markdown files must remain append-only, just like JSONL, to ensure crash resilience.
- **Format stability:** The `.md` header format (`### [YYYY-MM-DDTHH:MM:SSZ] Role`) must be stable so `grep_recall` can predictably extract snippet context.

## Failure Modes
- If the `.md` write fails, `grep` fallback will miss the latest turn, but normal FTS5 search (if healthy) or future resumes will still work from JSONL.
- Multi-line tool outputs or giant `<thinking>` blocks should be explicitly excluded from the `.md` transcript to keep it strictly conversational and cleanly greppable.

## High-Level Design
1. **Markdown Formatting Logic:** Create a helper in `co_cli/context/transcript.py` that takes `list[ModelMessage]` and filters down to `UserPromptPart` and `TextPart`, formatting them into:
   ```markdown
   ### [{timestamp}] {Role}
   {content}
   
   ```
2. **Dual-write Hook:** In `append_messages()`, write the JSONL, then `try/except` write the `.md` append.
3. **Session Branching:** In `write_compact_boundary()` or session creation, if branching, append a breadcrumb: `> Continued from session {parent_id}.md`
4. **Search Fallback:** In `co_cli/tools/session_search.py`, if `store is None`, glob all `.md` files in `sessions_dir`, run a Python string search (reusing logic from `grep_recall` or a fast regex), and reconstruct a `SessionSearchResult`-like structure using the `### [{timestamp}]` headers.

## Implementation Plan

- **TASK-1: Markdown Formatter & Dual Write**
  - `files:` `co_cli/context/transcript.py`, `tests/test_transcript.py`
  - Modify `append_messages` to write the `.md` file alongside the `.jsonl` file. Ensure tool parts and thinking parts are stripped. Wrap in `try/except` so errors don't block JSONL persistence.
  - `done_when:` `uv run pytest tests/test_transcript.py` passes and writes both `.jsonl` and cleanly formatted `.md` files in `tmp_path`.
  - `success_signal:` User sees `.md` files appearing alongside `.jsonl` files in `.co-cli/sessions/` after chatting.

- **TASK-2: Compaction Boundary Support for Markdown**
  - `files:` `co_cli/context/transcript.py`, `co_cli/context/session.py`
  - Ensure that when a new branched session is created (compaction), the Markdown file starts with a breadcrumb back to the parent session.
  - `done_when:` `uv run pytest tests/test_transcript.py` validates parent links in new `.md` files.
  - `success_signal:` N/A (Internal structural correctness).

- **TASK-3: Search Fallback to Grep on MD**
  - `files:` `co_cli/tools/session_search.py`, `tests/test_session_search.py`
  - Update `session_search` to check `if store is None:`. Instead of bailing, glob `sessions_dir/*.md`, find matches, extract the nearest preceding `### [timestamp] Role` header for context, and return results.
  - `done_when:` `uv run pytest tests/test_session_search.py` passes, specifically testing the fallback path when `deps.session_index = None`.
  - `success_signal:` Running `co_cli` with a forced `session_index = None` allows the agent to successfully use the `session_search` tool.

## Testing
- Add `test_dual_write_success` and `test_dual_write_md_failure_ignored` in `tests/test_transcript.py`.
- Add `test_session_search_degraded_fallback` in `tests/test_session_search.py` simulating a missing SQLite index.

## Open Questions
- **Q:** Should the `.md` fallback use external `ripgrep` or pure Python?
  - **A:** Pure Python string search (like `grep_recall`) is sufficient because `.md` files strip out the 50k+ char tool noise, keeping file sizes relatively small and fast to parse in memory. It removes the dependency on `rg`.
- **Q:** Do we need to backfill `.md` files for older sessions?
  - **A:** No. `session_search` fallback will simply glob whatever `.md` files exist. Older sessions will only be searchable if FTS5 is healthy, which is an acceptable degradation curve.

---

# Audit Log

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev dual-transcript-write`
