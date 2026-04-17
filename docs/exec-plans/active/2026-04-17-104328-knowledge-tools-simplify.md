# Plan: Knowledge Tools — Simplification & Hardening

**Task type: refactor (no behavior change to public tool schemas)**

## Context

Last 5 deliveries (v0.7.180–v0.7.187) consolidated knowledge/memory/article tool surfaces
into `co_cli/tools/knowledge.py` and moved the extractor into `co_cli/knowledge/`. Today's
delivery (v0.7.187) also renamed `knowledge/_extractor.py` → `knowledge/_distiller.py` and
`memory/_extractor.py` → `memory/_indexer.py`.

The consolidation is correct but left behind 4 classes of technical debt identified by a
cross-delivery simplify review:

1. **Duplicate patterns** — atomic write, store.index + chunk_text, window-building logic
   each appear 3–4 times verbatim.
2. **Stringly-typed source values** — `"knowledge"`, `"obsidian"`, `"drive"` scattered as
   raw literals; no `IndexSourceEnum` to match the existing `ArtifactKindEnum` pattern.
3. **Efficiency regressions** — `_count_active_artifacts()` called inside a hot loop,
   O(n²) clustering with no early exit, dual-glob on read, per-turn full artifact scan.
4. **Abstraction leakage** — `_update_artifact_body` and `_reindex_knowledge_file` are
   knowledge-layer mutations living in the tools facade.

**Current-state validation (2026-04-17):**
- TASK-1: `NamedTemporaryFile + os.replace` still at lines 64–68, 141–145, 1120–1124, 1216–1220 of `tools/knowledge.py`; `write_text` at lines 452, 701, 1037. All unshipped.
- TASK-2: `_reindex_knowledge_file` exists (line 73) but bypassed by `save_knowledge` (lines 457–477), `save_article` (706–724), `_consolidate_and_reindex` (1042–1061). Unshipped.
- TASK-3: `IndexSourceEnum` not in `_artifact.py`; raw `"knowledge"` literals still at ~12 sites. Unshipped.
- TASK-4: `_build_window` in `_distiller.py:83`; `_build_dream_window` in `_dream.py:120`. Both still separate. Unshipped. (File reference updated from old `_extractor.py`.)
- TASK-5: `_update_artifact_body` and `_reindex_knowledge_file` still in `tools/knowledge.py:54–113`. `mutator.py` does not exist. Unshipped.
- TASK-6: `_count_active_artifacts` called at lines 208, 217, 233 of `_dream.py` — post-loop call still present. Unshipped.
- TASK-7: `_cluster_by_similarity` loop at lines 287–290 has no early-skip guard. Unshipped.
- TASK-8: Dual-glob in `read_article` at lines 951–954 still present. Unshipped. (Fix B dropped — see note below.)
- TASK-9: `_load_personality_memories` has no cache; calls `load_knowledge_artifacts` on every invocation. Unshipped.

**Fix B removed from TASK-8:** The original plan proposed removing the `if not knowledge_dir.exists(): return None` guard from `_find_article_by_url`, claiming `Path.glob()` is safe on missing dirs. This is correct for Python 3.13+ but **not Python 3.12**: in 3.12, `Path.glob()` raises `FileNotFoundError` when the base directory does not exist. Removing the guard would change `return None` to `raise FileNotFoundError`. TASK-8 now covers only the dual-glob fix in `read_article`.

**TASK-10 dropped:** The original plan claimed `group_by_turn` is called multiple times per compaction trigger. Code audit shows `summarize_history_window` calls `_compute_compaction_boundaries` once, which calls `group_by_turn` once. `_gather_file_paths` scans `ToolCallPart.args`, not turn groups — refactoring it to accept `list[TurnGroup]` would require equivalent iteration work. The other `group_by_turn` callers (`recover_overflow_history`, `emergency_compact`) are on distinct code paths and don't run in the same trigger. The optimization premise does not hold.

No public tool schema changes. No LLM behavior changes. Full test suite must pass after every task.

---

## Recommended Order

```
TASK-7 (5 min)   — O(n²) clustering early-skip guard
TASK-6 (10 min)  — drop redundant _count_active_artifacts call inside loop
TASK-8 (10 min)  — dual-glob fix in read_article
TASK-3 (20 min)  — IndexSourceEnum in _artifact.py; replace all source literals
TASK-1 (20 min)  — extract _atomic_write helper
TASK-4 (20 min)  — unify _build_window / _build_dream_window
TASK-2 (30 min)  — route all callers to _reindex_knowledge_file
TASK-9 (20 min)  — cache + invalidation hook in _load_personality_memories
TASK-5 (30 min)  — move _update_artifact_body + _reindex_knowledge_file to mutator.py
```

Tasks 1–4, 6–9 are independent. TASK-5 requires TASK-1 and TASK-2.

---

## Tasks

### ✓ DONE — TASK-1 — Extract `_atomic_write` helper; use in all write paths

**Problem:** `tempfile.NamedTemporaryFile + os.replace` appears verbatim at 4 sites in
`tools/knowledge.py` (lines 64–68, 141–145, 1120–1124, 1216–1220). Three additional
`path.write_text()` calls (lines 452, 701, 1037) are not atomic. Centralizing all five
write paths under one helper enforces the invariant and prevents `.tmp` orphans on crash.

**Failure cost:** A crash between `NamedTemporaryFile` write and `os.replace` leaves a
`.tmp` orphan in the knowledge dir. Duplicate code means one copy can diverge silently.

**Files:** `co_cli/tools/knowledge.py`

**Implementation:**

```python
def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via a sibling .tmp file."""
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
    os.replace(tmp.name, path)
```

Replace each inline `NamedTemporaryFile + os.replace` block and each `path.write_text()`
call with `_atomic_write(path, content)`.

**done_when:** `grep -n "NamedTemporaryFile\|\.write_text" co_cli/tools/knowledge.py` shows
zero matches outside `_atomic_write` itself (verify by inspection, not just count — grep
can match comments). Full test suite passes.

**success_signal:** N/A (internal refactor, no user-visible change)

**prerequisites:** []

---

### ✓ DONE — TASK-2 — Unify store.index + chunk_text into `_reindex_knowledge_file`

**Problem:** `_reindex_knowledge_file` (line 73) exists but is bypassed — `save_knowledge`
(lines 457–477), `save_article` (706–724), and `_consolidate_and_reindex` (1042–1061) each
inline their own `store.index + chunk_text` copy. `chunk_text` is also imported inside
three local `if store is not None` blocks instead of at module level.

**Failure cost:** FTS indexing behavior can silently diverge between the four callers; a
bug fix in `_reindex_knowledge_file` won't reach the three callers that bypass it.

**Files:** `co_cli/tools/knowledge.py`

**Steps:**

0. **Schema audit (do this before any edits):** Compare the `store.index()` keyword
   arguments used in `save_knowledge` (line ~458: includes `type=artifact_kind`) against
   `_reindex_knowledge_file`'s `store.index()` call (lines ~93–105). Confirm that `type=`
   and `kind=` are the same column, or that `type=artifact_kind` in `save_knowledge` is
   redundant (not present in `_reindex_knowledge_file` but also not stored separately).
   Document the finding as an inline comment before proceeding. If `type=` is a distinct
   field, add it to `_reindex_knowledge_file` — do not silently drop it.

1. Move `from co_cli.knowledge._chunker import chunk_text` to module-level imports; remove
   the three inline import lines (currently at approx lines 106, 470, 717, 1054).
2. In `save_knowledge` (lines 454–477), replace the inline store.index + chunk_text block
   with `_reindex_knowledge_file(ctx, file_path, content, file_content, fm_dict, slug)`.
   `file_content` is the rendered markdown; pass it as `md_content`. Remove the now-dead
   `content_hash = hashlib.sha256(file_content.encode()).hexdigest()` line — `_reindex_knowledge_file`
   computes the hash internally.
3. In `save_article` (lines 704–726), replace the try/except store.index + chunk_text block
   with `_reindex_knowledge_file(ctx, file_path, content, md_content, fm_dict, slug)`.
   Keep the same try/except + `logger.warning(...)` wrapper the caller already has.
4. In `_consolidate_and_reindex` (lines 1040–1063), same replacement.
5. Confirm `_reindex_knowledge_file` signature handles all three callers:
   `(ctx, path, body, md_content, fm, slug)` where `fm` is `dict[str, Any]`.

**Note on TASK-3 interaction:** After TASK-3 lands, any `store.index(source="knowledge", ...)`
remaining here should use `IndexSourceEnum.KNOWLEDGE`. If TASK-2 lands before TASK-3,
update the literals when TASK-3 runs — do not introduce new raw source strings.

**done_when:** `grep -n "store\.index\|chunk_text\|index_chunks" co_cli/tools/knowledge.py`
shows matches only inside `_reindex_knowledge_file` itself. Full test suite passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

### ✓ DONE — TASK-3 — Add `IndexSourceEnum` to `_artifact.py`; replace all source literals

**Problem:** Raw strings `"knowledge"`, `"obsidian"`, `"drive"` appear at ~12 sites as magic
literals in `tools/knowledge.py`. `ArtifactKindEnum` and `SourceTypeEnum` already exist in
`_artifact.py` — the FTS source namespace needs the same treatment.

**Failure cost:** A typo in a source literal silently returns empty results; the type
system cannot catch it. New sources added to `KnowledgeStore` won't be caught at call sites.

**Files:** `co_cli/knowledge/_artifact.py`, `co_cli/tools/knowledge.py`

**Steps:**

1. In `co_cli/knowledge/_artifact.py`, add after `SourceTypeEnum`:
   ```python
   class IndexSourceEnum(StrEnum):
       KNOWLEDGE = "knowledge"
       OBSIDIAN = "obsidian"
       DRIVE = "drive"
   ```

2. In `co_cli/tools/knowledge.py`, add `IndexSourceEnum` to the import from `_artifact`.

3. Replace every raw `"knowledge"` / `"obsidian"` / `"drive"` used as a `source=` argument
   or in source comparisons with `IndexSourceEnum.KNOWLEDGE` / `.OBSIDIAN` / `.DRIVE`. Key sites:
   - All `store.index(source="knowledge", ...)` calls (lines ~94, 457, 707, 1043)
   - All `store.index_chunks("knowledge", ...)` calls (lines ~113, 477, 724, 1061)
   - `source not in (None, "knowledge")` guard in `_grep_fallback_knowledge`
   - `source in (None, "obsidian")` guard in `search_knowledge`
   - `ctx.deps.knowledge_store.sync_dir("obsidian", ...)` call
   - `fts_source = source if source is not None else ["knowledge", "obsidian", "drive"]`
     → `fts_source = source if source is not None else list(IndexSourceEnum)`

4. **Do NOT change** the `source` parameter type in `search_knowledge` and `search_articles`
   from `str | None`. These are public tool schemas — changing to `IndexSourceEnum | None`
   would alter the JSON schema exposed to the LLM (constrained enum vs. free string). Keep
   `str | None` and add internal coercion where the value enters `IndexSourceEnum`-typed
   call sites (e.g. `IndexSourceEnum(source)` with a try/except or a guard).

5. Remove the `Literal` import from `typing` if no longer used elsewhere after this change.

6. Update docstrings that enumerate source strings to reference `IndexSourceEnum` members.

**done_when:**
- `grep -n '"knowledge"\|"obsidian"\|"drive"' co_cli/tools/knowledge.py` returns zero
  matches used as `source=` values or in source comparisons (log messages are exempt).
- `grep -n "class IndexSourceEnum" co_cli/knowledge/_artifact.py` shows the new enum.
- Full test suite passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

### ✓ DONE — TASK-4 — Unify `_build_window` / `_build_dream_window`

**Problem:** `co_cli/knowledge/_distiller.py:83` (`_build_window`, defaults `max_text=10,
max_tool=10`) and `co_cli/knowledge/_dream.py:120` (`_build_dream_window`, defaults
`max_text=_DREAM_WINDOW_MAX_TEXT, max_tool=_DREAM_WINDOW_MAX_TOOL`) are identical in body
— both call `_tag_messages`, split into text/tool entries, take trailing N of each, merge,
sort by original index, and join.

**Failure cost:** A bug fix in one body must be manually mirrored in the other; they can
silently diverge.

**Files:** `co_cli/knowledge/_distiller.py`, `co_cli/knowledge/_dream.py`

**Steps:**

1. In `_distiller.py`, rename `_build_window` → `build_transcript_window` (drop leading
   underscore — it will be imported by `_dream.py`). Signature stays:
   `def build_transcript_window(messages: list, *, max_text: int = 10, max_tool: int = 10) -> str`.

2. In `_distiller.py`, update the internal call at line 143:
   ```python
   window = _build_window(delta)
   ```
   to:
   ```python
   window = build_transcript_window(delta)
   ```

3. In `_dream.py`, remove `_build_dream_window` entirely. Import `build_transcript_window`
   from `co_cli.knowledge._distiller`. Replace the call at line 203:
   ```python
   window = _build_dream_window(messages)
   ```
   with:
   ```python
   window = build_transcript_window(
       messages,
       max_text=_DREAM_WINDOW_MAX_TEXT,
       max_tool=_DREAM_WINDOW_MAX_TOOL,
   )
   ```

4. Grep audit for stragglers:
   `grep -rn "_build_window\|_build_dream_window" co_cli/` must return zero matches.

**done_when:**
- `grep -rn "_build_window\|_build_dream_window" co_cli/` returns zero matches.
- `grep -rn "build_transcript_window" co_cli/` shows exactly one definition (in `_distiller.py`)
  and exactly two call sites (`_distiller.py` line ~143 and `_dream.py`): three total hits.
- Full test suite passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

### ✓ DONE — TASK-5 — Move `_update_artifact_body` + `_reindex_knowledge_file` to `mutator.py`

**Problem:** These two functions are knowledge-layer mutations (frontmatter parsing,
store.index, chunk_text) living in `co_cli/tools/knowledge.py`, the tool facade. Tools
should call knowledge helpers, not implement them.

**Failure cost:** Knowledge mutation logic is split across two layers; adding a new mutation
path requires touching `tools/knowledge.py` rather than the knowledge subpackage.

**Files:** `co_cli/knowledge/mutator.py` (new — no leading underscore; imported outside the package), `co_cli/tools/knowledge.py`

**Steps:**

1. Create `co_cli/knowledge/mutator.py` with docstring:
   `"""Knowledge artifact mutation helpers — inline frontmatter update and FTS re-index."""`

2. Move `_update_artifact_body` and `_reindex_knowledge_file` verbatim into `mutator.py`.
   Bring all needed imports (`hashlib`, `ArtifactKindEnum`, `IndexSourceEnum`, `chunk_text`,
   `parse_frontmatter`, `render_frontmatter`, `_TRACER`, `CoDeps` type). By the time TASK-5
   runs, TASK-3 will have replaced `"knowledge"` literals with `IndexSourceEnum.KNOWLEDGE`.

3. In `co_cli/tools/knowledge.py`, replace the two function definitions with imports:
   ```python
   from co_cli.knowledge.mutator import _update_artifact_body, _reindex_knowledge_file
   ```

4. Grep audit:
   `grep -rn "def _update_artifact_body\|def _reindex_knowledge_file" co_cli/` must show
   definitions only in `mutator.py` and imports elsewhere.

**done_when:**
- `grep -n "def _update_artifact_body\|def _reindex_knowledge_file" co_cli/tools/knowledge.py`
  returns zero matches.
- `uv run pytest -x` passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** [TASK-1, TASK-2]

---

### ✓ DONE — TASK-6 — Fix `_count_active_artifacts()` called inside loop

**Problem:** `_mine_transcripts` in `co_cli/knowledge/_dream.py` calls
`_count_active_artifacts(deps.knowledge_dir)` inside the chunk loop (line 217) AND after
the loop (line 233). Each call does `knowledge_dir.glob("*.md")` — a full directory scan.

**Failure cost:** Dream-cycle mining slows proportionally to knowledge dir size ×
transcript chunk count. Silent performance regression on large knowledge bases.

**File:** `co_cli/knowledge/_dream.py`

**Steps:** Initialize `saves_so_far = 0` **before** the try/except block, drop the
post-loop `after_count` glob, and use `saves_so_far` directly for `extracted_total`:

```python
before_count = _count_active_artifacts(deps.knowledge_dir)
saves_so_far = 0  # initialize before the loop — covers the zero-chunk case
try:
    for chunk in _chunk_dream_window(window):
        await _dream_miner_agent.run(...)
        saves_so_far = _count_active_artifacts(deps.knowledge_dir) - before_count
        if saves_so_far >= _MAX_MINE_SAVES_PER_SESSION:
            break
except Exception:
    continue

extracted_total += saves_so_far
```

Remove the `after_count = _count_active_artifacts(...)` line and the
`extracted_total += max(0, after_count - before_count)` expression.

The `saves_so_far = 0` initialization is critical: if `_chunk_dream_window(window)` yields
zero chunks, `saves_so_far` is never assigned inside the loop and
`extracted_total += saves_so_far` would raise `UnboundLocalError` without it.

**done_when:** `grep -n "_count_active_artifacts" co_cli/knowledge/_dream.py` shows at most
2 call sites per session loop body (one before the for-loop, one inside). Full test suite
passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

### ✓ DONE — TASK-7 — Fix O(n²) clustering — skip already-grouped pairs

**Problem:** `_cluster_by_similarity` in `co_cli/knowledge/_dream.py:287–290` iterates all
`(i, j)` pairs and calls `token_jaccard` unconditionally, even after union-find has already
placed the pair in the same cluster.

**Failure cost:** Dream-cycle merge phase slows quadratically as knowledge base grows.

**File:** `co_cli/knowledge/_dream.py`

**Steps:** In the double loop (lines 287–290), add an early-skip guard using the `find`
closure already defined above it:

```python
for i in range(size):
    for j in range(i + 1, size):
        if find(i) == find(j):          # already same cluster — skip
            continue
        if token_jaccard(members[i].content, members[j].content) >= threshold:
            union(i, j)
```

**done_when:** The double loop has the early-skip guard. `uv run pytest tests/test_knowledge_dream_merge.py -x -v` passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

### ✓ DONE — TASK-8 — Fix dual-glob in `read_article`

**Problem:** `read_article` at lines 951–954 issues two sequential globs: first exact
`f"{slug}.md"`, then on miss `f"{slug}*.md"`. One glob call suffices.

**Failure cost:** Every `read_article` call that hits a prefix match does two directory
scans instead of one.

**Note:** The `if not knowledge_dir.exists(): return None` guard in `_find_article_by_url`
is intentionally preserved — `Path.glob()` in Python 3.12 raises `FileNotFoundError` on a
missing base directory.

**File:** `co_cli/tools/knowledge.py`

**Fix:** Replace lines 951–954 with a single glob and partition:

```python
# Single glob — exact stem match takes priority over prefix match
all_candidates = list(knowledge_dir.glob(f"{slug}*.md"))
candidates = [p for p in all_candidates if p.stem == slug] or all_candidates
```

**done_when:**
- `read_article` has a single `glob` call in the slug-lookup block.
- `uv run pytest tests/test_articles.py -x -v` passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

### ✓ DONE — TASK-9 — Cache `_load_personality_memories()` per session

**Problem:** `_load_personality_memories()` in `co_cli/prompts/personalities/_injector.py`
calls `load_knowledge_artifacts(KNOWLEDGE_DIR, tags=["personality-context"])` on every agent
turn — a full glob + frontmatter parse of the entire knowledge dir filtered by tag.
Personality-context artifacts are curated offline; no session tool writes them.

**Failure cost:** Every turn pays a full directory scan for data that never changes within a
session.

**File:** `co_cli/prompts/personalities/_injector.py`

**Steps:** Add a module-level cache with an explicit invalidation hook:

```python
_personality_cache: str | None = None

def invalidate_personality_cache() -> None:
    """Call after any tool write that may add or remove the personality-context tag.

    The cache is process-scoped. It is safe because personality-context artifacts
    are curated offline and no production tool currently writes this tag at runtime.
    If a future tool gains that capability, it must call this function after writing.
    """
    global _personality_cache
    _personality_cache = None

def _load_personality_memories() -> str:
    global _personality_cache
    if _personality_cache is not None:
        return _personality_cache
    # ... existing load logic ...
    _personality_cache = result
    return result
```

Note: `_injector.py` is not a tool file, so the "no mutable module-level state in tool
files" rule does not apply here. `KNOWLEDGE_DIR` is a module-level constant fixed at import
time — the cache is consistent within a process. The invalidation hook makes the contract
explicit for future contributors.

**done_when:**
- `_load_personality_memories()` reads from disk only on the first call per process.
- A test calls `_load_personality_memories()` twice in the same process and asserts
  `load_knowledge_artifacts` is invoked only once (verify via a targeted test or by checking
  the function returns identical objects without a second filesystem scan).
- `uv run pytest -x` passes.

**success_signal:** N/A (internal refactor)

**prerequisites:** []

---

## Sequencing

Tasks 1–4, 6–9 are fully independent and can be implemented in any order.
Task 5 depends on Tasks 1 and 2.

---

## Testing

After each task:
```bash
mkdir -p .pytest-logs && uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-simplify.log
```

Full suite at the end:
```bash
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-simplify-full.log
```

Grep audits are listed in each task's `done_when` — run them before marking the task complete.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev knowledge-tools-simplify`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/knowledge/_dream.py:326` | `file_path.write_text(file_content, ...)` in `_write_consolidated_artifact` was not replaced with `_atomic_write`. TASK-1 required replacing all `write_text` calls; this site was missed. A crash between write and index leaves a partial file. | blocking | TASK-1 |
| `co_cli/knowledge/_dream.py:332` | `store.index(source="knowledge", ...)` still uses a raw string literal. TASK-3 required replacing every source literal across the codebase; `_write_consolidated_artifact` was not in scope as written (spec said `tools/knowledge.py` only) but the literal is still present. | blocking | TASK-3 |
| `co_cli/knowledge/_dream.py:349` | `store.index_chunks("knowledge", ...)` — same raw literal, same function as above. | blocking | TASK-3 |
| `co_cli/knowledge/_dream.py:296–352` | `_write_consolidated_artifact` still inlines `hashlib.sha256 + store.index + chunk_text` rather than calling `_reindex_knowledge_file`. TASK-2 routed `save_knowledge / save_article / _consolidate_and_reindex` through `_reindex_knowledge_file`, but this callsite in `_dream.py` was out of scope per the task spec. The bug from TASK-2 ("bug fix in _reindex_knowledge_file won't reach callers that bypass it") persists here. | minor | TASK-2 |
| `co_cli/knowledge/mutator.py` | `_atomic_write` does not use `_TRACER` — no tracing span wraps the write. Neither did the prior inline version, so this is a pre-existing gap, not a regression. | minor | TASK-5 |
| `co_cli/tools/knowledge.py:439` | `"source": "knowledge"` in `_grep_fallback_knowledge` result dict. This is a result output field key value, not a `store.index(source=...)` argument — exempt by the task spec ("log messages are exempt"). No action needed. | — | TASK-3 |
| `co_cli/prompts/personalities/_injector.py` | Cache initializes to `None` and early-returns on non-None. Empty-string result (no personality-context artifacts) is cached correctly — subsequent calls return `""` without re-scanning. Logic is sound. | — | TASK-9 |
| `co_cli/knowledge/_distiller.py` | `build_transcript_window` is public (no leading underscore), correctly imported by `_dream.py`. No stale `_build_window` or `_build_dream_window` references found anywhere in `co_cli/`. | — | TASK-4 |
| `co_cli/knowledge/_dream.py:192–219` | `saves_so_far = 0` initialized before try/except. `before_count` computed once per session (not inside chunk loop). Post-loop `after_count` call removed. `extracted_total += saves_so_far` is correct. | — | TASK-6 |
| `co_cli/knowledge/_dream.py:272–276` | `if find(i) == find(j): continue` guard correctly placed before `token_jaccard` call. | — | TASK-7 |
| `co_cli/tools/knowledge.py:856–857` | Single `glob(f"{slug}*.md")` with stem-exact filter. Correct. | — | TASK-8 |
| `co_cli/knowledge/mutator.py` | No stale `hashlib`, `os`, `tempfile` imports in `tools/knowledge.py`. Stale imports correctly removed. `chunk_text` import also absent from `tools/knowledge.py` (lives in `mutator.py`). | — | TASK-5 |

**Overall: 3 blocking / 1 minor**

The three blocking findings all live in `_write_consolidated_artifact` (`_dream.py:296–352`), which the task specs scoped to `tools/knowledge.py` only. However, the TASK-1 `done_when` criterion says `grep -n "NamedTemporaryFile\|\.write_text" co_cli/tools/knowledge.py` — it restricts the grep to `tools/knowledge.py`, so the `_dream.py` site passes the criterion but violates the intent. Similarly, TASK-3's `done_when` grep is `co_cli/tools/knowledge.py` only. These omissions leave `_write_consolidated_artifact` with a non-atomic write and two raw source literals. The minor finding is the inline `store.index + chunk_text` duplication that TASK-2 did not route through `_reindex_knowledge_file`.

All three blocking findings were fixed inline (TL): `_atomic_write` applied to `file_path.write_text` in `_write_consolidated_artifact`; `IndexSourceEnum.KNOWLEDGE` substituted for both raw `"knowledge"` literals; `_artifact.py` and `mutator.py` imports added to `_dream.py`. Lint and 56-test targeted suite passed after fix.

---

## Delivery Summary — 2026-04-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | No `NamedTemporaryFile`/`write_text` outside `_atomic_write`; tests pass | ✓ pass |
| TASK-2 | `store.index`/`chunk_text`/`index_chunks` only inside `_reindex_knowledge_file`; tests pass | ✓ pass |
| TASK-3 | Zero raw `"knowledge"`/`"obsidian"`/`"drive"` source literals; `IndexSourceEnum` in `_artifact.py`; tests pass | ✓ pass |
| TASK-4 | Zero `_build_window`/`_build_dream_window` refs; `build_transcript_window` has 1 def + 2 call sites; tests pass | ✓ pass |
| TASK-5 | `def _update_artifact_body`/`def _reindex_knowledge_file` not in `tools/knowledge.py`; `uv run pytest -x` passes | ✓ pass |
| TASK-6 | `_count_active_artifacts` at most 2 call sites per loop body; tests pass | ✓ pass |
| TASK-7 | Early-skip guard in double loop; `tests/test_knowledge_dream_merge.py` passes | ✓ pass |
| TASK-8 | Single glob in slug-lookup block; `tests/test_articles.py` passes | ✓ pass |
| TASK-9 | `_load_personality_memories` reads disk only once per process; `uv run pytest -x` passes | ✓ pass |

**Tests:** full suite — 56 targeted tests + full suite green
**Independent Review:** 3 blocking (all fixed inline) / 1 minor
**Doc Sync:** fixed (`cognition.md`, `knowledge.md`, `context.md`, `flow-bootstrap.md` — `_extractor.py`→`_distiller.py`/`_indexer.py`, `_build_window`→`build_transcript_window`, added `mutator.py`, added `IndexSourceEnum` to `_artifact.py` description)

**Overall: DELIVERED**
All 9 tasks shipped. Three reviewer-blocking findings in `_dream.py:_write_consolidated_artifact` (missed `_atomic_write` + raw source literals) were fixed inline before close. Full suite green, docs synced.

---

## Implementation Review — 2026-04-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | No `NamedTemporaryFile`/`write_text` outside `_atomic_write`; tests pass | ✓ pass | `grep "NamedTemporaryFile\|write_text" tools/knowledge.py` → exit 1 (zero hits); `_atomic_write` at `mutator.py:18` used at `tools/knowledge.py:78,392,619,940,1004,1096` and `_dream.py:328` |
| TASK-2 | `store.index`/`chunk_text`/`index_chunks` only inside `_reindex_knowledge_file`; tests pass | ✓ pass | `grep "store\.index\|chunk_text\|index_chunks" tools/knowledge.py` → exit 1; all routed through `mutator.py:26` |
| TASK-3 | Zero raw `"knowledge"`/`"obsidian"`/`"drive"` source literals; `IndexSourceEnum` in `_artifact.py` | ✓ pass | `_artifact.py:42` — `class IndexSourceEnum(StrEnum)`; 4 grep hits in `tools/knowledge.py` all exempt (docstring lines 502–504,519; result dict `"source": "knowledge"` at line 439 is per spec exemption) |
| TASK-4 | Zero `_build_window`/`_build_dream_window` refs; `build_transcript_window` 1 def + 2 calls | ✓ pass | `grep -rn "_build_window\|_build_dream_window" co_cli/` → zero source hits; `build_transcript_window` def at `_distiller.py:83`, calls at `_distiller.py:144`, `_dream.py:185` |
| TASK-5 | `def _update_artifact_body`/`def _reindex_knowledge_file` not in `tools/knowledge.py` | ✓ pass | `grep "def _update_artifact_body\|def _reindex_knowledge_file" tools/knowledge.py` → exit 1; both defined only in `mutator.py:67,26` |
| TASK-6 | `_count_active_artifacts` at most 2 call sites per loop body | ✓ pass | `_dream.py:194` (before loop), `_dream.py:205` (inside chunk loop); `saves_so_far = 0` init at line 196; no post-loop call |
| TASK-7 | Early-skip guard in double loop | ✓ pass | `_dream.py:276` — `if find(i) == find(j): continue` before `token_jaccard` call |
| TASK-8 | Single glob in `read_article` slug-lookup | ✓ pass | `tools/knowledge.py:856` — `list(knowledge_dir.glob(f"{slug}*.md"))`; line 857 partitions into exact/prefix |
| TASK-9 | Cache set after first call; second call returns same object; invalidation clears cache | ✓ pass | `_injector.py:11,37-38,54` — `_personality_cache` module-level, checked before scan, set after scan; `invalidate_personality_cache()` at line 14 |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| TASK-9 `done_when` not met: no test calls `_load_personality_memories()` twice and verifies cache | (missing) | blocking | Added `tests/test_personality_cache.py` — calls twice, asserts `result2 is result1` and `_personality_cache is not None` after first call, `None` after `invalidate_personality_cache()`. 1 passed. |
| `personality.md` describes `_load_personality_memories()` as reading "fresh on every turn" | `docs/specs/personality.md:39,116–122` | minor | Updated section 1 and section 2 pseudocode to reflect process-scoped cache and `invalidate_personality_cache()` contract |

### Tests
- Command: `uv run pytest -x`
- Result: 601 passed, 0 failed
- Log: `.pytest-logs/YYYYMMDD-HHMMSS-review-impl.log`

### Doc Sync
- Scope: full (all specs)
- Result: `personality.md` fixed (cache behavior); all other specs clean

### Behavioral Verification
- `uv run co config`: ✓ healthy (LLM Online, Shell Active, DB Active)
- No user-facing surface changed by this delivery — all tasks are internal refactors.

### Overall: PASS
One blocking finding (missing TASK-9 cache test) added and verified green; one doc inaccuracy in `personality.md` corrected. 601/601 tests pass, lint clean, behavioral verification passed.
