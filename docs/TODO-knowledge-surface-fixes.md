# TODO: Knowledge Surface Implementation Fixes

Task type: code + doc

## Context

Source: TL implementation review of knowledge surface (2026-03-07).
Absorbs remaining items from TODO-retrieval-surfaces-2.5-alignment (deleted).

No prior REVIEW verdict for this scope.

---

## TASK-1: Fix `_dedup_pulled` leaving stale FTS entries after file deletion [DONE]

**Problem:**
`_dedup_pulled` called `older.path.unlink()` but never called
`knowledge_index.remove(source, path)`. After dedup-on-read deleted a file, the FTS
entry for that path persisted in `search.db` until the next bootstrap `sync_dir`.

**Fix:** In `recall_memory` (FTS path), diff before/after dedup paths and call
`knowledge_index.remove("memory", p)` for each deleted path.

done_when:
- `grep -n "knowledge_index.remove" co_cli/tools/memory.py` returns match in recall_memory
- New test `test_dedup_pulled_removes_stale_fts_entry` passes

---

## TASK-2: Fix `recall_memory` O(N) full memory load in FTS path [DONE]

**Problem:**
FTS path called `_load_memories(memory_dir)` — loading ALL memory files — solely to
build a path→entry map. O(N) disk scan on every user turn.

**Fix:** Load only the FTS-pointed files directly (O(k) where k ≤ max_results×4).
Lazy full load for one-hop traversal only when matched entries have `related` fields.

done_when:
- `grep -n "_load_memories(memory_dir)" co_cli/tools/memory.py` returns 0 matches in the FTS path section
- Existing recall_memory tests pass

---

## TASK-3: Fix `recall_memory` ranking — restore BM25 signal alongside decay [DONE]

**Problem:**
FTS path threw away BM25 scores and re-ranked purely by `_decay_multiplier`.

**Fix:** Composite scoring: `0.6 * bm25 + 0.4 * decay` where `bm25` is reinverted
(`1.0 - r.score`) so higher = better match. `decay_protected` entries stay at decay=1.0.

Note: `r.score` uses `1/(1+abs(rank))` convention (lower = stronger BM25 match);
the reinversion restores the expected "higher = better" semantic before combining.

done_when:
- `grep -n "path_to_bm25" co_cli/tools/memory.py` returns match in recall_memory FTS path
- `grep -n "0.6 \* bm25" co_cli/tools/memory.py` returns match
- Test `test_composite_bm25_decay_scoring` passes

---

## TASK-4: Fix `_find_article_by_url` O(N) scan — text prefilter [DONE]

**Problem:**
Every `save_article` call scanned all files in `library_dir`, parsing frontmatter for
each before checking `origin_url`. O(N) reads + parses.

**Fix:** Fast string prefilter — check `origin_url in raw` before parsing frontmatter.

Note: Proper fix is to add `origin_url` column to `docs` table. Deferred until library
grows large enough to warrant the schema migration.

done_when:
- `grep -n "origin_url not in raw" co_cli/tools/articles.py` returns match

---

## TASK-5: Fix stale `inject_opening_context` docstring [DONE]

**Fix:** Updated comment to: "recall_memory is FTS5/BM25 or grep fallback — zero LLM
cost in both cases."

done_when:
- `grep -n "grep-based" co_cli/_history.py` returns no matches

---

## TASK-6: Doc fix — `search_knowledge` signature + fallback semantics in DESIGN-knowledge.md [DONE]

Updated §2.4 signature line and fallback subsection to reflect actual parameter names
(`limit`, `tag_match_mode`) and accurate `knowledge_index is None` routing behavior.

done_when:
- `grep -n "limit" docs/DESIGN-knowledge.md` returns match on `search_knowledge` line in §2.4
- `grep -n "tag_match_mode" docs/DESIGN-knowledge.md` returns match on same line
- `grep -n "knowledge_index is None" docs/DESIGN-knowledge.md` returns match in fallback subsection

---

## TASK-7: Doc fix — Elevate retrieval mutation to explicit contract language in DESIGN-memory.md [DONE]

Replaced passive note "Side effect: retrieval can mutate data..." with a full
"Retrieval mutation contract" subsection in §2.6 describing `_touch_memory()` and
`_dedup_pulled()` mutations, runtime call chain, and `decay_protected` exemptions.

done_when:
- `grep -F "read+maintenance" docs/DESIGN-memory.md` returns match
- `grep -n "_touch_memory" docs/DESIGN-memory.md` returns match in new subsection
- `grep -n "_dedup_pulled" docs/DESIGN-memory.md` returns match in new subsection
- `grep -n "Side effect:" docs/DESIGN-memory.md` returns no match

---

## Known Limitations (Deferred)

- `_find_article_by_url` proper fix: add `origin_url` column to `docs` table and query
  via index. Schema migration needed. Deferred until library grows large.
- Unifying `recall_memory` / `search_memories` result schemas: intentional split
  (internal vs agent-registered). Doc note only.
- `search_knowledge` default exclusion of memories: intentional design. No change.
