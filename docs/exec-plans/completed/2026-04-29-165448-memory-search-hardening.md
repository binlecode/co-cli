# Plan: Sessions Channel Search Hardening

Task type: code-feature

## Context

A gap analysis of the sessions channel (session transcript search) against the hermes-agent
reference design surfaced seven issues. Issue 1 (all-or-nothing timeout) was fixed inline in
this session: `recall.py:_search_sessions()` now uses `asyncio.wait()` + per-task result
collection so sessions that finish before the deadline are returned even if others time out.

**Scope trimmed 2026-04-30** after `2026-04-30-103908-port-openclaw-session-chunked-recall.md`
landed. The port plan moves session indexing into the unified chunked pipeline
(`source='session'` rows in `chunks`/`chunks_fts`/`chunks_vec`) and routes session recall
through `KnowledgeStore` with MMR + temporal decay. As a result, the original tasks targeting
the legacy `session_store.py` FTS path and the LLM-summarize window pipeline are
superseded — see "Superseded by port plan" below for what was removed and why.

The remaining task addresses a bug in the LIKE fallback ranking inside
`knowledge_store.py:_chunks_like_fallback`. After the port lands, sessions share that
fallback path with artifacts, so the fix benefits both sources.

**Current-state validation:**
- `co_cli/memory/knowledge_store.py:_chunks_like_fallback()` (line 139): confirmed — all rows
  get `rank = -1.0`, so the dedup loop falls back to DB row order rather than match density.

**Existing tests** relevant to this work:
- `tests/memory/test_knowledge_store.py` — chunks FTS + LIKE fallback paths
- `tests/memory/test_search_util.py` — `sanitize_fts5_query`, `run_fts`, `normalize_bm25`

**Superseded by `port-openclaw-session-chunked-recall`:**
- Original TASK-1 (strip FTS5 boolean ops in `summary.py:_find_match_positions`) — only
  matters for the legacy LLM-summarize window pipeline; chunked path doesn't anchor a window.
- Original TASK-2 (index tool-return content in `indexer.py`) — chunked path keeps
  `role=='tool'` records and emits `Tool[<name>]: <content>` lines into the unified
  `chunks_fts`. Tool-return content is BM25-searchable natively.
- Original TASK-3 (recency-blended score in `session_store.search()`) — replaced by
  `_temporal_decay` in `KnowledgeStore` (port-plan TASK-2), 30-day half-life applied to
  `source='session'` rows.
- Original TASK-5 (stale-entry eviction in `sync_sessions()`) — eviction must cover both
  the legacy `messages_fts` and the chunked `chunks` rows during dual-write; folded into
  the port plan's lifecycle work rather than half-fixed in legacy here.
- Backlog G1 (bounded summarizer concurrency) — only applies to the LLM-summarize path,
  which becomes deprecated after port-plan Phase 3.
- Backlog G2 (tool-metadata in FTS call-site coverage) — chunked path includes tool name
  in the line prefix natively; full tool-call invocation metadata is a separate future plan
  against the chunked pipeline.
- Backlog G7 (pre-rank source filtering in hybrid search) — absorbed into port-plan TASK-2,
  which audits both FTS and vec legs to push `source IN (...)` before the RRF merge.

---

## Problem & Outcome

**Problem:** `knowledge_store.py:_chunks_like_fallback` returns a flat `-1.0 AS rank` for every
row when FTS5 errors and the LIKE fallback fires. Downstream, `normalize_bm25` then maps
every row to the same normalized score, so the dedup-by-best-score loop in `search()` picks
arbitrary DB row order rather than match density.

**Failure cost:** When FTS5 is unavailable (sanitization edge case, tokenizer corner case),
the top-N result window is filled by whatever rows the DB happened to return first instead
of the rows with the most matching tokens. After the port plan ships, the same fallback
is hit by both artifact and session sources — the bug then degrades both channels.

**Outcome:** `_chunks_like_fallback` ranks rows by match density (count of distinct query
tokens that appear in the row) so the LIKE fallback returns the most relevant rows first.
The dedup loop in `search()` continues to work unchanged — it just sees real ranks now.

---

## Scope

**In scope:**
- `co_cli/memory/knowledge_store.py`: replace flat `-1.0 AS rank` in `_chunks_like_fallback`
  with a per-row match-count expression.

**Out of scope:**
- Anything in the legacy `session_store.py` LIKE fallback — that store is being deprecated
  by the port plan.
- The same bug in any other LIKE fallback path (none other exists today; `_chunks_like_search`
  is the only call site after sessions migrate).
- Tokenizer or sanitizer changes.
- Adding tests against `session_store.py` — the legacy session LIKE path is on a
  deprecation timer.

---

## Behavioral Constraints

- The fallback must not change the column shape returned from `_chunks_like_search` —
  callers consume rows positionally and via the existing `SearchResult` mapping path.
- Match-count ranking must be order-stable for ties (DB-row-order tiebreak is fine; do
  not introduce randomness).
- No new required parameters on `KnowledgeStore`; new behavior is unconditional within
  the LIKE fallback.

---

## High-Level Design

### TASK-1: LIKE fallback ranking by match density

`knowledge_store.py:_chunks_like_fallback()` (line 139) uses a flat `-1.0 AS rank` for every
row, so `normalize_bm25` cannot differentiate match density.

Fix: replace the flat literal with a per-row match-count expression. Build the SQL
dynamically with a `SUM(CASE WHEN content LIKE ? THEN 1 ELSE 0 END ...)` aggregate covering
all tokens. The resulting `match_count` ranges from `1` to `len(tokens)`. Store as
`-float(match_count)` so `normalize_bm25` maps multi-token matches to higher scores (e.g., 3
of 4 tokens → normalized score `3/4 = 0.75`). The token-LIKE pattern `?` parameters are
appended to the existing parameter list in matching order.

The aggregate runs over rows already filtered by the `OR`-joined LIKE predicates, so cost
is bounded by the existing fallback cost. No GROUP BY needed — `match_count` is a row-level
expression evaluated per row, not a session aggregate.

---

## Implementation Plan

### ✓ DONE TASK-1 — LIKE fallback ranking by match density

**files:**
- `co_cli/memory/knowledge_store.py`
- `tests/memory/test_knowledge_store.py`

**done_when:**
```
uv run pytest tests/memory/test_knowledge_store.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task1.log
```
passes, including a new test that:
- Indexes two artifacts: artifact A content matches 2 distinct query tokens, artifact B
  matches 1 token.
- Forces FTS5 failure by passing a query that triggers the LIKE fallback (e.g., a query
  whose `sanitize_fts5_query` output the FTS engine rejects, or by mocking FTS5 raise).
- Asserts artifact A appears before artifact B in the returned results, and A's score is
  strictly greater than B's.
- Asserts the result row shape (column count and order) matches the existing `SearchResult`
  mapping.

**success_signal:** When FTS5 is unavailable, artifacts (and post-port, sessions) with
more matching tokens appear higher in `memory_search` results than rows with fewer matches.

---

## Testing

The change is covered by tests added to `tests/memory/test_knowledge_store.py`. No new test
files. Real fixtures, no mocks (per CLAUDE.md), except for the targeted FTS5-failure path
where a tightly scoped mock or a deliberately malformed query is acceptable to force the
fallback branch.

Full-suite gate after TASK-1: `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`.

---

## Open Questions

None — the design is mechanically determined by the existing fallback shape.

---

## Backlog — Follow-up Work (Out of Scope for This Plan)

Source: `docs/reference/RESEARCH-memory-recall-design-families.md` §8 (sessions porting gaps)
+ §11 (cheap-borrow candidates). Items absorbed by `port-openclaw-session-chunked-recall`
are not relisted here.

Already resolved (do not re-implement):
- FTS5 query sanitization (`sanitize_fts5_query` in `search_util.py`, wired in both the sessions
  and artifacts channels — commit `59e45b4`).
- LIKE fallback when FTS5 MATCH throws (`run_fts()` with `like_fallback=` in both
  `session_store.py` and `knowledge_store.py` — commit `58325cf`).

### Sessions Channel — Cross-Path Recall Gaps

These items still apply once the chunked pipeline is primary, because the active session
and parent-session lineage logic live above both pipelines.

#### G3: Parent-session lineage resolution
**Where:** `co_cli/memory/session_store.py` (and the new `KnowledgeStore.index_session`
backfill path) — exclusion logic during sync.
**Why:** Current exclusion is path-equality only (skips the active session). When delegation
lands, a forked sub-agent session shares parent-session lineage but a different path — it
will appear in sessions-channel results even when it should be excluded as part of the
current logical session.
**Effort:** ~20 LOC — needs `session_meta` parent-chain traversal during sync or at query time.
**Dependency:** Blocker lands when delegation is active; low urgency until then.

#### G4: CJK trigram sidecar (chunks-pipeline framing)
**From:** hermes commit `1fa76607` — `messages_fts_trigram` virtual table alongside
`messages_fts`.
**Where:** `co_cli/memory/knowledge_store.py` schema — `chunks_fts_trigram` sidecar.
**Why:** Porter tokenizer (`tokenize='porter unicode61'`) fragments CJK characters. A trigram
sidecar enables substring search for Chinese/Japanese/Korean content without replacing the
porter index. Affects both artifacts and (post-port) sessions.
**Effort:** ~25 LOC (schema + dual-query path). Low-impact for current English-only users;
defer until non-Latin content is a real use case.

#### G5: Live-session search
**Where:** Both `session_store.py:sync_sessions()` (legacy path) and
`KnowledgeStore.index_session()` (chunked path) skip the active session during bootstrap.
**Why:** In-progress session turns are not searchable via the sessions channel. A user
asking "what did I say earlier about X?" within a long session gets no sessions-channel
results for the current session.
**Effort:** ~30 LOC — needs either a shutdown reindex pass or an in-memory overlay of the
active session's messages at query time.
**Note:** The in-memory overlay approach avoids writes and is safer for the append-only
transcript invariant.

### Artifacts Channel — Knowledge Recall Improvements

#### G6: Component score exposure for hybrid search diagnostics
**From:** openclaw commit `66e66f19c6` — `vectorScore` and `textScore` exposed alongside
merged score in results.
**Where:** `co_cli/memory/knowledge_store.py` — `_hybrid_search()` return rows.
**Why:** Currently only the merged RRF score is visible. Exposing individual BM25 and vector
scores allows tuning hybrid weights without guesswork. Benefits artifacts and (post-port)
sessions.
**Effort:** ~10 LOC — add fields to the result dataclass and pass through in `_hybrid_search`.

### Backlog Priority Order

| Priority | Item | Effort | Rationale |
| --- | --- | --- | --- |
| 1 | G6 component score exposure | ~10 LOC | Low-risk diagnostic improvement; benefits all chunked sources |
| 2 | G3 parent-session lineage | ~20 LOC | Correctness fix; only urgent when delegation is active |
| 3 | G5 live-session search | ~30 LOC | Real UX gap but safe to defer |
| 4 | G4 CJK trigram sidecar | ~25 LOC | Defer until non-Latin content is a real use case |

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-search-hardening`
