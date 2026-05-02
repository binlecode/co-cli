# knowledge write path cleanup

**Scope:** `co_cli/memory/`, `co_cli/tools/memory/`, `co_cli/agent/_native_toolset.py` — fix
functional bugs, eliminate dead code, and apply naming/visibility rules surfaced during
write-path audit.

---

## Issues

### Functional bugs

**F1 — incomplete `frontmatter_dict` passed to `reindex`** (`service.py:172-178, 216-222, 302-308`)
Three of four `save_artifact` paths hand-roll a partial dict instead of deriving it from the
artifact. Result: FTS index silently loses `tags`, `description`, or `source_ref` depending on path.
The Jaccard path (passes actual parsed frontmatter) is the only one that indexes correctly.

**F2 — `reindex` ignores config chunk sizes** (`service.py:116-117`)
`_reindex` uses module-level constants `_DEFAULT_CHUNK_SIZE=600` / `_DEFAULT_CHUNK_OVERLAP=80`
(defined at `service.py:39-40`) regardless of `config.knowledge.chunk_size` /
`config.knowledge.chunk_overlap`. Every `save_artifact` write ignores the configured chunking.

### Architectural anti-pattern

**A1 — `save_artifact` and `mutate_artifact` mix write with FTS reindex side effect**
Both functions take `memory_store` and call `_reindex` internally, despite `save_artifact`'s
docstring claiming it is "Pure — no RunContext." Reindexing is a call-site concern — the tool
layer owns config and decides whether and how to reindex. Fix: remove `memory_store` params and
all `_reindex` calls from both service functions. Have `memory_create` and `memory_modify` call
`reindex` explicitly after the write, passing config chunk sizes directly.

Consequence for `SaveResult` and `MutateResult`: callers need enough data to call `reindex`.
- `SaveResult`: replace the incomplete `fm_dict` field with `frontmatter_dict: dict` populated
  via `artifact_to_frontmatter(artifact)` (complete); add `markdown_content: str` (rendered file,
  needed for hash). Remove `memory_store` param from `save_artifact`.
- `MutateResult`: `frontmatter` (from N1 rename) is already the parsed dict; add
  `markdown_content: str`. Remove `memory_store` param from `mutate_artifact`.

This also fully resolves F1 and F2: the complete dict and config chunk sizes both live at the
call site.

### Dead code

**D1 — `_update_artifact_body` and `_reindex_knowledge_file` never called**
(`_reindex_knowledge_file`: `mutator.py:26-62`; `_update_artifact_body`: `mutator.py:65-78`)
Both are defined but never imported or called from outside `mutator.py`. Delete both.

### DRY violations

**R1 — `_write_consolidated_artifact` bypasses canonical write path** (`dream.py:331-355`)
Inlines `atomic_write` + `render_knowledge_file` + `store.index` + `store.index_chunks` directly.
After A1, the canonical pattern is: write → call `reindex` with config values. Dream.py should
follow the same pattern.

### Naming violations — abbreviations

**N1 — `fm` is a domain abbreviation**
(`service.py`, `artifact.py`, `frontmatter.py`, `memory_store.py`)
Rename all local variables, parameters, and struct fields named `fm` to `frontmatter`. Includes:
- `MutateResult.fm` → `MutateResult.frontmatter` (`service.py:63`)
- `_reindex` param `fm: dict` → `frontmatter: dict` (`service.py:95`)
- `_coerce_fields` param at `artifact.py:72`
- All internal functions in `frontmatter.py` (`_require_iso8601`, `_validate_identity`,
  `_validate_string_fields`, `_validate_typed_scalars`, `validate_knowledge_frontmatter`,
  `_artifact_to_frontmatter` locals, `render_knowledge_file:171`, `render_frontmatter:175`)
- Local variable in `memory_store.py:1161`
- Note: `fm` in `mutator.py` belongs to dead code deleted by T3 — no rename needed there.

**N2 — `fm_dict` is a domain abbreviation** (`service.py`)
Rename `fm_dict` → `frontmatter_dict` throughout (local variables and `SaveResult.fm_dict`
field). After A1, `SaveResult.frontmatter_dict` is populated via `artifact_to_frontmatter`.

**N3 — `md_content` uses an abbreviation** (`service.py`)
`md` is a domain abbreviation for markdown. Rename `md_content` → `markdown_content` throughout
`service.py` (function signature at `service.py:94`, local variables, all call sites).
Note: `md_content` in `mutator.py` is dead code deleted by T3 — no rename needed there.

### Naming violations — semantics

**N4 — `slug` used for two different concepts** (`service.py`, `co_cli/tools/memory/`)
`slugify()` produces a short URL-friendly string (e.g., `my-title`). But `SaveResult.slug`,
`MutateResult.slug`, the `slug` parameter in `mutate_artifact`, `_find_by_slug`'s parameter,
and the `slug` field in `_search_artifacts` result dicts all refer to the full filename stem
including UUID suffix (e.g., `my-title-a1b2c3`). Rename all full-stem usages to `filename_stem`.

### Visibility violations

**V1 — `_atomic_write` imported across modules** (`mutator.py:18`)
Imported by both `service.py` and `dream.py`. Drop underscore: `atomic_write`.

**V2 — `_slugify` imported across modules** (`service.py:66`)
Imported by `dream.py`. Drop underscore: `slugify`.

**V3 — `_reindex` should be callable by `dream.py`** (`service.py:90`)
After A1, `dream.py` calls `reindex` directly. Drop underscore: `reindex`.

---

## Tasks

- [x] **T1** Apply V1, V2, V3, and drop underscore on `_artifact_to_frontmatter`:
  `_atomic_write` → `atomic_write`, `_slugify` → `slugify`, `_reindex` → `reindex`,
  `_artifact_to_frontmatter` → `artifact_to_frontmatter` (`frontmatter.py:138`).
  Update all import sites. **Do this first — T2, T4, T5 depend on public names.**

- [x] **T2** Delete dead code (D1): delete `_reindex_knowledge_file` (`mutator.py:26-62`) and
  `_update_artifact_body` (`mutator.py:65-78`). After deletion only `atomic_write` remains
  in `mutator.py`. Verify no callers exist before deleting.

- [x] **T3** Fix A1: separate reindex from service functions.
  - Add `chunk_size: int = _DEFAULT_CHUNK_SIZE` and `chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP`
    params to `reindex` (constants at `service.py:39-40`).
  - Remove `memory_store` param from `save_artifact`; remove all internal `reindex` calls from it.
    Update `SaveResult`: replace `fm_dict` with `frontmatter_dict: dict` (from
    `artifact_to_frontmatter(artifact)`); add `markdown_content: str` (rendered file).
    For the Jaccard "skipped" path, `frontmatter_dict` and `markdown_content` can be empty/`""` —
    no reindex needed (existing artifact unchanged).
  - Remove `memory_store` param from `mutate_artifact`; remove the internal `reindex` call.
    Add `markdown_content: str` to `MutateResult`.
  - Update `memory_create` (`write.py`): after `save_artifact`, if action != "skipped" and
    `ctx.deps.memory_store` is not None, call `reindex(store, result.path, result.content,
    result.markdown_content, result.frontmatter_dict, result.filename_stem,
    chunk_size=ctx.deps.config.knowledge.chunk_size,
    chunk_overlap=ctx.deps.config.knowledge.chunk_overlap)`.
  - Update `memory_modify` (`write.py`): after `mutate_artifact`, call `reindex` the same way
    using `result.updated_body`, `result.markdown_content`, `result.frontmatter`.

- [x] **T4** Fix R1: replace the inline index block in `_write_consolidated_artifact`
  (`dream.py:333-355`) with a direct call to `service.reindex(store, file_path, merged_body,
  file_content, artifact_to_frontmatter(merged_artifact), file_path.stem,
  chunk_size=deps.config.knowledge.chunk_size,
  chunk_overlap=deps.config.knowledge.chunk_overlap)`.

- [x] **T5** Fix F1 (now a `SaveResult` shape fix, covered by T3): verify all four
  `save_artifact` paths populate `SaveResult.frontmatter_dict` completely.
  - Paths 1, 2, 4 (URL-keyed existing, URL-keyed new, straight create): use
    `artifact_to_frontmatter(artifact)` — a `KnowledgeArtifact` is constructed in each.
  - Path 3b (Jaccard merged/appended): use the `frontmatter` dict parsed directly from the
    existing file — already complete; no `KnowledgeArtifact` object exists here, so
    `artifact_to_frontmatter` is not applicable.
  - Path 3a (Jaccard skipped): `frontmatter_dict={}` and `markdown_content=""` — no reindex
    at the call site (`action == "skipped"` guard in `memory_create`).

- [x] **T6** Apply N4: rename `slug` → `filename_stem` in `SaveResult.slug`,
  `MutateResult.slug`, `mutate_artifact`'s `slug` parameter, `_find_by_slug`'s `slug`
  parameter, and rename the function itself to `_find_by_filename_stem`. Update all call sites.

- [x] **T7** Apply N1, N2, N3:
  - `fm` → `frontmatter` across `service.py`, `artifact.py`, `frontmatter.py`, `memory_store.py`
    (includes `MutateResult.fm` → `MutateResult.frontmatter` and all function parameters/locals).
    `mutator.py` excluded — its `fm` usages are dead code removed by T2.
  - `fm_dict` → `frontmatter_dict` across `service.py` (field already reshaped by T3).
  - `md_content` → `markdown_content` across `service.py` only.
    `mutator.py` excluded — its `md_content` usages are dead code removed by T2.

- [x] **T8** Apply N4 in the tool layer: rename `slug` → `filename_stem` in
  `_search_artifacts` result dicts (`recall.py:93, 109`), `memory_modify` param name, and
  `memory_modify` return kwargs (`write.py:171`).

- [x] **T9** Remove `memory_list` from the tool surface; fold listing into `memory_search`'s
  empty-query path:
  - Delete the `memory_list` function from `read.py` entirely (function body and decorator).
  - Remove `memory_list` from `_native_toolset.py:53`.
  - Implement `_list_artifacts(ctx, kinds, limit, span, offset: int = 0)` in `recall.py`
    (same pattern as `_browse_recent`): loads knowledge artifacts, sorts by `created`
    descending, accepts `kinds: list[str] | None` filter and `offset` for callers that
    need to page, returns formatted output via `tool_output`.
  - In `memory_search`, when query is empty, call both `_browse_recent` (sessions) and
    `_list_artifacts` (knowledge artifacts); merge and return combined output.

- [x] **T10** Fix `memory_modify.action` type (`write.py:118`): change `action: str` →
  `action: Literal["append", "replace"]` and remove the now-redundant runtime guard at
  `write.py:145-149`.

- [x] **T11** Fix `memory_modify` docstring (`write.py:124, 140`): replace "Use memory_list
  to find the slug" with "Use memory_search to find the filename_stem"; update the
  `filename_stem` arg description to reference `memory_search` results.

- [x] **T12** Run full test suite. Fix any failures before marking done.

---

## Execution order

```
T1  (public symbols — T3, T4 depend on atomic_write, reindex, artifact_to_frontmatter)
 ↓
T2  (delete dead code — reduces noise before rename tasks)
 ↓
T6  (N4 service layer: slug → filename_stem — must precede T3 so SaveResult.filename_stem
     exists when T3 writes the call-site reindex in memory_create/memory_modify)
 ↓
T3  (A1 + F2: separate reindex; reshape SaveResult/MutateResult)
 ↓
T4  (R1: dream.py uses service.reindex with config values)
 ↓
T5  (F1 verification: confirm all save paths populate frontmatter_dict correctly)
 ↓
T7  (N1/N2/N3: fm/fm_dict/md_content renames)
 ↓
T8  (N4 tool layer — depends on T6)
 ↓
T9, T10, T11  (tool surface cleanup — independent of each other; T11 depends on T8)
 ↓
T12 (full test suite)
```

---

## Delivery Summary — 2026-05-02

| Task | done_when | Status |
|------|-----------|--------|
| T1 | `atomic_write`, `slugify`, `reindex`, `artifact_to_frontmatter` public in their modules; all import sites updated | ✓ pass |
| T2 | `_reindex_knowledge_file` and `_update_artifact_body` deleted; only `atomic_write` remains in `mutator.py` | ✓ pass |
| T3 | `save_artifact` and `mutate_artifact` have no `memory_store` param; `SaveResult` has `frontmatter_dict`/`markdown_content`/`filename_stem`; `reindex` called explicitly in `memory_create`/`memory_modify` | ✓ pass |
| T4 | `_write_consolidated_artifact` in `dream.py` calls `service.reindex` with config chunk sizes | ✓ pass |
| T5 | All four `save_artifact` paths populate `SaveResult.frontmatter_dict` via `artifact_to_frontmatter` or parsed dict | ✓ pass |
| T6 | `SaveResult.slug` → `filename_stem`, `MutateResult.slug` → `filename_stem`, `mutate_artifact` param, `_find_by_filename_stem`, all call sites updated | ✓ pass |
| T7 | `fm` → `frontmatter`, `fm_dict` → `frontmatter_dict`, `md_content` → `markdown_content` across `service.py`, `artifact.py`, `frontmatter.py`, `memory_store.py` | ✓ pass |
| T8 | `slug` → `filename_stem` in `_search_artifacts` result dicts, `memory_modify` param, and return kwargs | ✓ pass |
| T9 | `memory_list` deleted from `read.py` and `_native_toolset.py`; `_list_artifacts` added to `recall.py`; empty-query path in `memory_search` returns both sessions and artifacts | ✓ pass |
| T10 | `memory_modify.action: Literal["append", "replace"]`; runtime guard removed | ✓ pass |
| T11 | `memory_modify` docstring updated: `memory_list`/`slug` → `memory_search`/`filename_stem` | ✓ pass |
| T12 | `uv run pytest` — 109 passed, 0 failed | ✓ pass |

**Tests:** full suite — 109 passed, 0 failed
**Doc Sync:** fixed — `memory-knowledge.md`: `slug` → `filename_stem` in result shapes (×2), write-path description updated (`_atomic_write` → `atomic_write`, inline reindex → explicit at tool layer), `mutator.py` Files entry trimmed to `atomic_write` only, `read.py` Files entry: `memory_list()` removed

**Overall: DELIVERED**
All 12 tasks shipped. Service layer is now pure (no `memory_store` params, no reindex side effects). FTS reindexing is explicit at the tool layer with config-sourced chunk sizes. Dead code deleted. All abbreviation violations resolved (`fm`, `fm_dict`, `md_content`, `slug`). `memory_list` folded into `memory_search` empty-query path.

---

## Implementation Review — 2026-05-02

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | Public symbols + all import sites updated | ✓ pass | `mutator.py:8` `atomic_write`; `service.py:69` `slugify`; `service.py:93` `reindex`; `frontmatter.py:142` `artifact_to_frontmatter`; import sites `service.py:25,30`, `write.py:12` |
| T2 | Dead functions deleted; only `atomic_write` remains | ✓ pass | `mutator.py` is 13 lines — `atomic_write` only, no dead code |
| T3 | No `memory_store` param; `SaveResult`/`MutateResult` reshaped; explicit `reindex` at tool layer | ✓ pass | `service.py:125,286` (no `memory_store`); `service.py:45-67` (correct shapes); `write.py:85-94,172-181` (explicit reindex calls) |
| T4 | `_write_consolidated_artifact` calls `service.reindex` with config chunk sizes | ✓ pass | `dream.py:331-340` — positional + `chunk_size=deps.config.knowledge.chunk_size` confirmed |
| T5 | All four paths populate `frontmatter_dict` completely | ✓ pass | `service.py:179,208` (`artifact_to_frontmatter`); `service.py:230` (`{}`); `service.py:250` (parsed dict) |
| T6 | `slug` → `filename_stem` everywhere it means filename stem | ✓ pass | `service.py:54,62,73,289`; `recall.py:119,135`; `write.py:185`; `reindex` param fixed (see Issues) |
| T7 | `fm`/`fm_dict`/`md_content` renamed across all scoped files | ✓ pass | `artifact.py:72`; `frontmatter.py` functions; `memory_store.py:1161`; `service.py` clean |
| T8 | Tool-layer `slug` → `filename_stem` | ✓ pass | `recall.py:119,135`; `write.py:185` |
| T9 | `memory_list` deleted; `_list_artifacts` added; empty-query returns both channels | ✓ pass | `read.py` — no `memory_list`; `_native_toolset.py:44-91` — no `memory_list`; `recall.py:70-93`; `recall.py:293-313` |
| T10 | `action: Literal["append","replace"]`; runtime guard removed | ✓ pass | `write.py:129` |
| T11 | Docstring references `memory_search`/`filename_stem` | ✓ pass | `write.py:135,151` |
| T12 | 109 passed, 0 failed | ✓ pass | `.pytest-logs/20260502-095349-review-impl.log` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `reindex` parameter named `slug` — T6 N4 rename missed this instance; parameter represents full filename stem but kept the old name | `service.py:99` | blocking | Renamed `slug` → `filename_stem`; usage at `service.py:110` updated |

### Tests
- Command: `uv run pytest -x -v`
- Result: 109 passed, 0 failed
- Log: `.pytest-logs/20260502-095349-review-impl.log`

Fix verification: `uv run pytest tests/test_flow_memory_write.py` — 5 passed (`.pytest-logs/20260502-095637-fix-verify.log`)

### Doc Sync
- Scope: narrow — single-parameter rename in `service.py`; `reindex` parameter name is not referenced by name in any spec.
- Result: clean — no doc changes required.

### Behavioral Verification
- No `co status` command exists. User-facing changes (tool surface: `memory_list` removal, `memory_search` empty-query combined output, `action: Literal` type) verified via full test suite.
- No user-facing crashes or regressions in 109-test green run.

### Overall: PASS
One blocking finding found and fixed: `reindex` parameter `slug` renamed to `filename_stem` (`service.py:99,110`). All 12 tasks confirmed implemented to spec. Test suite green. Ready to ship.
