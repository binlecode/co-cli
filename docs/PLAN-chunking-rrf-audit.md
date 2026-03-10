# Plan Audit Log: Article Chunking + RRF Hybrid Merge
_Slug: chunking-rrf | Date: 2026-03-10_

---

# Audit Log

## Cycle C1 — Team Lead

Task type: code-feature. Plan pre-existed; TL reviewed implementation state and made targeted updates:

- Added `Task type: code-feature` declaration.
- Added `prerequisites: [TASK-1, TASK-2]` to TASK-3 and TASK-4 (they depend on `_chunker.py` and `index_chunks()` existing).
- Added section 2b-bis to TASK-2: the existing character-level chunking in `index()` (the `chunk_id > 0` branch) must be disabled as part of TASK-2 scope, otherwise `sync_dir` double-indexes into both `docs` (character chunks) and `chunks` (paragraph chunks) after TASK-3 ships.
- Updated TASK-2 `done_when` to include verification that `chunk_id > 0` writes are removed from `index()`.

Implementation state confirmed:
- TASK-5 (config/deps/main): SHIPPED — `knowledge_chunk_size`/`knowledge_chunk_overlap` present in all three files with `CO_CLI_` env prefix.
- TASK-1 (`co_cli/_chunker.py`): not started.
- TASK-2 (schema + methods + routing): not started — existing code uses character-level `chunk_id` in `docs`.
- TASK-3 (sync_dir): not started.
- TASK-4 (articles.py): not started.
- TASK-6 (RRF): not started — `_hybrid_merge()` still uses weighted score merge.

Submitting for Core Dev review.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2, CD-M-3
**Summary:** The plan is well-structured and the RRF section is clean, but three blocking issues exist: (1) the existing `test_chunking` test directly contradicts the plan's goal and will break when TASK-2b-bis ships; (2) `rebuild()` is never mentioned but silently breaks chunk consistency after this change; (3) the `done_when` grep for TASK-2 is wrong and will pass vacuously even if the old chunking branch is left intact.

**Major issues:**

- **CD-M-1** [TASK-2 / test_knowledge_index.py]: `test_chunking` (lines 531–554) directly tests the old character-level `chunk_id > 0` behaviour. TASK-2b-bis removes exactly that behaviour. The plan's Testing section lists new scenarios 8–15 but gives no instruction to remove `test_chunking`. Recommendation: add an explicit bullet to TASK-2 `done_when`: "Delete `test_chunking` from `tests/test_knowledge_index.py`; replace with scenario 8."

- **CD-M-2** [TASK-2 / `rebuild()` method]: `rebuild()` deletes `docs` rows for a source then calls `sync_dir()`. After TASK-2 ships, `sync_dir` writes to `chunks`, but `rebuild()` never deletes orphaned chunk rows — causing duplicate chunk rows on every rebuild. Recommendation: add a step to TASK-2 to call `remove_chunks` (or bulk DELETE from `chunks WHERE source = ?`) inside `rebuild()` before the docs DELETE, and add a verifiable `done_when` grep.

- **CD-M-3** [TASK-2 `done_when` — 5th bullet]: `grep -v 'chunk_id > 0' co_cli/_knowledge_index.py` always exits 0 (non-matching lines always exist). The check is logically inverted. Recommendation: replace with `! grep -q 'chunk_id > 0' co_cli/_knowledge_index.py`.

**Minor issues:**

- **CD-m-1** [TASK-2, 2c]: Plan pseudocode shows `chunk_size: int = 512, chunk_overlap: int = 64` but shipped TASK-5 constants are 600/80 and `KnowledgeIndex.__init__` already defaults to 600/80. Recommendation: update pseudocode to `chunk_size: int = 600, chunk_overlap: int = 80`.

- **CD-m-2** [TASK-4 `done_when`]: `grep -q 'index_chunks'` only checks presence — `save_article` has two call sites (new-article + consolidation path). Recommendation: `grep -c 'index_chunks' co_cli/tools/articles.py` asserts count equals 2.

- **CD-m-3** [TASK-3 `done_when`]: Scenario 12 covers two orthogonal behaviours. Recommendation: split into 12a (library emits chunks) and 12b (memory does NOT emit chunks).

## Cycle C1 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** The plan addresses the actual recall problem directly and with the right tools. Both chunking and RRF are minimum-viable, well-scoped interventions. No gold-plating detected; known limitations are correctly deferred.

**Major issues:**
- none

**Minor issues:**
- **PO-m-1** [TASK-4 / Option B paragraph]: Option B is dead weight — the plan already resolves the choice. Recommendation: delete the Option B paragraph; leave only the chosen path.
- **PO-m-2** [TASK-6 / deprecated settings]: `hybrid_vector_weight` and `hybrid_text_weight` becoming silent no-ops after RRF ships is a minor UX hazard. Recommendation: either add a one-line `logger.warning` when non-default values are detected, or explicitly note the no-warning behaviour as an accepted limitation in Known Limitations.
- **PO-m-3** [TASK-2 / `read_article_detail`]: After chunking ships, `search_knowledge` returns chunk snippets but `read_article_detail` returns the full body — asymmetry could confuse future contributors. Recommendation: add one sentence to the Known Limitations entry making this explicit.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Add explicit `done_when` bullet: delete `test_chunking`, replace with scenario 8. |
| CD-M-2   | adopt    | Add `rebuild()` fix to TASK-2 scope (new subsection 2j) with verifiable `done_when`. |
| CD-M-3   | adopt    | Fix grep to `! grep -q 'chunk_id > 0'`. |
| CD-m-1   | adopt    | Update TASK-2 pseudocode defaults to 600/80. |
| CD-m-2   | adopt    | Change TASK-4 `done_when` to `grep -c ... == 2`. |
| CD-m-3   | adopt    | Split scenario 12 into 12a and 12b in Testing section and TASK-3 `done_when`. |
| PO-m-1   | adopt    | Remove Option B paragraph from TASK-4. |
| PO-m-2   | modify   | Add to Known Limitations as accepted behaviour — adding a runtime warning is scope creep for MVP. |
| PO-m-3   | adopt    | Add asymmetry sentence to Known Limitations. |
