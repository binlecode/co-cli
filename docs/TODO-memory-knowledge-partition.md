# TODO: Memory / Knowledge Partition

Task type: doc+code

## Context

Design driver: `docs/FIX-retrieval-surfaces-2.5-alignment.md` surface review exposed a deeper
architectural issue — `source="memory"` is used in the FTS index for both memories (`kind:
memory`) and articles (`kind: article`). This conflates storage namespace (where) with content
type (what), and bleeds memory lifecycle semantics into the article layer.

No prior REVIEW verdict. No existing TODO for this slug.

**Architecture audit findings (pre-plan code scan):**

| Finding | Location | Impact |
|---------|----------|--------|
| `source="memory"` used for both `kind: memory` and `kind: article` in FTS index | `knowledge_index.py:8–9`, `memory_lifecycle.py:159,240`, `tools/articles.py:343,392` | Source label is semantically wrong for articles |
| `sync_dir("memory", knowledge_dir)` in bootstrap syncs all local files under `"memory"` regardless of kind | `_bootstrap.py:35` | Articles inherit wrong source label at every startup |
| `_load_memories()` called without `kind` filter in retention cap and dedup window scans | `memory_lifecycle.py:125,265` | Articles counted toward memory retention cap; dedup candidates include articles |
| `search_knowledge(source=None)` searches all local content including memories | `tools/articles.py:208–232` | Knowledge search and memory recall are indistinguishable to the model |
| No agent-registered semantic search tool for memories — model uses `search_knowledge(source="memory")` or `list_memories` | `agent.py:293–307` | Model has no dedicated memory search; `list_memories` is paginated listing, not query-driven |

**Reference system alignment:**

Letta cleanly separates `ArchivalPassage` (agent memory) from `SourcePassage` (external
documents) at the ORM/table level. OpenClaw uses `source` as a backend/origin label only and
does not mix content types under one source. Neither system uses a single source label for
both ephemeral agent memory and durable reference material.

**Proposed partition (confirmed with PO):**

```
Memory subsystem              Knowledge subsystem
─────────────────             ───────────────────────────────────
kind: memory                  kind: article  (local, web-fetched)
source: "memory"              source: "local" (local articles)
                              source: "obsidian" (vault notes)
                              source: "drive"   (Drive docs)

Lifecycle: signal detection,  Lifecycle: save/retrieve only
dedup, decay, consolidation,  No dedup window, no decay,
touch-on-read, retention cap  no signal hooks, no retention cap

Agent tools:                  Agent tools:
  search_memories (new)         search_knowledge (local+obsidian+drive)
  list_memories                 save_article
  save_memory                   read_article_detail
  update_memory                 search_notes / list_notes / read_note
  append_memory
```

## Problem & Outcome

**Problem:**
1. `source="memory"` labels local articles identically to memories in the FTS index.
2. Memory retention cap and dedup window incorrectly count articles as memories.
3. `search_knowledge` mixes memory and knowledge results by default, blurring the partition at the model's tool interface.
4. Model has no dedicated semantic search tool for memories.

**Outcome:**
- Local articles indexed under `source="local"` — clean semantic label for local knowledge backend.
- Memory lifecycle (dedup window, retention cap) operates on memories only.
- `search_knowledge` defaults to knowledge sources (local + obsidian + drive); memories excluded from default scope.
- New `search_memories` agent tool gives the model a dedicated, query-driven memory recall surface.
- Existing FTS index rows migrated on first bootstrap after upgrade.

## Scope

In scope:
- Source label rename: `source="memory"` for `kind: article` entries → `source="local"`.
- Bootstrap sync split: memories synced under `"memory"`, articles under `"local"`.
- Schema migration: existing FTS index rows updated.
- Memory lifecycle boundary: retention + dedup count only `kind: memory` entries.
- `search_knowledge` default scope: excludes `source="memory"`.
- New agent tool: `search_memories`.
- Doc update: `docs/DESIGN-knowledge.md` — source namespace table, §2.1, §2.3, §2.5.

Out of scope:
- Separate filesystem directories for memories vs articles (same `.co-cli/knowledge/` dir, different FTS source labels).
- Renaming `_load_memories()` (function is used internally; rename is cosmetic and deferred).
- Obsidian / Drive source label changes (already clean).
- Memory consolidation logic (no change to consolidation behavior).

## High-Level Design

### Source label convention (post-partition)

| Source | Meaning | Content |
|--------|---------|---------|
| `"memory"` | Agent memory — local, lifecycle-managed | `kind: memory` files |
| `"local"` | Local knowledge — saved references | `kind: article` files |
| `"obsidian"` | External vault | Obsidian notes |
| `"drive"` | External cloud | Google Drive docs |

### Bootstrap sync strategy

Current: `sync_dir("memory", knowledge_dir)` — all local files under `"memory"`.

After: bootstrap calls a new `sync_knowledge_dir(index, knowledge_dir)` helper that does two passes:
1. `index.sync_dir("memory", knowledge_dir, kind_filter="memory")`
2. `index.sync_dir("local", knowledge_dir, kind_filter="article")`

This requires `sync_dir` to accept an optional `kind_filter` param that reads frontmatter and skips files where `kind` doesn't match.

### Schema migration

On bootstrap, run once: `UPDATE docs SET source='local' WHERE source='memory' AND kind='article'`.

Implemented as `migrate_source_labels()` in `KnowledgeIndex`, called from `run_bootstrap()` before `sync_knowledge_dir`. Migration is idempotent.

### `search_knowledge` scope change

`source=None` (default) → searches `"local"`, `"obsidian"`, `"drive"` only. Does NOT include `"memory"`.

Model can still search memories explicitly via:
- `search_memories(query)` — new dedicated tool (semantic search on `source="memory"`)
- `search_knowledge(source="memory")` — explicit override (kept as escape hatch, documented)

### `search_memories` tool

New agent-registered tool. Thin wrapper over the existing FTS path:
- `search_memories(ctx, query, *, limit=10, tags=None, tag_match_mode="any", created_after=None, created_before=None)`
- Calls `knowledge_index.search(query, source="memory", kind="memory", ...)`.
- Grep fallback: `_grep_recall(memories, query, limit)` — same as current `recall_memory` grep path.
- Returns same `{display, count, results}` schema as `search_knowledge`.
- No confidence scoring or contradiction detection (memory-specific, can be added later).
- `requires_approval` inherits from agent registration — default `all_approval` mode.

Guard conditions (matching `search_knowledge` peer):
- Empty `query` → return `{"display": "Query is required.", "count": 0, "results": []}`.
- `limit < 1` → `{"display": "limit must be >= 1", "count": 0, "results": []}`.

## Implementation Plan

### TASK-1: Add `kind_filter` to `KnowledgeIndex.sync_dir`

files:
- `co_cli/knowledge_index.py`

Add optional `kind_filter: str | None = None` parameter to `sync_dir`. When set, skip files
where the frontmatter `kind` field doesn't match. When `kind_filter=None`, behavior unchanged.

Note: `sync_dir` already calls `parse_frontmatter(raw)` on each file's content (confirmed at
`knowledge_index.py:840`). Adding `kind_filter` is a simple skip condition on already-read
data — no new I/O or import needed. Extract `fm.get("kind", "memory")` (default `"memory"`
when absent, matching frontmatter convention).

done_when:
- `grep -n "kind_filter" co_cli/knowledge_index.py` returns a match on the `sync_dir` signature.
- `uv run pytest tests/test_knowledge_index.py -q` passes.

### TASK-2: Add `migrate_source_labels` to `KnowledgeIndex`

files:
- `co_cli/knowledge_index.py`

Add method:
```python
def migrate_source_labels(self) -> int:
    """Migrate legacy source='memory' article rows to source='local'.
    Idempotent — safe to call on every bootstrap.
    Returns count of rows updated.
    """
```

Implementation — two-step to avoid `UNIQUE(source, path)` constraint violation when a row
already exists under `source='local'` for the same path (e.g. from a prior partial run):
```sql
-- Step 1: remove any conflicting 'local' rows for paths we're about to rename
DELETE FROM docs
  WHERE source = 'local'
    AND path IN (SELECT path FROM docs WHERE source = 'memory' AND kind = 'article');

-- Step 2: rename the legacy rows
UPDATE docs SET source = 'local' WHERE source = 'memory' AND kind = 'article';
```

Commit and return `cursor.rowcount` from the UPDATE step.

done_when:
- `grep -n "migrate_source_labels" co_cli/knowledge_index.py` returns a match.
- `uv run pytest tests/test_knowledge_index.py -q` passes.
- New test `test_migrate_source_labels` in `tests/test_knowledge_index.py`: index one article
  under `source="memory"`, call `migrate_source_labels()`, assert row now has `source="local"`.

### TASK-3: Update bootstrap to use split sync and run migration

prerequisites: [TASK-1, TASK-2]

files:
- `co_cli/_bootstrap.py`

Replace:
```python
ctx.deps.knowledge_index.sync_dir("memory", knowledge_dir)
```
With two-pass sync via new helper:
```python
# sync memories and articles under their correct source labels
ctx.deps.knowledge_index.migrate_source_labels()
ctx.deps.knowledge_index.sync_dir("memory", knowledge_dir, kind_filter="memory")
ctx.deps.knowledge_index.sync_dir("local",  knowledge_dir, kind_filter="article")
```

Migration runs first so existing rows are corrected before any re-sync overwrites.

done_when:
- `grep -n "migrate_source_labels\|sync_dir.*local\|sync_dir.*memory" co_cli/_bootstrap.py` returns matches for both `sync_dir` calls and the migration call.
- `uv run pytest tests/test_bootstrap.py -q` passes.
- New test in `tests/test_bootstrap.py`: write one `kind: memory` file and one `kind: article`
  file, run bootstrap, assert memory file is searchable under `source="memory"` and article
  file is searchable under `source="local"`.

### TASK-4: Update article write paths to use `source="local"`

prerequisites: [TASK-2, TASK-3]

files:
- `co_cli/tools/articles.py`
- `co_cli/memory_lifecycle.py`

Changes in `co_cli/tools/articles.py`:
1. `save_article()` new file write (line ~392): `index(source="local", kind="article", ...)`
2. `save_article()` consolidation reindex (line ~343): `index(source="local", kind="article", ...)`
3. `recall_article()` FTS search (line ~455): `search(source="local", kind="article", ...)`

Grep fallback guard in `search_knowledge`: update `source != "memory"` to
`source not in (None, "memory", "local")`.

Changes in `co_cli/memory_lifecycle.py` (consolidation reindex path, lines ~158–172):
The consolidation reindex block calls `index(source="memory", kind=fm.get("kind","memory"), ...)`.
If a consolidated file has `kind: article` (pathological edge case), it would be re-stamped as
`source="memory"` after migration corrected it. Add source derivation:
```python
entry_kind = fm.get("kind", "memory")
entry_source = "local" if entry_kind == "article" else "memory"
# then: index(source=entry_source, kind=entry_kind, ...)
```

done_when:
- `grep -n 'source.*"local"' co_cli/tools/articles.py` returns matches in save and recall paths.
- `uv run pytest tests/test_save_article.py -q` passes.

### TASK-5: Update `search_knowledge` default scope to exclude memories

prerequisites: [TASK-4]

files:
- `co_cli/tools/articles.py`

Change the fallback guard and the FTS default scope:

**Fallback branch changes:**

Derive `effective_kind` from both `kind` and `source` so the `source="memory"` escape hatch
still works in grep-only mode:
```python
if kind is not None:
    effective_kind = kind
elif source == "memory":
    effective_kind = "memory"   # explicit memory query — escape hatch
else:
    effective_kind = "article"  # default: knowledge only
memories = _load_memories(knowledge_dir, kind=effective_kind)
```

Update fallback return dict: `"source": "local"` when `effective_kind != "memory"`, else
`"source": "memory"`.

Update fallback short-circuit guard: `source not in (None, "memory", "local")` returns empty
(obsidian/drive require FTS).

**FTS branch changes:**

When `source is None`, the FTS `search()` call has no source filter and returns all rows
including `source="memory"`. After partition, default `search_knowledge` must exclude memories.
Pass an explicit source list instead of `None`:
```python
fts_source = source if source is not None else ["local", "obsidian", "drive"]
results = ctx.deps.knowledge_index.search(query, source=fts_source, ...)
```

This requires `KnowledgeIndex.search()` to accept `source: str | list[str] | None`. Add
list support to **both** `_fts_search` and `_vec_search`: when source is a list, generate
`WHERE source IN (?, ?, ?)` in each method. `_vec_search` at `knowledge_index.py:448` has
the identical scalar `AND source = ?` pattern — it must receive the same IN-clause expansion
or hybrid-mode queries will hit a runtime type error with list source values.

Update docstring source filter shortcuts:
- `source="local"` → local articles only
- `source="memory"` → memories only (explicit override, not default)

files:
- `co_cli/tools/articles.py`
- `co_cli/knowledge_index.py` (add list support to `search()` source parameter)

done_when:
- `grep -n "effective_kind" co_cli/tools/articles.py` returns match in fallback branch.
- `grep -n 'source.*obsidian.*drive\|fts_source' co_cli/tools/articles.py` returns match showing explicit source list for FTS default.
- `uv run pytest tests/test_save_article.py -q` passes.
- `grep -n "isinstance.*list\|IN.*source" co_cli/knowledge_index.py` returns a match showing list support added to `search()` **and** `_vec_search()`.
- New test: call `search_knowledge(query)` with no source filter; assert no memory results returned. Call `search_knowledge(query, source="memory")` with `ctx.deps.knowledge_index = None` to force grep path; assert memory results returned.

### TASK-6: Fix memory lifecycle boundary — count only memories for retention and dedup

prerequisites: []

files:
- `co_cli/memory_lifecycle.py`

Two fixes:

Fix 1 — Initial full load in `_persist_memory_inner` (line ~125):
```python
# Before (loads both memories and articles):
all_items = _load_memories(memory_dir)
# After (memories only):
all_items = _load_memories(memory_dir, kind="memory")
```

Note: this single load feeds three downstream uses — dedup candidate window filtering,
consolidation candidate selection, and next-id computation. Split into two explicit calls to
avoid article/memory ID collisions (they share the same numeric ID sequence):

```python
# For next-id: must include all items (memories + articles share the ID sequence)
all_items_for_id = _load_memories(memory_dir)
max_id = max((m.id for m in all_items_for_id), default=0)

# For dedup/consolidation candidates: memories only
all_items = _load_memories(memory_dir, kind="memory")
```

Replace the existing single `_load_memories(memory_dir)` call with these two calls.
The `max_id` value feeds the new-file ID assignment; `all_items` feeds dedup/consolidation.

Fix 2 — Retention cap count (line ~265):
```python
# Before (counts both):
all_items = _load_memories(memory_dir)
# After (memories only):
all_items = _load_memories(memory_dir, kind="memory")
```

Rationale: retention cap (`memory_max_count`) is a memory budget, not a knowledge budget.
Articles are decay-protected and must not be evicted by memory pressure.

done_when:
- `uv run pytest tests/test_memory_lifecycle.py -q` passes.
- `uv run pytest tests/test_memory_decay.py -q` passes.
- New assertion in `tests/test_memory_lifecycle.py`: set `memory_max_count=3`, save 4 memories
  + 1 article; assert article is not deleted and oldest non-protected memory is deleted.

### TASK-7: Add `search_memories` agent tool

prerequisites: [TASK-5]

files:
- `co_cli/tools/memory.py`
- `co_cli/agent.py`

Add `search_memories` to `co_cli/tools/memory.py`:

```python
async def search_memories(
    ctx: RunContext[CoDeps],
    query: str,
    *,
    limit: int = 10,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict[str, Any]:
    """Semantic search over saved memories..."""
```

Guard conditions:
- `not query.strip()` → `{"display": "Query is required.", "count": 0, "results": []}`
- `limit < 1` → `{"display": "limit must be >= 1.", "count": 0, "results": []}`

FTS path: `ctx.deps.knowledge_index.search(query, source="memory", kind="memory", ...)`
Grep fallback: `_grep_recall(_load_memories(knowledge_dir, kind="memory"), query, limit)`
Return schema: `{display, count, results}` — same shape as `search_knowledge`.

Register in `agent.py` alongside other memory tools with `requires_approval` matching
`list_memories` (all_approval mode).

done_when:
- `grep -n "search_memories" co_cli/agent.py` returns a match.
- `uv run pytest tests/test_memory.py -q` passes.
- New test in `tests/test_memory.py`: save two memories, call `search_memories`, assert both
  appear in results with correct `source="memory"`.

### TASK-8: Update `DESIGN-knowledge.md` — source namespace table + partition design

prerequisites: [TASK-3, TASK-4, TASK-5, TASK-6, TASK-7]

files:
- `docs/DESIGN-knowledge.md`
- `docs/TODO-retrieval-surfaces-2.5-alignment.md`

Changes:
1. §2.1 source namespace table: add `"local"` row; clarify `"memory"` is memories-only.
2. §2.3 bootstrap flow: reflect two-pass `sync_dir` and migration call.
3. §2.5 `search_knowledge` signature and default scope: reflect `source=None` excludes memories.
4. §2.5 new `search_memories` entry under agent-registered retrieval tools.
5. §2.6 write surfaces: `save_article` → `source="local"` indexing.
6. Add partition overview in §2.1 or a new §2.0: memory subsystem vs knowledge subsystem.

Also add a note to `docs/FIX-retrieval-surfaces-2.5-alignment.md`: this delivery supersedes
the fixes planned there; doc fixes are now subsumed by the full partition redesign.

Add a top-level superseded note to `docs/TODO-retrieval-surfaces-2.5-alignment.md`:
```
> **Superseded by `memory-knowledge-partition` delivery — TASK-8 covers all §2.5 fixes.
> Do not run `/orchestrate-dev retrieval-surfaces-2.5-alignment`.**
```
This prevents a future `/orchestrate-dev retrieval-surfaces-2.5-alignment` run from producing
conflicting or redundant edits to §2.5 of `DESIGN-knowledge.md`.

done_when:
- `grep -F '"local"' docs/DESIGN-knowledge.md` returns a match in the source namespace table.
- `grep -n "search_memories" docs/DESIGN-knowledge.md` returns a match in the retrieval tools list.
- `grep -F 'source="memory"' docs/DESIGN-knowledge.md` returns no match in the article write path description.
- `grep -F "Superseded" docs/TODO-retrieval-surfaces-2.5-alignment.md` returns a match.

## Testing

- TASK-1: `test_knowledge_index.py` — sync_dir with kind_filter.
- TASK-2: `test_knowledge_index.py` — `test_migrate_source_labels` (new).
- TASK-3: `test_bootstrap.py` — verify two-pass sync and migration called.
- TASK-4/5: `test_save_article.py` — verify `source="local"` in saved/recalled articles.
- TASK-6: `test_memory_lifecycle.py`, `test_memory_decay.py` — retention isolation (new assertion).
- TASK-7: `test_memory.py` — `search_memories` functional test (new).
- TASK-8: doc-only — verified by grep done_when checks.

Run all after final task: `uv run pytest tests/ -q`

## Open Questions

None — all answerable from code inspection.


## Final — Team Lead

Plan approved. Three cycles completed — C1 had 4 blocking items, C2 had 2, C3 approved clean.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-knowledge-partition`
