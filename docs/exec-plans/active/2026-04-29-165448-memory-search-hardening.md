# Plan: Sessions Channel Search Hardening

Task type: code-feature

## Context

A gap analysis of the sessions channel (session transcript search) against the hermes-agent
reference design surfaced seven issues. Issue 1 (all-or-nothing timeout) was fixed inline in
this session: `recall.py:_search_sessions()` now uses `asyncio.wait()` + per-task result
collection so sessions that finish before the deadline are returned even if others time out.

This plan addresses the remaining six issues in priority order. Issue 7 (FTS5 phrase-quoting
false negatives) is deferred — it has no clean fix without changing the overall FTS5 tokenizer
strategy and the affected case (non-consecutive identical tokens) is vanishingly rare in
practice.

**Current-state validation:**
- `co_cli/memory/indexer.py`: confirmed — only `user-prompt` and `text` parts indexed; no tool-return content.
- `co_cli/memory/summary.py:_find_match_positions()`: confirmed — strategy-3 term list includes raw boolean operators.
- `co_cli/memory/session_store.py:search()`: confirmed — no recency component; pure BM25 score.
- `co_cli/memory/session_store.py:_like_fallback()`: confirmed — all rows get `rank = -1.0`.
- `co_cli/memory/session_store.py:sync_sessions()`: confirmed — no cleanup of stale entries.
- No existing plan file for this slug.

**Existing tests** relevant to this work:
- `tests/memory/test_search_util.py` — sanitize_fts5_query unit tests
- `tests/memory/test_truncate_around_matches.py` — `_truncate_around_matches` unit tests
- `tests/memory/test_session_search_tool.py` — `memory_search` integration tests
- `tests/memory/test_memory_index.py` — session indexer unit tests

---

## Problem & Outcome

**Problem:** The sessions channel has five structural deficiencies that degrade recall quality
and create unpredictable behavior.

**Failure cost:**
- Tool-return content not indexed → queries matching bash output, error messages, file paths
  find no sessions; agent cannot recall past tool-driven work.
- Boolean operators in window truncation → "auth OR login" anchors the transcript window on
  occurrences of "or" rather than query terms; summarizer sees the wrong passage.
- No recency weighting → a 2-year-old session with exact term matches outranks a session
  from yesterday with partial matches.
- LIKE fallback unranked → when FTS5 errors, top-3 session selection is DB row order, not
  relevance; less relevant sessions crowd out more relevant ones.
- Stale index entries → deleted sessions waste overfetch budget and produce silent misses in
  `_prepare_tasks()`.

**Outcome:** After this plan, the sessions channel correctly indexes tool-produced content,
anchors transcript windows on true query terms, blends recency into ranking, ranks LIKE
fallback by match density, and evicts deleted sessions from the index.

---

## Scope

**In scope:**
- `co_cli/memory/indexer.py`: add tool-return indexing with length cap
- `co_cli/memory/summary.py`: strip FTS5 boolean operators from strategy-3 term list
- `co_cli/memory/session_store.py`: recency blending, LIKE fallback ranking, stale-entry eviction

**Out of scope:**
- Issue 7 (FTS5 phrase-quoting false negatives) — deferred; no clean fix without rethinking
  tokenizer strategy
- Issue 8 (double dedup) — informational; behavior is correct
- Changing the `_SESSIONS_CHANNEL_CAP` or overfetch constants
- Vector/semantic search for the sessions channel

---

## Behavioral Constraints

- `indexer.py` changes must not alter the UNIQUE index on `(session_id, line_index, part_index)`;
  tool-return parts get their own `part_index` naturally.
- Recency blending must keep BM25 dominant (weight ≤ 0.25) so highly relevant old sessions
  still surface.
- LIKE fallback must not change the column shape returned from `_like_fallback` — `_build_results_payload`
  consumes it via the same `SessionSearchResult` path.
- Stale-entry eviction must only remove sessions whose file no longer exists; it must not touch
  sessions whose file exists but hasn't been re-indexed (size unchanged).
- No new required constructor parameters on `SessionStore`; all new constants are module-level
  with sane defaults.

---

## High-Level Design

### TASK-1: Strip FTS5 boolean operators from window truncation terms

In `_find_match_positions()`, strategy-3 individual-term fallback currently includes "AND", "OR",
"NOT" as search terms when the user query uses FTS5 boolean syntax. These tokens appear
throughout any transcript ("or", "not") and flood the match-position list with false anchors,
causing `_best_window_start()` to pick a misaligned window.

Fix: define `_FTS5_BOOL_OPS = frozenset({"and", "or", "not"})` and filter term candidates
in strategy-3. Also filter single-char tokens (already filtered in `_like_tokens` but not here).

### TASK-2: Index tool-return content

Extend `indexer.py:_extract_part()` to handle `part_kind == "tool-return"`. Truncate content
at `_TOOL_RETURN_INDEX_CAP = 500` chars (same as `_TOOL_OUTPUT_TRUNCATION_THRESHOLD` in
summary.py) to avoid bloating the FTS index with large bash outputs. Role stored as `"tool"`.

The FTS index schema does not change (role column already accepts any string). The
`messages` UNIQUE constraint on `(session_id, line_index, part_index)` already accommodates
tool-return parts. Only `indexer.py` changes. Existing session DBs remain valid; tool-return
content surfaces on the next re-index (size-change triggers re-index).

Truncation preserves the first 500 chars — most useful for error messages and short command
outputs. For large file-read outputs, the first 500 chars capture the filename and initial
content which is usually sufficient for recall. If the deserialized `content` field is not
a `str`, stringify it with `json.dumps(content)` before truncation.

### TASK-3: Recency-blended score in `session_store.search()`

After BM25 normalization, blend in a recency factor:

```
recency = 1 / (1 + age_days / RECENCY_HALF_LIFE_DAYS)   # in (0, 1], 1 = today
final_score = bm25_score * (1 - RECENCY_WEIGHT) + recency * RECENCY_WEIGHT
```

Constants: `RECENCY_HALF_LIFE_DAYS = 90` (90-day half-life), `RECENCY_WEIGHT = 0.2`.
At weight 0.2, BM25 contributes 80% of the score. A session from 90 days ago gets
`recency = 0.5`, contributing `0.5 * 0.2 = 0.1` penalty relative to a session from today.
This is enough to break ties without overriding strong BM25 signals.

`created_at` is already stored in `sessions` table as an ISO8601 string. Parse it with
`datetime.fromisoformat()` in the dedup loop; compute `age_days` once per session.

### TASK-4: LIKE fallback ranking by match density

Both `session_store.py:_like_fallback()` (line 233) and `knowledge_store.py:_chunks_like_fallback()`
(line 139) use the same flat `-1.0 AS rank` pattern — fix both.

Replace the flat `-1.0` rank with a per-row match-count rank. Build the SQL dynamically
with a `SUM(CASE WHEN content LIKE ? THEN 1 ELSE 0 END ...)` expression covering all tokens.
The resulting `match_count` ranges from `1` to `len(tokens)`. Store as `-float(match_count)`
so `normalize_bm25` maps multi-token matches to higher scores (e.g., 3 matches → score
`3/4 = 0.75`). The dedup loop in `search()` already selects the highest-scoring message per
session, so this naturally propagates.

### TASK-5: Stale-entry eviction in `sync_sessions()`

After the main index loop, query `SELECT session_id, session_path FROM sessions`, and for
each row where `session_path` no longer exists on disk, delete `messages` first (so the
`messages_ad` trigger correctly removes FTS entries), then delete from `sessions`.
Log a debug message per eviction.

This runs once per startup (same cadence as `sync_sessions()`). For most users with intact
session directories the loop terminates quickly; only deleted sessions incur DB writes.

---

## Implementation Plan

### TASK-1 — Strip booleans from window truncation terms

**files:**
- `co_cli/memory/summary.py`
- `tests/memory/test_truncate_around_matches.py`

**done_when:**
```
uv run pytest tests/memory/test_truncate_around_matches.py -x -q
```
passes, including new tests that assert:
- `_find_match_positions(text, "auth OR login")` returns positions of "auth" and "login" but
  not positions of the word "or" in unrelated text
- `_find_match_positions(text, "deploy NOT prod")` excludes "not" positions

**success_signal:** A query like "auth OR login" extracts a window anchored on the auth/login
passage rather than a random "or" occurrence elsewhere in the transcript.

---

### TASK-2 — Index tool-return content

**files:**
- `co_cli/memory/indexer.py`
- `tests/memory/test_memory_index.py`

**done_when:**
```
uv run pytest tests/memory/test_memory_index.py -x -q
```
passes, including new tests that assert:
- A session with a tool-return part containing "xyloquartz-tool-return-unique" is indexed
- `store.search("xyloquartz-tool-return-unique")` returns a result with role `"tool"`
- Content is truncated at `_TOOL_RETURN_INDEX_CAP` (500 chars) when the tool output exceeds that

**success_signal:** Querying for a term that appeared only in a past tool return (e.g., a
bash error message) surfaces the session via `memory_search`.

---

### TASK-3 — Recency-blended score

**files:**
- `co_cli/memory/session_store.py`

**done_when:**
```
uv run pytest tests/memory/test_session_search_tool.py -x -q
```
passes, including new tests that assert:
- Two sessions with identical content (same BM25 score) but different `created_at` set
  directly in the DB (one today, one 200+ days ago) — the recent session's final score
  is strictly higher than the old session's final score
- Two sessions where session A has a stronger FTS5 BM25 score than session B, but session
  B's `created_at` is today and session A's is 200+ days ago — session A still ranks first
  (confirming BM25 dominance over recency)

**success_signal:** When two sessions are roughly equally relevant, the more recent one
appears first in `memory_search` results.

**prerequisites:** [TASK-2]

---

### TASK-4 — LIKE fallback ranking by match density

**files:**
- `co_cli/memory/session_store.py`
- `co_cli/memory/knowledge_store.py`

**done_when:**
```
uv run pytest tests/memory/test_session_search_tool.py -x -q
```
passes, including a new test that:
- Forces FTS5 failure (passes a deliberately broken query that falls through to LIKE)
- Writes two sessions: session A content matches 2 tokens, session B matches 1 token
- Asserts session A has a higher score in the returned results than session B

**success_signal:** When FTS5 is unavailable, sessions with more matching terms appear
higher in results.

**prerequisites:** [TASK-3]

---

### TASK-5 — Stale-entry eviction

**files:**
- `co_cli/memory/session_store.py`

**done_when:**
```
uv run pytest tests/memory/test_session_search_tool.py -x -q
```
passes, including a new test that:
- Indexes a session, then deletes the file from disk
- Calls `sync_sessions()` again
- Asserts the session no longer appears in `store.search()` results

**success_signal:** After a session file is deleted and co-cli restarts (triggering
`sync_sessions()`), the deleted session no longer surfaces in `memory_search`.

**prerequisites:** [TASK-3]

---

## Testing

All changes are unit/integration tested in the existing test suite under `tests/memory/`.
No new test files — tests are added to the files already covering each module. Each task's
`done_when` specifies the concrete pytest target.

Full suite must pass after all tasks: `uv run pytest tests/ -x -q`.

---

## Open Questions

None — all design choices are resolved by inspection of the existing source.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1   | adopt    | Both original assertions were physically impossible; replaced with implementable variants using direct DB `created_at` control | Replaced both TASK-3 `done_when` assertions with: (1) identical-content same-BM25 tie-break test; (2) stronger-BM25-but-older session still ranks first test |
| CD-m-1   | adopt    | json.dumps fallback is a one-liner and makes the contract explicit | Added to TASK-2 High-Level Design: "If `content` is not a `str`, stringify with `json.dumps(content)` before truncation" |
| CD-m-2   | adopt    | Deletion order matters for FTS trigger correctness; cheap to specify | Added to TASK-5 design: delete messages before sessions |
| PO-m-1   | adopt    | Already resolved by CD-M-1 adoption — both TASK-3 assertions are now explicit and mechanically testable | Covered by CD-M-1 changes above |

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-search-hardening`
