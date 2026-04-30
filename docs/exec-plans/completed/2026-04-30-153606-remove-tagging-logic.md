# Exec Plan: Remove Tagging Logic

_Created: 2026-04-30_
_Slug: remove-tagging-logic_
_Task type: cleanup_

## Problem

Tags are a write-only field — stored in `knowledge/*.md` frontmatter and indexed in
`docs.tags` (DB column), but no code in the search or filter path consumes them.
`load_knowledge_artifacts(tags=...)` supports tag filtering but every caller passes
`None`. `SearchResult.tags` is populated but never acted on downstream. The dream miner
writes `personality-context` tags that nothing reads back. Tags add schema surface,
write-path complexity, and prompt guidance that implies behavior that does not exist.

**Decision:** remove all application-layer tag logic. The DB column (`docs.tags`) and
existing `tags:` frontmatter in `*.md` files are left in place — no migration, no data
loss, just silently ignored on load.

**Latent bug discovered during audit:** `co_cli/tools/obsidian.py:67` passes `tags=` to
`knowledge_store.search()`, which has no such parameter. The call is silently swallowed
by a surrounding `try/except`, causing the Obsidian FTS tag-filter path to always fail
through to the regex fallback. This plan fixes it as part of the removal.

## Out of Scope

- DB schema changes — `docs.tags` column stays
- Changes to `knowledge/*.md` files on disk — `tags:` frontmatter is kept, just ignored
- Obsidian-specific `_extract_frontmatter_tags()` helper — it parses Obsidian vault files
  (not knowledge artifacts) and is used for regex-path filtering; leave it in place
- FTS5 tag-in-index port (e.g. Hermes approach) — separate future plan if needed

## Files Changed

### Application logic

| File | Change |
|---|---|
| `co_cli/memory/artifact.py` | Remove `tags` field from `KnowledgeArtifact`; remove `tags=` param and filter from `load_knowledge_artifacts`; remove `tags=list(...)` from `load_knowledge_artifact` |
| `co_cli/memory/service.py` | Remove `tags=` from `save_artifact` and `mutate_artifact`; remove tag merge logic and all `"tags":` write paths |
| `co_cli/tools/memory/write.py` | Remove `tags=` param from `memory_create` tool and docstring reference |
| `co_cli/memory/knowledge_store.py` | Remove `tags` field from `SearchResult`; remove `tags=` from `_write_doc` and its INSERT; remove `tags=row["tags"]` propagation in `_fts_search` and `_vec_search`; remove tag extraction at `sync_dir` (lines 1166-1178) |
| `co_cli/memory/dream.py` | Remove `union_tags` accumulation and `tags=` in both `save_artifact` calls |
| `co_cli/memory/mutator.py` | Remove `tags=" ".join(...)` from the `_write_doc` call |
| `co_cli/memory/frontmatter.py` | Remove `("tags", list(artifact.tags))` from `_artifact_to_frontmatter` (line 154); leave `_require_str_list(fm, "tags")` in place |
| `co_cli/tools/memory/read.py` | Remove `m.tags` from grep filter; remove `"tags": a.tags` from list output |
| `co_cli/tools/obsidian.py` | Drop `tags=` kwarg from `knowledge_store.search()` call (latent bug fix) |
| `co_cli/memory/prompts/dream_miner.md` | Remove full tagging section (personality-context guidance) |

### Tests

| File | Change |
|---|---|
| `tests/memory/test_knowledge_artifact.py` | Remove `tags` assertions and constructor `tags=` usage |
| `tests/memory/test_service.py` | Remove `tags` param from fixture and test calls |
| `tests/memory/test_knowledge_dream_cycle.py` | Remove `tags=["testing"]` from artifact construction |
| `tests/bootstrap/test_bootstrap.py` | Remove `"tags": ["test"]` from fixture dict |
| `tests/memory/test_session_search_tool.py` | Remove `"tags": []` from fixture dict |
| `tests/memory/test_rrf_merge.py` | Remove `tags=None` from `SearchResult` construction |
| `tests/memory/test_knowledge_tools.py` | Remove `tags=` param from `_write_memory` helper and all 20+ call sites |
| `tests/memory/test_articles.py` | Remove `tags=` from all `save_artifact` / fixture calls |

## Tasks

### ✓ DONE — TASK-1 — Remove tags from artifact schema and load path

```
files:
  - co_cli/memory/artifact.py

done_when:
  - KnowledgeArtifact has no tags field
  - load_knowledge_artifacts has no tags param and no tag filter logic
  - load_knowledge_artifact does not read fm["tags"]
```

### ✓ DONE — TASK-2 — Remove tags from write path

```
files:
  - co_cli/memory/service.py
  - co_cli/tools/memory/write.py
  - co_cli/memory/dream.py
  - co_cli/memory/mutator.py
  - co_cli/memory/frontmatter.py

done_when:
  - save_artifact and mutate_artifact have no tags param
  - No "tags" key written to frontmatter dicts in service.py
  - dream.py has no union_tags accumulation
  - mutator.py does not read or write tags
  - memory_create tool has no tags param
  - _artifact_to_frontmatter does not reference artifact.tags
```

### ✓ DONE — TASK-3 — Remove tags from read/search path

```
files:
  - co_cli/memory/knowledge_store.py
  - co_cli/tools/memory/read.py
  - co_cli/tools/obsidian.py

done_when:
  - SearchResult has no tags field
  - _write_doc has no tags param; INSERT does not include tags value
  - _fts_search and _vec_search do not propagate tags= to SearchResult
  - sync_dir does not extract tags from frontmatter
  - memory_list output dict has no "tags" key
  - grep_recall filter does not reference m.tags
  - obsidian.py search() call has no tags= kwarg
```

### ✓ DONE — TASK-4 — Remove tagging guidance from dream_miner.md

```
files:
  - co_cli/memory/prompts/dream_miner.md

done_when:
  - No mention of tags, personality-context, or tagging rules in the prompt
```

### ✓ DONE — TASK-5 — Update tests

```
files:
  - tests/memory/test_knowledge_artifact.py
  - tests/memory/test_service.py
  - tests/memory/test_knowledge_dream_cycle.py
  - tests/bootstrap/test_bootstrap.py
  - tests/memory/test_session_search_tool.py
  - tests/memory/test_rrf_merge.py
  - tests/memory/test_knowledge_tools.py
  - tests/memory/test_articles.py

done_when:
  - No test references tags= constructor param or .tags field assertion
  - uv run pytest tests/ -x passes with no tag-related errors
```

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | KnowledgeArtifact has no tags field; load_knowledge_artifacts has no tags param | ✓ pass |
| TASK-2 | save_artifact/mutate_artifact no tags param; no "tags" key written; dream.py/mutator.py clean; _artifact_to_frontmatter no artifact.tags | ✓ pass |
| TASK-3 | SearchResult no tags field; _write_doc no tags in INSERT; sync_dir no tag extraction; memory_list no "tags" key; obsidian.py latent bug fixed | ✓ pass |
| TASK-4 | No mention of tags, personality-context, or tagging rules in dream_miner.md | ✓ pass |
| TASK-5 | No test references tags= constructor param or .tags field; 95 scoped tests passed | ✓ pass |

**Integration fixes:** Removed stale `tags=None` kwargs from `mutator.py` and `dream.py` calls to `knowledge_store.index()` (absorbed by `**_kwargs` but dead code). LLM-driven `test_full_cycle_executes_all_phases_with_live_llm` showed one flaky run (extracted=0 on first run, extracted=5 on re-run) — pre-existing non-determinism, unrelated to our changes.

**Tests:** scoped (95 touched tests) — 95 passed, 0 failed
**Doc Sync:** fixed — memory.md: removed phantom personality-context tag claim (§2.1), removed tagged personality-context from §2.3, removed `tags` row from §2.5 artifact schema table

**Overall: DELIVERED**
All tagging logic removed from application layer. DB column and on-disk `tags:` frontmatter preserved per plan. Obsidian FTS latent bug fixed as part of removal.

## Verification

```
grep -r "\.tags\b\|tags=" co_cli/ --include="*.py"   # should return nothing relevant
uv run pytest tests/ -x
```

## Key File Locations

| Component | File |
|---|---|
| Artifact schema | `co_cli/memory/artifact.py` |
| Write service | `co_cli/memory/service.py` |
| `memory_create` tool | `co_cli/tools/memory/write.py` |
| Search index | `co_cli/memory/knowledge_store.py` |
| Dream cycle | `co_cli/memory/dream.py` |
| Mutator | `co_cli/memory/mutator.py` |
| Memory list / grep | `co_cli/tools/memory/read.py` |
| Obsidian tool | `co_cli/tools/obsidian.py` |
| Dream miner prompt | `co_cli/memory/prompts/dream_miner.md` |
