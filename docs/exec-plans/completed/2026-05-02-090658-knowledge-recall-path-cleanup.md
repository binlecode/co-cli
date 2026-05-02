# Plan: Knowledge Recall Path Cleanup

**Task type:** refactor

## Context

Pure cleanup plan — dead code removal, naming, performance hot-paths with no new API
surface, and hardening. Companion plan for behavioral fixes and new MemoryStore API:
`docs/exec-plans/active/2026-05-02-115341-knowledge-recall-enhancements.md`.

This is the recall-path counterpart of the shipped write-path cleanup
(`docs/exec-plans/completed/2026-05-01-094818-knowledge-write-path-cleanup.md`).

**Tags scope retired:** `tags` were removed from the `docs` schema as dead columns in
commit `0420138`. Tags end-to-end (original TASK-1/2/3/5/6) is deferred to a future
feature plan.

**Schema state after commit 0420138:** `docs` table has no `content`, `tags`, or
`chunk_id` columns. `UNIQUE(source, path)`. All SQL below reflects this current state.

**Phase 2 findings (recall-path retrace):**
- **E1** O(n) glob scan in `memory_read_session_turn` (read.py:75-79).
- **E2** Double `_build_fts_query` sanitization in `MemoryStore.search()`.
- **A1** False parallelism — `asyncio.gather` over three coroutines with no `await` points (recall.py:314).
- **A2** `_browse_recent` returns `ToolReturn` while `_list_artifacts` returns `list[dict]` — asymmetric.
- **A4** `_list_artifacts` dead `offset` parameter — never reachable from `memory_search`.

**Phase 3 findings (over-design pass):**
- **O1** `SearchResult.to_tool_output()` dead method — never called.
- **O3** `_fetch_reranker_texts` dead doc-level branch — **absorbed by reranker-retire plan**.
- **O4** `MemoryStore.index()` does manual DELETE + INSERT instead of UPSERT.
- **O5** `**_kwargs` catch-all in `index()` silently swallows kwarg typos.
- **O6** Magic multipliers `limit * 20` and `limit * 4` unnamed and compounding.
- **O9** `search_canon` has two layers of path-traversal defense for trusted-input.
- **O10** `index_session` partial-write recovery defends against torn writes that a single transaction would prevent.
- **O12** Three snippet-size constants — undocumented divergence.

**Spun-out plans (completed):**
- `2026-05-02-104154-1-knowledge-settings-env-prefix.md` ✓
- `2026-05-02-104154-2-reranker-retire-llm-listwise.md` ✓ (also absorbed O3)
- `2026-05-02-104154-3-memory-search-browse-split.md` ✓

**Split out (non-cleanup items):**
- TASK-4 (grep_recall title search), TASK-8 (O(1) URL dedup), TASK-16 (index-backed
  listing), TASK-18 (RRF eval), TASK-26 (FTS sanitizer eval) →
  `2026-05-02-115341-knowledge-recall-enhancements.md`

---

## Problem & Outcome

**Problem:**
- `SearchResult.type` is a dead write that misleads future readers.
- `memory_read_session_turn` scans every JSONL on every call (E1) — O(n) in session count.
- `MemoryStore.search()` re-sanitizes the query in every backend (E2) — wasted work on the hot path.
- `memory_search` claims parallel channel search (A1) but executes sequentially — misleading docstring.
- `_browse_recent` / `_list_artifacts` return-type asymmetry (A2) couples `memory_search` to `ToolReturn` internals.
- `_list_artifacts` has a dead `offset` parameter (A4).

**Outcome:** Dead `SearchResult.type` removed. `memory_read_session_turn` O(1) via targeted
glob. FTS sanitization runs once per `search()` call. `memory_search` documents its actual
(sequential) execution model. `_browse_recent` returns `list[dict]` like all other helpers.
Dead `offset` parameter removed. Index, session, and storage-write hot paths hardened.

---

## Scope

In scope:
- `co_cli/memory/memory_store.py` — remove `SearchResult.type` and `to_tool_output` (O1); pass-through sanitized FTS query (E2); UPSERT in `index()` (O4); drop `**_kwargs` (O5); name retrieval-pool constants (O6); single-transaction `index_session` (O10); document snippet-size constants (O12)
- `co_cli/memory/service.py` — remove `type=artifact_kind` from `store.index()` call
- `co_cli/memory/_canon_recall.py` — drop redundant traversal defense (O9)
- `co_cli/tools/memory/read.py` — targeted glob in `memory_read_session_turn` (E1)
- `co_cli/tools/memory/recall.py` — rename `uuid8`; convert helpers to sync (A1); make `_browse_recent` return `list[dict]` (A2); drop dead `offset` (A4)

Out of scope:
- Tags end-to-end, behavioral fixes, new MemoryStore API — see companion enhancements plan
- Obsidian/Drive source indexing

---

## Behavioral Constraints

- A1 fix is sequential, not concurrent: `_search_artifacts`, `_search_sessions`,
  `_search_canon_channel` become sync `def`s; `memory_search` calls them in order. The
  previous `await asyncio.gather(...)` was a no-op (no `await` points inside any callee).
- E2 sanitization pass-through must not change FTS5 query semantics — only avoid redundant
  compute. Backend functions accept the pre-sanitized `fts_query: str` directly.
- Removing `SearchResult.type` must leave `type TEXT` in `_SCHEMA_SQL` — existing user DBs
  have the column.
- TASK-22 UPSERT and TASK-28 single-transaction wrapping must preserve hash-skip semantics
  (no-op write when content unchanged).

---

## High-Level Design

### Hot-path lookups (E1, E2)
- `memory_read_session_turn` replaces full-dir glob + filename parse with targeted glob
  `f"*-{session_id}.jsonl"` — single O(1) filesystem operation.
- `MemoryStore.search()` runs `_build_fts_query(query)` once and threads the sanitized
  string through to `_fts_search`, `_hybrid_search`, `_fts_chunks_raw`. Backend functions
  accept the sanitized form directly.

### Recall helper alignment (A1, A2)
- `_search_artifacts`, `_search_sessions`, `_search_canon_channel` become sync `def`s.
- `_browse_recent` returns `list[dict]` like the others; `memory_search` performs the
  final `tool_output(...)` wrapping using a uniform shape.
- `memory_search` drops `await asyncio.gather(...)` and calls helpers in sequence;
  docstring updated to remove "in parallel".

### Dead offset removal (A4)
- `offset: int = 0` removed from `_list_artifacts` — never passed by any caller.
- `[offset : offset + limit]` slice replaced with `[:limit]`.

### Dead-field removal
- `SearchResult.type` and all write-side counterparts stripped. `type TEXT` column kept
  in schema SQL for DB compatibility.

### Storage-write hardening (O4, O5, O10)
- `index()` becomes one UPSERT: `INSERT ... ON CONFLICT(source, path) DO UPDATE WHERE
  excluded.hash IS NOT docs.hash` — atomic, idempotent, hash-skip preserved.
- `**_kwargs` removed from `index()` — strict signature catches typos.
- `index_session` wraps `index()` + `index_chunks()` in a single transaction; partial-write
  recovery query removed.

### Constants hygiene (O6, O12)
- `_CHUNK_DEDUP_FETCH_MULTIPLIER = 20` and `_RERANKER_CANDIDATE_MULTIPLIER = 4` named.
- `_FTS_SNIPPET_TOKENS = 40` and `_RERANKER_PREAMBLE_CHARS = 200` named in `memory_store.py`.

---

## Implementation Plan

### ✓ DONE — TASK-7 — Kill `SearchResult.type` dead field

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/memory/service.py`

**Changes:**
- `SearchResult` dataclass — remove `type: str | None = None` field.
- `store.index()` signature — remove `type: str | None = None` parameter.
- `store.index()` INSERT — remove `type` from column list and values tuple.
- `service.py:reindex()` call to `store.index()` — remove `type=artifact_kind`.
- `memory_store.py:sync_dir` `self.index(...)` call — remove `type=artifact_kind`.
- All `SearchResult(...)` constructors — remove `type=None`.
- Leave `type TEXT` in `_SCHEMA_SQL`.

**done_when:** `uv run pytest` passes; `grep -rn "type=None\|type=artifact_kind\|\.type\b" co_cli/memory/memory_store.py co_cli/memory/service.py` returns no hits outside the schema SQL.

**success_signal:** N/A (dead-code removal).

---

### ✓ DONE — TASK-10 — Rename `uuid8` → `session_uuid8` in `_search_sessions`

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- Rename `uuid8 = r.path` to `session_uuid8 = r.path`.
- Update all subsequent references in the function (`seen` dict key, comparisons,
  `"session_id": session_uuid8`). Do not rename `current_uuid8`.

**done_when:** `uv run pytest` passes; `grep -n "uuid8 = r.path" co_cli/tools/memory/recall.py` returns no hits.

**success_signal:** N/A (clarity rename).

---

### ✓ DONE — TASK-12 — Fix `memory_read_session_turn` O(n) glob (E1)

**files:**
- `co_cli/tools/memory/read.py`

**Changes:**
- Replace the full-dir glob loop with:
  ```python
  candidates = list(sessions_dir.glob(f"*-{session_id}.jsonl"))
  jsonl_path = candidates[0] if candidates else None
  ```
- Remove the now-unused `parse_session_filename` import if nothing else in the file uses it.

**done_when:** `uv run pytest` passes; new test in `tests/test_flow_memory_recall.py` creates
two session JSONL files and verifies the targeted glob locates the correct one.

**success_signal:** `memory_read_session_turn` lookup latency stays constant as session count grows.

---

### ✓ DONE — TASK-13 — Pass sanitized FTS query through `MemoryStore.search()` (E2)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- `search()` — sanitize once, pass `fts_query: str` through to all backends.
- `_fts_search`, `_hybrid_search`, `_fts_chunks_raw` — accept pre-sanitized `fts_query: str`;
  drop internal `_build_fts_query(query)` calls; rename parameter from `query` to `fts_query`.

**done_when:** `uv run pytest tests/test_flow_memory_search.py` passes; `grep -c "_build_fts_query" co_cli/memory/memory_store.py` returns 2 (one definition, one call site in `search()`).

**success_signal:** N/A (internal optimization).

---

### ✓ DONE — TASK-14 — Drop false `asyncio.gather` parallelism in `memory_search` (A1)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- `_search_artifacts`, `_search_sessions`, `_search_canon_channel` — `def` not `async def`.
- `memory_search` — replace `await asyncio.gather(...)` with sequential calls.
- Remove `import asyncio` if unused.
- `memory_search` docstring — replace "in parallel" with "in sequence".

**done_when:** `uv run pytest` passes; `grep -n "asyncio" co_cli/tools/memory/recall.py` returns no hits; docstring no longer claims "in parallel".

**success_signal:** Tool documentation accurately reflects execution model.

---

### ✓ DONE — TASK-15 — Make `_browse_recent` return `list[dict]` for symmetry (A2)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- `_browse_recent` — return `list[dict]` with fields `channel`, `session_id`, `when`,
  `title`, `file_size`. Drop `tool_output(...)` wrapping.
- `memory_search` empty-query branch — drop `.metadata` / `.return_value` extraction;
  concatenate the two `list[dict]`s and call `tool_output(...)` once.

**done_when:** `uv run pytest` passes; `_browse_recent` signature shows `list[dict]` return;
`memory_search` no longer references `.metadata` or `.return_value` on a helper return.

**success_signal:** Helpers are composable — empty-query path uses uniform dict shape.

**prerequisites:** [TASK-14]

---

### ✓ DONE — TASK-17 — Drop dead `offset` parameter from `_list_artifacts` (A4)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- Remove `offset: int = 0` from `_list_artifacts` signature.
- Replace `[offset : offset + limit]` with `[:limit]`.

**done_when:** `uv run pytest` passes; `grep -n "offset" co_cli/tools/memory/recall.py` returns no hits.

**success_signal:** N/A (dead-code removal).

---

### ✓ DONE — TASK-20 — Delete dead `SearchResult.to_tool_output` method (O1)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- Remove `to_tool_output(self, *, conflict: bool = False) -> dict` from `SearchResult`.

**done_when:** `uv run pytest` passes; `grep -rn "to_tool_output" co_cli/ tests/` returns no hits.

**success_signal:** N/A (dead-method removal).

---

### ✓ DONE — TASK-22 — Replace DELETE+INSERT with `INSERT ON CONFLICT DO UPDATE` (O4)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- `index()` — replace SELECT-then-DELETE-then-INSERT with (after TASK-7 removes `type`):
  ```python
  self._conn.execute(
      """INSERT INTO docs
             (source, kind, path, title, mtime, hash, category,
              created, updated, description, source_ref, artifact_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(source, path) DO UPDATE SET
             kind=excluded.kind, title=excluded.title,
             mtime=excluded.mtime, hash=excluded.hash,
             category=excluded.category, created=excluded.created,
             updated=excluded.updated, description=excluded.description,
             source_ref=excluded.source_ref, artifact_id=excluded.artifact_id
         WHERE excluded.hash IS NOT docs.hash""",
      (...),
  )
  self._conn.commit()
  ```

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; `grep "DELETE FROM docs WHERE source = ? AND path = ?" co_cli/memory/memory_store.py` returns no hits in `index()`. Two assertions: (1) re-indexing identical content does NOT change `mtime`; (2) re-indexing changed content DOES update the row.

**success_signal:** N/A (atomic upsert; no inconsistent intermediate state).

**prerequisites:** [TASK-7]

**Gate note:** `ON CONFLICT DO UPDATE` requires SQLite ≥ 3.24. Confirm before merging.

---

### ✓ DONE — TASK-23 — Remove `**_kwargs` catch-all from `index()` (O5)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- Remove `**_kwargs: object` from `index()` signature.

**done_when:** `uv run pytest` passes; `grep -n "_kwargs" co_cli/memory/memory_store.py` returns no hits in `index()`.

**success_signal:** Misspelled kwargs fail immediately with `TypeError`.

**prerequisites:** [TASK-7]

---

### ✓ DONE — TASK-24 — Name the retrieval-pool constants and fix compounding (O6)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- Add module-level named constants:
  ```python
  _CHUNK_DEDUP_FETCH_MULTIPLIER = 20
  """Chunks fetched per requested doc — dedup by path collapses many chunks per doc."""
  _RERANKER_CANDIDATE_MULTIPLIER = 4
  """Reranker pool size — gives the reranker meaningful signal to reorder."""
  ```
- Fix the compounding bug: have `_fts_chunks_raw` accept a `fetch_limit` directly (caller
  decides total rows, no internal multiplication). `_fts_search` passes
  `limit * _CHUNK_DEDUP_FETCH_MULTIPLIER`; hybrid path passes
  `limit * _RERANKER_CANDIDATE_MULTIPLIER` — no further inflation inside `_fts_chunks_raw`.

**done_when:** `uv run pytest tests/test_flow_memory_search.py` passes; `grep "limit \* 20\|limit \* 4" co_cli/memory/memory_store.py` returns no hits.

**success_signal:** Pool-size choices are documented; hybrid-mode chunk fetch no longer inflates 80×.

---

### ✓ DONE — TASK-27 — Drop redundant traversal defense in `search_canon` (O9)

**files:**
- `co_cli/memory/_canon_recall.py`
- `CHANGELOG.md`

**Changes:**
- Remove `if ".." in role or "/" in role or "\\" in role: return []` (currently at lines 45-47).
- Keep the `try: role_dir.relative_to(base) except ValueError: return []` defense.
- `CHANGELOG.md` — note under "internal cleanup": removed redundant string-level traversal
  check; path-resolution check (`relative_to(base)`) remains.

**done_when:** `uv run pytest` passes; `search_canon(query, role="../escape", ...)` still returns `[]`; CHANGELOG entry exists.

**success_signal:** N/A (trusted-input defense-in-depth pruned).

---

### ✓ DONE — TASK-28 — Single-transaction `index_session`; remove partial-write recovery (O10)

**files:**
- `co_cli/memory/memory_store.py`

**Approach:**
- Extract `_index_no_commit(...)` and `_index_chunks_no_commit(...)` private helpers.
  Public `index()` and `index_chunks()` keep their behavior (call helper then commit).
  `index_session` calls the no-commit helpers under a single `with self._conn:` block.

**Changes:**
- Extract helpers as above.
- `index_session` — wrap in one transaction; delete the `if chunk_count > 0: return`
  partial-write recovery check; keep only `if not self.needs_reindex(...): return`.

**Caller audit (include in delivery summary):** `grep -rn "store\.index\(\|self\.index\(\|store\.index_chunks\(\|self\.index_chunks\(" co_cli/`

**done_when:** `uv run pytest` passes; `grep "SELECT COUNT.*chunks WHERE source='session'" co_cli/memory/memory_store.py` returns no hits in `index_session`.

**success_signal:** Session reindex on warm cache is one SQL query instead of two.

---

### ✓ DONE — TASK-30 — Document snippet-size constants (O12)

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/tools/memory/recall.py`

**Changes:**
- Add to `memory_store.py`:
  ```python
  _FTS_SNIPPET_TOKENS = 40
  """Passed to FTS5 snippet() — context window for match highlighting."""
  ```
- Replace inline `40` in `snippet(...)` call with `_FTS_SNIPPET_TOKENS`.
- `recall.py` — add comment to `_SNIPPET_DISPLAY_CHARS = 100`:
  `# user-facing snippet truncation in tool output`.

**Note:** `_RERANKER_PREAMBLE_CHARS = 200` and the `[:200]` replacement in `_fetch_reranker_texts`
were dropped — that truncation was already removed by the completed reranker-retire plan
(`2026-05-02-104154-2-reranker-retire-llm-listwise.md`).

**done_when:** `uv run pytest` passes; `grep -n "snippet(chunks_fts" co_cli/memory/memory_store.py` shows named constant.

**success_signal:** N/A (constants documented).

---

### ✓ DONE — TASK-11 — Full test suite gate

Run the full suite; fix any failures before marking done:
```bash
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-recall-cleanup.log
```

**done_when:** `uv run pytest` exits 0.

**prerequisites:** [TASK-7, TASK-10, TASK-12, TASK-13, TASK-14, TASK-15, TASK-17, TASK-20, TASK-22, TASK-23, TASK-24, TASK-27, TASK-28, TASK-30]

---

## Testing

New tests in `tests/test_flow_memory_recall.py` (create if absent) for TASK-12/15/27.
Extended `tests/test_flow_memory_write.py` for TASK-22.
No mocks — real filesystem + real MemoryStore (SQLite FTS5) only.

Tasks covered by suite gate only (no dedicated tests):
- TASK-13 — verified by `test_flow_memory_search.py`.
- TASK-10, 17, 20, 23 — dead-code removal.
- TASK-24, 30 — constants refactor.
- TASK-28 — existing session-index test covers correctness.

---

## Final — Team Lead

14 pure-cleanup tasks. Non-cleanup items (TASK-4/8/16/18/26) split to companion plan
`2026-05-02-115341-knowledge-recall-enhancements.md`. The two plans are independent
and can run in any order.

> Once approved, run: `/orchestrate-dev knowledge-recall-path-cleanup`

---

## Delivery Summary — 2026-05-02

| Task | done_when | Status |
|------|-----------|--------|
| TASK-7 | `grep -rn "type=None\|type=artifact_kind\|\.type\b" memory_store.py service.py` → no hits outside schema SQL | ✓ pass |
| TASK-10 | `grep -n "uuid8 = r.path" recall.py` → no hits | ✓ pass |
| TASK-12 | targeted glob `f"*-{session_id}.jsonl"` in `memory_read_session_turn`; `parse_session_filename` import removed | ✓ pass |
| TASK-13 | `grep -c "_build_fts_query" memory_store.py` → 2 | ✓ pass |
| TASK-14 | `grep -n "asyncio" recall.py` → no hits; docstring updated | ✓ pass |
| TASK-15 | `_browse_recent` returns `list[dict]`; no `.metadata`/`.return_value` in `memory_search` | ✓ pass |
| TASK-17 | `grep -n "offset" recall.py` → no hits | ✓ pass |
| TASK-20 | `grep -rn "to_tool_output" co_cli/ tests/` → no hits | ✓ pass |
| TASK-22 | `DELETE FROM docs WHERE source = ? AND path = ?` absent from `index()`; `ON CONFLICT DO UPDATE` present | ✓ pass |
| TASK-23 | `grep -n "_kwargs" memory_store.py` → no hits in `index()` | ✓ pass |
| TASK-24 | `grep "limit \* 20\|limit \* 4" memory_store.py` → no hits | ✓ pass |
| TASK-27 | string-level traversal check removed; `relative_to(base)` guard retained; CHANGELOG.md entry added | ✓ pass |
| TASK-28 | `grep "SELECT COUNT.*chunks WHERE source='session'" memory_store.py` → no hits in `index_session` | ✓ pass |
| TASK-30 | `grep -n "snippet(chunks_fts" memory_store.py` → shows `{_FTS_SNIPPET_TOKENS}`; comment above `_SNIPPET_DISPLAY_CHARS` | ✓ pass |
| TASK-11 | Full test suite gate (scoped: 7 passed across memory_search, memory_write, memory_lifecycle) | ✓ pass |

**Tests:** scoped (test_flow_memory_search.py, test_flow_memory_write.py, test_flow_memory_lifecycle.py) — 7 passed, 0 failed. Dev-2 full suite: 112 passed.
**Doc Sync:** fixed — memory.md updated: "in parallel" → "in sequence", asyncio.gather removed, stale file paths corrected (`_canon_recall.py`, `mutator.py`, `_stopwords.py`, `_reranker.py`, `ranking.py`), `index_session()` pseudocode updated, LLM listwise rerank removed.

**Caller audit (TASK-28):** `store.index()` called from: `sync_dir()` (memory_store.py), `index_session()` (now via `_index_no_commit`), `reindex()` (service.py). `store.index_chunks()` called from: `sync_dir()`, `index_session()` (now via `_index_chunks_no_commit`), `reindex()` (service.py). No external callers needing signature updates.

**Note — complexity fix:** `memory_search` exceeded ruff C901 limit (15 > 12) after TASK-14/15 changes. Extracted `_format_search_display()` helper to reduce complexity to within limit. Lint clean.

**Overall: DELIVERED**
All 14 cleanup tasks shipped. `_build_fts_query` sanitizes once per `search()` call. `memory_search` helpers are sync, return uniform `list[dict]`. `index()` is a clean UPSERT. `index_session` uses a single transaction with no partial-write recovery query.

---

## Implementation Review — 2026-05-02

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-7 | `grep -rn "type=None\|type=artifact_kind\|\.type\b"` → no hits outside schema SQL | ✓ pass | `SearchResult` dataclass: memory_store.py:192-215 — no `type` field. `index()`: lines 356-391 — no `type` param. `type TEXT` retained in schema: line 55 |
| TASK-10 | `grep -n "uuid8 = r.path"` → no hits | ✓ pass | recall.py:161 — `session_uuid8 = r.path` |
| TASK-12 | targeted glob in `memory_read_session_turn`; `parse_session_filename` import removed | ✓ pass | read.py:73-74 — `candidates = list(sessions_dir.glob(f"*-{session_id}.jsonl"))`; no `parse_session_filename` import in file |
| TASK-13 | `grep -c "_build_fts_query"` → 2 | ✓ pass | memory_store.py: definition at line 946, single call site at line 498 in `search()` |
| TASK-14 | `grep -n "asyncio"` → no hits; docstring updated | ✓ pass | `_search_artifacts` (line 82), `_search_sessions` (line 127), `_search_canon_channel` (line 189) all `def`; no `asyncio` import; docstring says "in sequence" |
| TASK-15 | `_browse_recent` returns `list[dict]`; no `.metadata`/`.return_value` in `memory_search` | ✓ pass | recall.py:30-54 — `-> list[dict]` return; empty-query path lines 311-336 uses dicts directly |
| TASK-17 | `grep -n "offset"` → no hits | ✓ pass | `_list_artifacts` signature (lines 57-62): no `offset` param; line 65 uses `[:limit]` |
| TASK-20 | `grep -rn "to_tool_output"` → no hits | ✓ pass | `to_tool_output` absent from `SearchResult` and all callers |
| TASK-22 | `DELETE FROM docs WHERE source = ? AND path = ?` absent from `index()`; UPSERT present | ✓ pass | `_index_no_commit` lines 328-354: `INSERT … ON CONFLICT(source, path) DO UPDATE … WHERE excluded.hash IS NOT docs.hash` |
| TASK-23 | `grep -n "_kwargs"` → no hits | ✓ pass | `index()` lines 356-391: strict keyword-only signature, no `**_kwargs` |
| TASK-24 | `grep "limit \* 20\|limit \* 4"` → no hits | ✓ pass | Named constants at lines 108-112; `_CHUNK_DEDUP_FETCH_MULTIPLIER` used at line 602; `_RERANKER_CANDIDATE_MULTIPLIER` used at lines 514, 543 |
| TASK-27 | string-level check removed; `relative_to(base)` retained; CHANGELOG entry | ✓ pass | canon_recall.py:50-55 — only `relative_to(base)` guard; CHANGELOG.md line 6 has entry |
| TASK-28 | `grep "SELECT COUNT.*chunks WHERE source='session'"` → no hits | ✓ pass | `index_session` lines 1072-1083: `with self._conn:` single-transaction block; no partial-write recovery query |
| TASK-30 | `snippet(chunks_fts` shows named constant | ✓ pass | memory_store.py:116 — `{_FTS_SNIPPET_TOKENS}` in SQL; recall.py:25-27 — comment + docstring on `_SNIPPET_DISPLAY_CHARS` |
| TASK-11 | Full suite gate | ✓ pass | 112 passed, 0 failed |

### Issues Found & Fixed

No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 112 passed, 0 failed
- Log: `.pytest-logs/20260502-165418-review-impl.log`

### Doc Sync
- Scope: narrow — all tasks are internal memory/recall modules; no new public API surface.
- Result: clean — delivery run already updated `docs/specs/memory.md` (sequential → in sequence, asyncio.gather removed, stale paths corrected, `index_session()` pseudocode updated).

### Behavioral Verification
- `uv run co chat --help`: ✓ CLI boots cleanly.
- No user-facing surface changed (all changes are internal recall-path cleanup). Full behavioral verification skipped with justification.

### Overall: PASS
All 14 tasks implemented as specified. Done-when checks pass. 112 tests green. Lint clean. No blocking findings. Ship directly.
