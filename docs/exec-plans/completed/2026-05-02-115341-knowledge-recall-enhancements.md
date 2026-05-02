# Plan: Knowledge Recall Enhancements

**Task type:** code-feature

## Context

Companion to `2026-05-02-090658-knowledge-recall-path-cleanup.md`. Contains the five items
split out of that plan because they add new behavior or new public API surface rather than
purely cleaning up existing code:

- **TASK-4** — `grep_recall` title search: changes what the fallback returns (new behavior).
- **TASK-8** — O(1) URL dedup: adds `find_by_source_ref` to `MemoryStore` and threads
  `memory_store` through `save_artifact` (new public API + write-path change).
- **TASK-16** — Index-backed artifact listing: adds `list_artifacts` to `MemoryStore`
  (new public API + performance change to empty-query recall path).
- **TASK-18** — RRF aggregation eval: may switch `max` → `sum` in `_hybrid_merge` (eval-
  gated behavior change).
- **TASK-26** — FTS sanitizer eval: may simplify `sanitize_fts5_query` (eval-gated
  behavior change).

**Schema state:** `docs` table has no `content`, `tags`, or `chunk_id` columns.
`UNIQUE(source, path)`. All SQL below reflects this current state.

**Independence:** The two plans are independent — neither is a prerequisite of the other.

---

## Problem & Outcome

**Problem:**
- `grep_recall` (FTS fallback) only searches `content` — misses artifacts matched only by
  title. Docstring claims "content and tags" but the filter is content-only.
- `_find_article_by_url` scans every `.md` file on every web-article save — O(n)
  degradation as the knowledge store grows.
- `_list_artifacts` loads every `.md` file from disk on every empty-query `memory_search`
  call, even when the index is warm.
- `_hybrid_merge` doc-level aggregation (`max`) is undocumented and may underrank documents
  with broad query coverage — no eval evidence either way.
- `sanitize_fts5_query` 6-step regex pipeline defends against typing errors that
  LLM-issued queries don't make in practice — possibly over-engineered.

**Outcome:** `grep_recall` matches by title. `_find_article_by_url` is O(1) when the
index is available. Empty-query `memory_search` no longer reads every `.md` file when the
index is warm. RRF aggregation choice is justified by eval data and documented.
`sanitize_fts5_query` is either simplified (if eval confirms safety) or explicitly justified.

---

## Scope

In scope:
- `co_cli/tools/memory/read.py` — `grep_recall` title search (TASK-4)
- `co_cli/memory/memory_store.py` — `find_by_source_ref`, `list_artifacts` new methods (TASK-8, TASK-16); `_hybrid_merge` aggregation (TASK-18)
- `co_cli/memory/service.py` — `memory_store` parameter in `_find_article_by_url` and `save_artifact` (TASK-8)
- `co_cli/tools/memory/write.py` — thread `memory_store` from `memory_create` (TASK-8)
- `co_cli/tools/memory/recall.py` — `_list_artifacts` index-backed path (TASK-16)
- `co_cli/memory/search_util.py` — `sanitize_fts5_query` trim (TASK-26)
- `evals/` — eval runs for TASK-18 and TASK-26
- `docs/` — REPORT artifacts for TASK-18 and TASK-26

Out of scope:
- Tags end-to-end (deferred; `KnowledgeArtifact` has no `tags` field)
- Pure cleanup items — see companion cleanup plan

---

## Behavioral Constraints

- `save_artifact` must remain RunContext-free; `memory_store` is an optional parameter
  defaulting to `None`. File-scan fallback must be preserved.
- Index-stale edge case: when a URL exists on disk but not in the index, a duplicate
  article may be created. Accepted tradeoff — `reindex()` is always called after every
  save in the tool layer.
- A5 (RRF aggregation) must be decided by eval data. If `sum` and `max` produce
  indistinguishable recall@k, keep `max` and document the choice explicitly.
- O8 (`sanitize_fts5_query` simplification) is eval-gated before adoption. Either branch
  produces a REPORT.

---

## High-Level Design

### grep_recall title search (TASK-4)
- Add `(m.title or "").lower()` check to the existing content filter. No tags (no
  `KnowledgeArtifact.tags` field exists).
- Docstring updated to "Case-insensitive substring search across title and content."

### O(1) URL dedup (TASK-8)
- `MemoryStore.find_by_source_ref(source_ref, source)` — single SQL lookup on `docs`.
- `_find_article_by_url` gains `memory_store: MemoryStore | None = None`; delegates to
  `find_by_source_ref` when available, falls back to file-scan.
- `save_artifact` gains `memory_store: MemoryStore | None = None`; threads it to
  `_find_article_by_url`.
- `memory_create` passes `ctx.deps.memory_store`.

### Index-backed listing (TASK-16)
- `MemoryStore.list_artifacts(kinds, limit)` — queries `docs` joined with `chunks` on
  `chunk_index=0` for snippets; sorted `created DESC`; returns `list[dict]`.
- `_list_artifacts` delegates to `store.list_artifacts(...)` when `memory_store is not
  None`; falls back to the existing disk scan.

### RRF aggregation eval (TASK-18)
- Run existing recall eval twice: current `max` vs `sum`. Compare recall@k. Decision rule:
  `sum` improves ≥ 5% → adopt; otherwise keep `max` with documenting comment.
- Produces `docs/REPORT-rrf-aggregation-<date>.md`.

### FTS sanitizer eval (TASK-26)
- Run existing search eval twice: current 6-step vs stripped 3-step (steps 1, 2, 6 only).
- Decision rule: stripped matches or improves → adopt; regresses → keep with justification comment.
- Produces `docs/REPORT-fts-sanitize-<date>.md`.

---

## Implementation Plan

### ✓ DONE — TASK-4 — Fix `grep_recall` to search title

**files:**
- `co_cli/tools/memory/read.py`
- `tests/test_flow_memory_recall.py` (create or extend)

**Changes:**
- Replace the single `content` check:
  ```python
  matches = [
      m for m in artifacts
      if query_lower in m.content.lower()
      or query_lower in (m.title or "").lower()
  ]
  ```
- Docstring — update to: "Case-insensitive substring search across title and content."

**done_when:** `uv run pytest` passes; a test in `tests/test_flow_memory_recall.py`
verifies `grep_recall` returns an artifact matched by title-only (body doesn't contain query).

**success_signal:** Agents in FTS-fallback mode can discover artifacts by title.

---

### ✓ DONE — TASK-8 — Fix `_find_article_by_url` O(n) scan

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/memory/service.py`
- `co_cli/tools/memory/write.py`

**Changes:**
- `MemoryStore` — add public method:
  ```python
  def find_by_source_ref(self, source_ref: str, source: str) -> str | None:
      """Return the path of the doc with the given source_ref, or None."""
      row = self._conn.execute(
          "SELECT path FROM docs WHERE source = ? AND source_ref = ?",
          (source, source_ref),
      ).fetchone()
      return row["path"] if row else None
  ```
- `_find_article_by_url` — add `memory_store: "MemoryStore | None" = None`; when present:
  ```python
  result = memory_store.find_by_source_ref(origin_url, IndexSourceEnum.KNOWLEDGE)
  return Path(result) if result else None
  ```
  Keep existing file-scan as `else` fallback.
- `save_artifact` — add `memory_store: "MemoryStore | None" = None`; thread to
  `_find_article_by_url(knowledge_dir, source_url, memory_store=memory_store)`.
- `memory_create` — add `memory_store=ctx.deps.memory_store` to `save_artifact` call.

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes. Two extended tests:
1. Second `save_artifact(source_url=URL, memory_store=store)` after indexing returns
   `result.action == "merged"` (O(1) index path exercised).
2. `_find_article_by_url(knowledge_dir, url, memory_store=None)` returns correct path
   (file-scan fallback exercised).

**success_signal:** Repeated `memory_create` calls with the same `source_url` do not degrade as the knowledge store grows.

---

### ✓ DONE — TASK-16 — `_list_artifacts` uses MemoryStore when available

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/tools/memory/recall.py`

**Changes:**
- `MemoryStore` — add public method:
  ```python
  def list_artifacts(self, kinds: list[str] | None, limit: int) -> list[dict]:
      kind_sql = ""
      kind_params: list = []
      if kinds is not None:
          ph = ",".join("?" * len(kinds))
          kind_sql = f" AND d.kind IN ({ph})"
          kind_params = list(kinds)
      rows = self._conn.execute(
          f"""SELECT d.path, d.kind, d.title, d.created,
                     c.content AS snippet
              FROM docs d
              LEFT JOIN chunks c
                ON c.doc_path = d.path AND c.source = d.source AND c.chunk_index = 0
              WHERE d.source = 'knowledge'{kind_sql}
              ORDER BY d.created DESC LIMIT ?""",
          [*kind_params, limit],
      ).fetchall()
      return [
          {
              "channel": "artifacts",
              "kind": row["kind"],
              "title": row["title"] or Path(row["path"]).stem,
              "snippet": (row["snippet"] or "")[:100],
              "score": 0.0,
              "path": row["path"],
              "filename_stem": Path(row["path"]).stem,
          }
          for row in rows
      ]
  ```
- `_list_artifacts` — when `ctx.deps.memory_store is not None`, delegate to
  `ctx.deps.memory_store.list_artifacts(kinds, limit)`; fallback to existing disk scan.

**done_when:** `uv run pytest` passes. Two tests in `tests/test_flow_memory_recall.py`:
1. Seeds knowledge dir + index with three artifacts; calls `_list_artifacts` with
   `memory_store` set; verifies dicts match index (sorted by created desc, limited correctly).
2. Calls with `memory_store=None`; verifies disk-scan fallback works.

**success_signal:** Empty-query `memory_search` no longer reads every `.md` file from disk when the index is warm.

---

### ✓ DONE — TASK-18 — Evaluate `_hybrid_merge` doc-level aggregation: max vs sum

**files:**
- `co_cli/memory/memory_store.py`
- `evals/` (existing recall eval scripts)

**Changes:**
- Run an existing recall eval (`ls evals/eval_*recall*.py`) twice on the same dataset:
  1. Baseline — current `doc_rrf[path] = max(doc_rrf.get(path, 0.0), score)`.
  2. Variant — `doc_rrf[path] = doc_rrf.get(path, 0.0) + score`.
- Decision rule:
  - `sum` improves recall@k ≥ 5% → adopt, update `_hybrid_merge` docstring.
  - Within ±5% → keep `max`, add inline comment documenting the intentional choice.
  - `sum` regresses → keep `max`, add same comment.
- Produces `docs/REPORT-rrf-aggregation-<date>.md`.

**done_when:** `docs/REPORT-rrf-aggregation-*.md` exists; code updated or commented; `uv run pytest` passes.

**success_signal:** RRF aggregation choice is justified by data, not historical accident.

---

### ✓ DONE — TASK-26 — Eval-driven trim of `sanitize_fts5_query`

**files:**
- `co_cli/memory/search_util.py`
- `evals/` (existing FTS / search eval scripts)

**Changes:**
- Run an existing search eval against the current 6-step pipeline. Capture baseline.
- Try stripped variant: keep step 1 (quote protection), step 2 (operator stripping),
  step 6 (quote restoration). Drop steps 3–5.
- Decision rule: stripped matches or improves → adopt; regresses → keep with justification comment.
- Produces `docs/REPORT-fts-sanitize-<date>.md`.

**done_when:** REPORT exists; function trimmed or comment added; `uv run pytest` passes.

**success_signal:** N/A (internal hardening / simplification).

---

### ✓ DONE — TASK-11 — Full test suite gate

```bash
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-recall-enhancements.log
```

**done_when:** `uv run pytest` exits 0.

**prerequisites:** [TASK-4, TASK-8, TASK-16, TASK-18, TASK-26]

---

## Testing

New/extended tests in `tests/test_flow_memory_recall.py` for TASK-4/16 and
`tests/test_flow_memory_write.py` for TASK-8. No mocks — real filesystem + real
MemoryStore (SQLite FTS5) only.

TASK-18 and TASK-26 produce REPORT artifacts, not unit tests.

---

## Delivery Summary — 2026-05-02

| Task | done_when | Status |
|------|-----------|--------|
| TASK-4 | `uv run pytest tests/test_flow_memory_recall.py` passes; title-only match test added | ✓ pass |
| TASK-8 | `uv run pytest tests/test_flow_memory_write.py` passes; index path + file-scan fallback both tested | ✓ pass |
| TASK-16 | `uv run pytest` passes; index-backed path and disk-scan fallback both tested | ✓ pass |
| TASK-18 | `docs/REPORT-rrf-aggregation-20260502.md` exists; `max` kept with justification comment | ✓ pass |
| TASK-26 | `docs/REPORT-fts-sanitize-20260502.md` exists; 6-step pipeline kept with per-step inline comments | ✓ pass |
| TASK-11 | `uv run pytest -x` exits 0 | ✓ pass |

**Tests:** full suite — 120 passed, 0 failed
**Doc Sync:** fixed — `memory.md`: `grep` backend description updated to "title and content"; `read.py` file description disambiguated; 3 new test gate rows added

**Overall: DELIVERED**
All 5 enhancement tasks shipped. TASK-18 and TASK-26 both resolved to "keep current" via eval data (`max` justified, 6-step FTS sanitizer retained); TASK-4/8/16 delivered clean code changes with real behavioral tests.

---

## Final — Team Lead

5 enhancement tasks split from recall-path-cleanup plan. Independent of that plan —
can run in any order.

> Once approved, run: `/orchestrate-dev knowledge-recall-enhancements`

---

## Implementation Review — 2026-05-02

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-4 | `pytest` passes; title-only match test added | ✓ pass | `read.py:39` — `query_lower in (m.title or "").lower()` added to filter; docstring at `:31` updated to "title and content" |
| TASK-8 | `pytest tests/test_flow_memory_write.py` passes; index path + fallback tested | ✓ pass | `memory_store.py:475-481` — `find_by_source_ref` method; `service.py:78-93` — `_find_article_by_url` delegates to index first; `service.py:152` — threads to `save_artifact`; `write.py:88` — `memory_store=ctx.deps.memory_store` passed |
| TASK-16 | `pytest` passes; index-backed and disk-scan fallback both tested | ✓ pass | `memory_store.py:483-512` — `list_artifacts` SQL confirmed; `recall.py:64-65` — delegates to `store.list_artifacts(kinds, limit)` when store present |
| TASK-18 | REPORT exists; `max` kept with comment | ✓ pass | `docs/REPORT-rrf-aggregation-20260502.md`; `memory_store.py:856-858` — comment: "eval 2026-05-02 showed 0% recall@2 delta" |
| TASK-26 | REPORT exists; 6-step pipeline kept with per-step comments | ✓ pass | `docs/REPORT-fts-sanitize-20260502.md` (32/34 pass, 3-step 19/34); `search_util.py:66-103` — per-step justification comments |
| TASK-11 | full `pytest` exits 0 | ✓ pass | 120 passed, 0 failed |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Docstring says "35-query set" but REPORT shows 34 queries | `search_util.py:63` | minor | Changed "35" → "34" |
| `list_artifacts` duplicated `_kind_clause` logic inline instead of reusing helper | `memory_store.py:483` | minor | Replaced 5-line inline block with `_kind_clause(kinds, "d.kind")` |

### Tests

- Command: `uv run pytest -v`
- Result: 120 passed, 0 failed
- Log: `.pytest-logs/20260502-*-review-impl.log`

### Doc Sync

- Scope: narrow — `docs/specs/memory.md` only (already complete from delivery)
- Result: clean — grep backend row updated to "title and content", `read.py` description updated, 3 test gate rows added

### Behavioral Verification

- `uv run co chat`: no `status` command in this project; verified via `python -c` smoke run:
  - `grep_recall("rrf scoring")` on title-only artifact → 1 hit, correct title ✓
  - `find_by_source_ref(url, 'knowledge')` after indexing → path returned ✓
  - `list_artifacts(None, 10)` → `channel='artifacts'` result ✓
  - `sanitize_fts5_query` output matches REPORT (e.g. `"chat-send"` → `"chat-send"`) ✓

### Overall: PASS

All 5 enhancement tasks delivered correctly. Two minor doc/style issues auto-fixed during review. Full test suite green.
