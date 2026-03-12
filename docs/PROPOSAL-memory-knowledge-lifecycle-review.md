# Proposal: Memory and Knowledge Lifecycle Review

## Purpose

This document captures a code-level review of the current memory and knowledge lifecycles in the latest codebase. It clarifies:

- whether memory and knowledge have separate lifecycles
- whether they share the same retrieval services
- whether they share all or only part of the retrieval processing logic
- concrete issues in the current implementation
- refactoring directions to improve correctness, separation of concerns, and maintainability

This is based on the current implementation in:

- [`co_cli/_memory_lifecycle.py`](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py)
- [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py)
- [`co_cli/tools/articles.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py)
- [`co_cli/_knowledge_index.py`](/Users/binle/workspace_genai/co-cli/co_cli/_knowledge_index.py)
- [`co_cli/_bootstrap.py`](/Users/binle/workspace_genai/co-cli/co_cli/_bootstrap.py)
- [`co_cli/main.py`](/Users/binle/workspace_genai/co-cli/co_cli/main.py)
- [`co_cli/_history.py`](/Users/binle/workspace_genai/co-cli/co_cli/_history.py)

## Executive Summary

Memory and knowledge/article data do have separate lifecycles at the product and workflow level, but they converge on a shared retrieval infrastructure. The current architecture is best described as:

- separate write and maintenance lifecycles
- shared retrieval backend
- partially shared retrieval logic
- partially blurred boundaries in helper reuse and metadata conventions

The intended separation is clear:

- memory is project-local conversational state
- knowledge/articles are user-global external references

The implemented separation is only partial:

- both are persisted as markdown plus frontmatter
- both are indexed into the same `KnowledgeIndex`
- both reuse some of the same file-loading and grep helpers
- some memory-specific behaviors mutate files during recall and do not fully keep the index in sync

The highest-risk issues are not architectural style problems. They are correctness problems:

1. memory recall “gravity” does not meaningfully affect indexed retrieval
2. recall-time dedup mutates files without fully reindexing the survivor
3. code comments and helper structure imply stronger lifecycle unification than the runtime actually has

## Current Architecture

### Storage scopes

The runtime creates two distinct filesystem roots in [`co_cli/main.py`](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L162):

- memory directory: project-local, `Path.cwd() / ".co-cli" / "memory"`
- library directory: user-global, `settings.library_path` or data dir `/library`

This is the first and strongest lifecycle boundary.

Memory is local to the current workspace. Articles are global to the user’s machine.

### Shared retrieval service

Despite separate storage roots, both memory and articles share one retrieval/index service:

- `CoServices.knowledge_index`

That service is created once in [`co_cli/main.py`](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L91) and used throughout the session.

Bootstrap sync in [`co_cli/_bootstrap.py`](/Users/binle/workspace_genai/co-cli/co_cli/_bootstrap.py#L34) explicitly indexes both:

- `sync_dir("memory", memory_dir, kind_filter="memory")`
- `sync_dir("library", library_dir, kind_filter="article")`

So the lifecycle split is not “separate systems.” It is “separate domains feeding one index.”

### Shared index model

`KnowledgeIndex` in [`co_cli/_knowledge_index.py`](/Users/binle/workspace_genai/co-cli/co_cli/_knowledge_index.py#L225) is a single SQLite-backed index with a `source` discriminator:

- `memory`
- `library`
- `obsidian`
- `drive`

Memory is indexed in `docs` and searched through the `docs_fts` leg.
Non-memory sources are indexed in `docs` plus `chunks`, and searched through the `chunks_fts` leg.

That means retrieval sharing is real, but asymmetric:

- memory uses the document leg
- articles use the chunk leg

This distinction matters when evaluating whether the systems “share retrieval logic.”

## Lifecycle Comparison

## Memory Lifecycle

Memory write entrypoint is [`persist_memory()`](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py#L65).

Current memory lifecycle:

1. A save enters through `save_memory()` or the auto-signal path.
2. Memory dedup checks recent memories by content similarity.
3. Optional LLM consolidation may issue `ADD`, `UPDATE`, or `DELETE`.
4. A new memory file may be written.
5. The new file is indexed into `KnowledgeIndex` with `source="memory"`.
6. Retention may delete old memory files when count exceeds the cap.
7. Stale index entries are removed if retention deleted files.
8. On recall, memories may be rescored, deduplicated again, relation-expanded, and touched.

Key properties:

- can be auto-saved
- can be deduplicated by similarity
- can be consolidated with LLM assistance
- can be evicted by retention
- can be injected into the prompt automatically
- can mutate during read-time recall

Relevant code:

- [`persist_memory()`](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py#L65)
- [`recall_memory()`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L512)
- [`inject_opening_context()`](/Users/binle/workspace_genai/co-cli/co_cli/_history.py#L540)
- [`enforce_retention()`](/Users/binle/workspace_genai/co-cli/co_cli/_memory_retention.py#L17)

## Knowledge/Article Lifecycle

Article write entrypoint is [`save_article()`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L318).

Current article lifecycle:

1. An external content item is explicitly saved as an article.
2. Dedup is by exact `origin_url`, not by semantic or text similarity.
3. A new article file is written to the global library, or an existing one is updated in place.
4. The article is indexed into `KnowledgeIndex` with `source="library"`.
5. The article body is chunked and written into the chunks tables.
6. Retrieval occurs via `recall_article()` or `search_knowledge()`.
7. There is no retention policy analogous to memory retention.
8. There is no automatic prompt injection analogous to memory recall injection.

Key properties:

- explicit save only
- dedup by URL identity
- decay-protected
- chunk-indexed
- not automatically injected into the conversational context
- not subject to memory-style retention or consolidation

Relevant code:

- [`save_article()`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L318)
- [`recall_article()`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L454)
- [`search_knowledge()`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L170)

## Answer: Do They Have Separate Lifecycles?

Yes, they have separate lifecycles in the parts that matter operationally.

They differ in:

- storage scope
- write triggers
- dedup criteria
- maintenance behavior
- retention behavior
- runtime injection behavior
- retrieval presentation

### Separation by storage scope

Memory is workspace-scoped and project-local.
Articles are global and library-scoped.

That alone makes their lifecycle different from a user-facing perspective.

### Separation by write trigger

Memory can be:

- explicitly saved
- auto-saved from signal analysis

Articles can only be:

- explicitly saved

### Separation by dedup/consolidation policy

Memory uses:

- similarity dedup
- optional LLM consolidation plan

Articles use:

- exact URL-based dedup

### Separation by retention

Memory is capacity-managed and can be evicted.
Articles are decay-protected and effectively retained indefinitely.

### Separation by runtime use

Memory is used as active conversational state and is injected automatically through [`inject_opening_context()`](/Users/binle/workspace_genai/co-cli/co_cli/_history.py#L540).

Articles are passive reference material and only surface when explicitly searched or read.

## Answer: Do They Share the Same Retrieval Services?

Yes.

They share the same retrieval service at the infrastructure layer:

- `CoServices.knowledge_index`
- one `KnowledgeIndex` instance
- one `search.db`
- one backend resolution path (`grep`, `fts5`, `hybrid`)

This is set up in [`create_deps()`](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L91) and used across memory and article tools.

### What is shared exactly

Shared retrieval infrastructure includes:

- backend initialization and fallback
- SQLite schema
- FTS search
- hybrid vector search
- reranking
- tag filtering
- date filtering
- stale entry cleanup
- bootstrap sync

### What is not shared

The user-facing retrieval entrypoints are not unified:

- memory: `recall_memory()`, `search_memories()`
- knowledge/articles: `recall_article()`, `search_knowledge()`

They all call into the same index, but they do not use the same higher-level post-processing.

## Answer: Do They Share All or Partial Retrieval Processing Logic?

They share partial retrieval processing logic, not all of it.

## Shared Retrieval Processing

The following logic is shared:

- use of `KnowledgeIndex.search()`
- shared FTS query construction in [`_build_fts_query()`](/Users/binle/workspace_genai/co-cli/co_cli/_knowledge_index.py#L1379)
- shared backend routing between FTS and hybrid
- shared tag filtering and date filtering inside `KnowledgeIndex.search()`
- shared reranking mechanics
- shared source-filter handling through `source`

At the retrieval-core level, this is genuinely shared.

## Divergent Retrieval Processing

### Memory-specific processing

After the index returns memory candidates, `recall_memory()` adds behavior that is specific to memory:

- load full files back into `MemoryEntry`
- apply composite BM25 + decay rescoring
- deduplicate pulled results with `_dedup_pulled()`
- remove stale deleted index entries for dedup casualties
- expand one-hop related memories
- touch recalled memories to update `updated`

Relevant code:

- [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L558)
- [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L653)

This is not generic retrieval logic. It is memory maintenance plus memory-specific presentation.

### Knowledge/article-specific processing

`search_knowledge()` adds different logic:

- optional Obsidian pre-sync
- source scoping across library/obsidian/drive
- per-result confidence calculation
- contradiction detection
- mixed-source formatting

Relevant code:

- [`co_cli/tools/articles.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L170)

This is cross-source retrieval enrichment, not memory maintenance.

### Article-specific retrieval behavior

`recall_article()` is simpler still:

- query library source
- summarize at a result/index level
- avoid memory-style dedup, related-hop expansion, and gravity

## Overall Conclusion on Shared Logic

The current system shares:

- retrieval infrastructure
- low-level search execution
- some file parsing and grep fallback helpers

It does not share:

- full retrieval pipeline behavior
- read-time maintenance semantics
- post-processing strategy
- result shaping

So the right answer is:

- shared retrieval services
- partially shared retrieval logic
- clearly different retrieval pipelines above the shared core

## Issues

## Issue 1: Memory Gravity Does Not Materially Affect Indexed Retrieval

Severity: high

### What the code does

`recall_memory()` computes ranking from:

- BM25-derived score
- decay based on `created`

See [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L604).

Then after retrieval it calls `_touch_memory()` on matched items, which only updates the file frontmatter `updated` field. See:

- [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L689)
- [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L258)

### Why this is a problem

The code comments and docs imply a “gravity” effect where frequently recalled memories become more retrievable. In practice:

- indexed recall scoring uses `created`, not `updated`
- touching does not reindex the document
- the index does not receive the new `updated` timestamp

Therefore, gravity does not change the primary indexed retrieval order.

It only affects:

- grep fallback ordering
- `list_memories()` ordering or display if any logic later uses `updated`

This is a correctness gap between intended behavior and implemented behavior.

### Consequences

- engineers may reason about the system incorrectly
- docs overstate an adaptive property the system does not really have
- recall writes incur I/O without delivering the intended ranking effect

## Issue 2: Recall-Time Dedup Leaves the Surviving Index Entry Stale

Severity: high

### What the code does

`_dedup_pulled()` may:

- merge tags into the surviving memory
- rewrite the surviving file
- delete the older file

See [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L282).

Then `recall_memory()` removes stale index entries for deleted paths, but it does not reindex the surviving updated file. See [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L653).

### Why this is a problem

The file system and the index can diverge:

- survivor file contents may change
- survivor tags may change
- survivor metadata may change
- index may still contain old content and old tags for that path

### Consequences

- search results may use stale snippet content
- tag filtering may be wrong until the next full sync or explicit write path
- debugging becomes harder because the source file and the retrieval store disagree

This is a direct correctness issue, not merely a style issue.

## Issue 3: Read Paths Perform Hidden Maintenance Writes

Severity: medium

### What the code does

`recall_memory()` is nominally a retrieval operation, but it can:

- rewrite matched files through `_touch_memory()`
- delete duplicate files through `_dedup_pulled()`
- partially mutate index state by removing deleted entries

### Why this is a problem

A read path that mutates storage is harder to reason about and test.

It creates several risks:

- retrieval changes state unexpectedly
- index consistency depends on side effects in a read path
- future callers may assume recall is safe and idempotent when it is not

### Architectural impact

This blurs the boundary between:

- retrieval
- lifecycle maintenance
- storage repair

That is exactly the kind of coupling that makes refactoring harder later.

## Issue 4: Lifecycle Boundaries Are Blurred by Shared Helper Naming and Placement

Severity: medium

### What the code does

Article code imports memory-oriented helpers from [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py):

- `_slugify`
- `_load_memories`
- `_grep_recall`

See [`co_cli/tools/articles.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L30).

### Why this is a problem

These helpers are no longer memory-specific in practice, but their names and location imply they are.

That causes:

- conceptual confusion
- accidental coupling between the article and memory modules
- a mental model where “article is just another memory,” which is not the intended lifecycle

### Consequences

Future code will likely keep reusing memory-module internals because they are convenient.
That tends to increase boundary blur over time.

## Issue 5: ID Semantics Are Inconsistent with the Code Comments

Severity: medium

### What the code says

In [`co_cli/_memory_lifecycle.py`](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py#L125), the comment says:

- memories + articles share the ID sequence

### What the code does

Memory ID allocation reads from `memory_dir`.
Article ID allocation reads from `library_dir`.

Those directories are separate in [`co_cli/main.py`](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L162).

So in the current runtime, IDs are not globally shared across memory and articles.

### Why this is a problem

This is a documentation/comment correctness problem and a design ambiguity:

- are IDs meant to be globally unique?
- or only unique within a storage root?

Right now, the codebase communicates both ideas depending on where you look.

### Consequences

- bad assumptions in future migrations or tooling
- potential confusion in UX, logs, or cross-linking semantics

## Issue 6: Retrieval Logic Is Shared, But the Pipeline Stages Are Not Explicitly Separated

Severity: medium

### What the code does

Memory and knowledge retrieval both call `KnowledgeIndex.search()`, but each tool then layers on its own post-processing.

The problem is not that they differ. The problem is that the differences are implicit and scattered.

Examples:

- memory rescoring in `recall_memory()`
- contradiction detection in `search_knowledge()`
- article-only summary shaping in `recall_article()`

### Why this is a problem

The code lacks an explicit retrieval-pipeline abstraction such as:

- fetch candidates
- post-process candidates
- shape result

Instead, each tool implements its own vertical slice.

### Consequences

- duplicated filtering/formatting patterns
- harder to compare behavior across entrypoints
- more likely to introduce drift between “memory search” and “knowledge search”

## Issue 7: Memory Recall Uses File Reloads After Indexed Search

Severity: low to medium

### What the code does

After indexed search returns paths, `recall_memory()` reloads each file from disk and rebuilds `MemoryEntry` objects. See [`co_cli/tools/memory.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L580).

### Why this exists

It is needed because memory recall wants fields and behaviors that are not fully represented in the initial search result pipeline:

- `related`
- `decay_protected`
- full body content
- later maintenance writes

### Why this matters

This is not a correctness bug by itself, but it reveals a shape mismatch between:

- index-level retrieval results
- memory-specific post-processing needs

That is another sign that the retrieval pipeline boundary is not clean.

## Refactoring Proposal

## Goal

Preserve the useful parts of the current architecture:

- shared index service
- one retrieval backend
- source-based routing
- separate product semantics for memory and knowledge

But make the system easier to reason about by:

- separating lifecycle ownership more clearly
- removing hidden read-time writes or making them explicit
- centralizing index synchronization guarantees
- making shared versus source-specific retrieval stages explicit

## Proposal 1: Introduce Explicit Store Layer per Domain

Create two domain stores:

- `MemoryStore`
- `ArticleStore`

Each store should own:

- file layout
- frontmatter normalization
- ID allocation
- write/update logic
- delete logic
- index synchronization calls

### Suggested responsibilities

`MemoryStore`

- `save()`
- `update_existing()`
- `append()`
- `load_entries()`
- `delete()`
- `enforce_retention()`
- `reindex()`

`ArticleStore`

- `save()`
- `consolidate_by_url()`
- `load_entries()`
- `delete()`
- `reindex()`
- `index_chunks()`

### Benefits

- article code no longer imports “memory” helpers
- ID policy becomes explicit per store
- indexing guarantees can be enforced in one place
- file mutation behavior stops leaking through tool modules

## Proposal 2: Keep One Shared Retrieval Core, But Make Pipeline Stages Explicit

Retain `KnowledgeIndex` as the shared retrieval engine.

Add an internal retrieval pipeline abstraction:

1. retrieve raw candidates
2. apply source/domain-specific post-processing
3. shape tool response

### Example shape

`retrieve_candidates(query, source, kind, filters) -> list[SearchResult]`

`postprocess_memory_results(candidates, ctx) -> list[MemoryRecallResult]`

`postprocess_knowledge_results(candidates, ctx) -> list[KnowledgeResult]`

`format_memory_results(...)`

`format_knowledge_results(...)`

### Benefits

- shared logic stays shared
- divergent logic becomes explicit
- easier testing of each stage
- easier reasoning about what “shared retrieval logic” actually means

## Proposal 3: Remove Recall-Time Writes, or Make Them Fully First-Class

This needs a product decision.

### Option A: Make recall read-only

Recommended default.

Remove from `recall_memory()`:

- `_touch_memory()`
- `_dedup_pulled()`

Move those behaviors into:

- write-time lifecycle maintenance
- explicit cleanup tasks
- bootstrap maintenance pass

### Why this is better

- read paths become safe and predictable
- retrieval no longer causes hidden storage mutation
- index consistency becomes easier to guarantee

### Option B: Keep recall-time maintenance, but formalize it

If gravity and recall-time cleanup are important product features, then they need to be implemented as first-class lifecycle operations:

- touch must reindex
- gravity ranking must actually use `updated` if that is the intended signal
- dedup survivor must be reindexed immediately
- maintenance side effects should be explicit in code and docs

### Minimum fixes if Option B is chosen

1. after `_touch_memory()`, reindex the touched memory
2. after `_dedup_pulled()`, reindex the surviving memory
3. decide whether recall ranking should use `updated` instead of only `created`
4. document recall as a mutating maintenance path, not a pure retrieval path

## Proposal 4: Clarify and Normalize ID Semantics

Pick one policy and enforce it everywhere.

### Option A: IDs are domain-local

Meaning:

- memory IDs are unique only within memory storage
- article IDs are unique only within article storage

If so:

- fix misleading comments
- stop implying a shared sequence
- ensure cross-links never assume global uniqueness by ID alone

### Option B: IDs are globally unique across both domains

Meaning:

- use a single allocator or metadata registry
- both `MemoryStore` and `ArticleStore` call into it

This is only worth doing if the product really needs cross-domain ID consistency.

### Recommendation

Use Option A.

The storage scopes are already separate. The product semantics are different. A global sequence adds complexity with little value.

## Proposal 5: Move Generic File Helpers Out of `tools/memory.py`

Create a neutral module for shared content-file operations, for example:

- `co_cli/_knowledge_files.py`

Move generic helpers there:

- slugify
- markdown/frontmatter load
- generic grep recall
- generic entry loading if still needed

Then keep domain-specific helpers in the domain module only.

### Benefits

- reduces conceptual drift
- removes misleading dependency direction
- makes article code independent from memory module internals

## Proposal 6: Make Index Sync Guarantees Centralized

Any file mutation should have one of two properties:

1. it always reindexes the mutated document and removes stale rows immediately
2. it is explicitly part of a deferred sync model with a documented delay

Right now, the system mixes the two styles.

### Recommendation

Use immediate sync for direct writes and deletes originating from lifecycle code.

That includes:

- memory save
- memory update
- memory append
- article save
- article consolidate
- retention delete
- dedup delete
- any survivor updates during dedup
- any touch writes if gravity remains

### Benefits

- index/file consistency becomes a hard invariant
- bootstrap sync remains a recovery mechanism, not a normal correctness dependency

## Proposed Target Architecture

```text
Memory Tool / Signal Path
  -> MemoryStore
  -> KnowledgeIndexGateway

Article Tool
  -> ArticleStore
  -> KnowledgeIndexGateway

Retrieval Tools
  -> RetrievalCore (shared query execution)
  -> MemoryRetrievalPipeline or KnowledgeRetrievalPipeline
  -> Formatter
```

### Domain ownership

Memory domain owns:

- similarity dedup
- consolidation
- retention
- related traversal
- prompt injection

Article domain owns:

- URL dedup
- chunk indexing
- article summary/detail access

Shared retrieval core owns:

- source routing
- FTS/hybrid execution
- tag/date filtering
- reranking
- result transport objects

## Recommended Implementation Order

## Phase 1: Correctness fixes

Do these first.

1. Fix recall-time dedup reindexing of the surviving memory.
2. Decide whether gravity stays.
3. If gravity stays, reindex after touch and update ranking logic to use the intended field.
4. Fix the misleading shared-ID comment in memory lifecycle.

## Phase 2: Boundary cleanup

1. Move generic file helpers out of `tools/memory.py`.
2. Introduce store-layer helpers for memory and article writes.
3. Remove article dependence on memory-named helpers.

## Phase 3: Retrieval pipeline cleanup

1. Extract shared retrieval-core invocation into a reusable internal adapter.
2. Extract memory-specific post-processing into a named pipeline.
3. Extract knowledge/article-specific post-processing into a named pipeline.

## Phase 4: Optional product refinement

1. Reevaluate whether memory recall should mutate state at all.
2. Reevaluate whether confidence and contradiction detection belong in the tool layer or a shared post-processing layer.
3. Reevaluate whether memory and knowledge should share one result model or use domain-specific result types.

## Recommended Decisions

If only a small number of changes are feasible now, the recommended decisions are:

1. keep separate lifecycles for memory and knowledge
2. keep one shared `KnowledgeIndex`
3. explicitly treat retrieval logic as partially shared, not fully shared
4. remove or fully formalize read-time memory mutations
5. separate generic helpers from memory-specific helpers
6. define IDs as domain-local unless there is a strong product reason not to

## Final Answers

### Do memory and knowledge have separate lifecycles?

Yes.

They are separate in storage scope, write triggers, dedup rules, retention rules, and runtime use.

### Do they share the same retrieval services?

Yes.

They share the same `KnowledgeIndex` service, backend resolution, and index database.

### Do they share all or only part of retrieval processing logic?

Only part.

They share the retrieval engine and some common filtering/reranking infrastructure, but their post-retrieval processing and lifecycle semantics are different.

### Main issues

- gravity does not materially affect indexed recall
- recall-time dedup can leave the survivor stale in the index
- retrieval performs hidden maintenance writes
- article and memory boundaries are blurred by helper reuse
- ID semantics are inconsistent between comments and implementation

### Main refactoring direction

Keep one shared retrieval backend, but separate lifecycle ownership and post-processing pipelines more clearly.
