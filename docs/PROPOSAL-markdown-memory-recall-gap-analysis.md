# Proposal: Markdown Memory Recall Gap Analysis

## Purpose

This document compares the current memory and knowledge implementation against the current best-practice direction for file-based agent memory:

- minimal always-loaded instruction memory
- many small markdown knowledge units
- lexical-first retrieval with optional semantic/rerank
- section/chunk-level recall instead of whole-file injection
- explicit compaction and budget control

The goal is not to redesign everything at once. The goal is to identify where the latest code already aligns, where it materially diverges, and what should change first.

Reviewed code:

- `co_cli/tools/memory.py`
- `co_cli/_memory_lifecycle.py`
- `co_cli/_memory_retention.py`
- `co_cli/_history.py`
- `co_cli/_knowledge_index.py`
- `co_cli/tools/articles.py`
- `co_cli/config.py`
- `docs/DESIGN-memory.md`
- `docs/DESIGN-knowledge.md`

## Executive Summary

The current system is already close to a pragmatic file-based memory architecture:

- markdown files are the source of truth
- SQLite FTS5/hybrid search is the primary retrieval layer
- memory and articles are separated by lifecycle and storage scope
- non-memory knowledge already uses chunked retrieval

The biggest gaps are not in the write path. They are in recall behavior and memory granularity:

1. memory retrieval is still document-level, while non-memory knowledge is chunk-level
2. opening-context injection sends a formatted whole-memory block, not bounded snippets
3. recall mutates memory files on read, but does not fully keep the derived index in sync
4. the so-called grep fallback is an in-process full directory scan, not a true lexical retrieval pipeline
5. there is no hard size budget for memory files or injected recall payloads

If the target is a frontier-style markdown-first memory system, the near-term direction should be:

- keep markdown as source of truth
- keep FTS5 as the default lexical engine
- add chunked recall for memory files
- stop injecting full memory display blocks
- make recall read-only, or make its write side-effects transactionally indexed
- add explicit file and prompt budget limits

## Current State

### What already matches best practice

The current implementation already follows several strong patterns.

#### 1. Files are the source of truth

Memory lives in project-local markdown files under `.co-cli/memory/`. Articles live in markdown files under the user-global library. `search.db` is derived and rebuildable. That is the right base architecture for a file-first system.

#### 2. Retrieval is not prompt-only

The system does not rely on a giant persistent prompt. It stores memory externally and retrieves it on demand through `KnowledgeIndex`. That aligns with current agent practice.

#### 3. The knowledge system already separates raw storage from recall units

For articles and external knowledge, the code already chunks content and retrieves the best chunk/document match through `chunks` and `chunks_fts`. This is materially closer to current best practice than whole-document loading.

#### 4. Memory and reference knowledge have different lifecycles

Memory uses dedup, optional LLM consolidation, retention, and proactive injection. Articles use explicit saves, URL dedup, chunk indexing, and no retention cap. That separation is correct.

### What the current system actually does

#### Memory write path

`persist_memory()` in `co_cli/_memory_lifecycle.py` does:

1. recent duplicate detection
2. optional LLM consolidation
3. markdown write
4. memory index write
5. count-based retention

This is reasonably sound for a v1 file-based memory writer.

#### Memory recall path

`recall_memory()` in `co_cli/tools/memory.py` does:

1. `KnowledgeIndex.search(... source="memory")` when FTS/hybrid is available
2. load matching memory files from disk
3. rescore them with a BM25/decay composite
4. dedup pulled results
5. expand one-hop related memories
6. touch pulled memories by rewriting `updated`
7. return a formatted display block containing full memory bodies

#### Automatic context injection

`inject_opening_context()` in `co_cli/_history.py` runs every new user turn, calls `recall_memory()` with the full latest user message, then injects:

`Relevant memories:\n{result["display"]}`

This is the most important place where the current design diverges from emerging best practice.

## Detailed Gap Analysis

## Gap 1: Memory recall is document-level, not section-level

### Current behavior

Memory is indexed and retrieved at whole-document granularity. The memory leg of `KnowledgeIndex` uses `docs` and `docs_fts`. Memory never uses `chunks` or `chunks_fts`.

### Why this matters

This makes memory retrieval asymmetric with article retrieval:

- articles can recall the most relevant chunk from a long file
- memories can only recall the whole file

That is acceptable while each memory file is tiny. It becomes a real quality and token-budget problem as soon as memory files accumulate appended facts, session checkpoints, or multi-paragraph content.

### Gap

The code currently assumes memory files remain compact, but it does not enforce that assumption.

### Proposal

Treat memory like chunkable knowledge once a file exceeds a small threshold.

Recommended direction:

- keep very small memories as one logical unit
- chunk larger memory files by heading/paragraph
- retrieve memory chunks, not only memory docs
- preserve document-level metadata and path identity

Recommended thresholds:

- always-loaded instruction files: under 200 lines
- normal memory files: aim for under 1.5 KB body text
- hard split threshold: 4 KB or any multi-topic file

## Gap 2: Opening-context injection uses full formatted memory bodies

### Current behavior

`inject_opening_context()` injects the `display` returned by `recall_memory()`. That display contains:

- memory IDs
- created dates
- tags
- full body text
- related memory bodies

### Why this matters

This couples the prompt payload to a user-facing rendering format instead of a retrieval-specific context format. It also inflates tokens with presentation noise and loads more text than necessary.

### Gap

There is no explicit injection budget, no snippet budget, and no distinction between:

- data returned to the agent for reasoning
- display returned to the user
- prompt context injected automatically

### Proposal

Split recall output into separate channels:

- `results`: structured retrieval data
- `display`: user-facing rendering
- `injection`: bounded, retrieval-specific context payload

Recommended injection policy:

1. take top 2 to 4 results only
2. inject heading/path/tag metadata plus matched snippet
3. cap per-result injected text
4. cap total injected text for the turn

Recommended default budget:

- 200 to 400 tokens total for automatic memory injection
- 60 to 120 tokens per recalled memory unit

## Gap 3: Recall mutates files on read without full index synchronization

### Current behavior

`recall_memory()` performs two read-time mutations:

- `_dedup_pulled()` may merge similar pulled memories, update the survivor, and delete the loser
- `_touch_memory()` rewrites the file to refresh `updated`

After dedup-on-read, deleted paths are removed from the index, but the surviving updated memory is not reindexed in that path. After touch-on-read, the touched memory is not reindexed either.

### Why this matters

The system treats markdown as source of truth and `search.db` as a derived view. Read-time writes without immediate reindex create a stale derived view:

- `updated` in the file diverges from `updated` in `docs`
- changed tags/content on the survivor may diverge from indexed metadata
- recall order and future search ranking become dependent on whether search uses file reload or DB metadata

### Gap

Read-time mutation currently violates the "derived index stays in sync" invariant.

### Proposal

Choose one of two designs and make it explicit:

#### Option A: Read-only recall

- remove touch-on-read
- remove dedup-on-read
- keep recall purely observational
- move all consolidation to write-time or background maintenance

This is the cleaner design.

#### Option B: Mutating recall with transactional reindex

If recall is allowed to mutate:

- reindex the survivor immediately after dedup
- reindex touched memories after `updated` changes
- perform mutation + index update as one logical operation

My recommendation is Option A. Read paths should stay read-only unless there is a strong product reason otherwise.

## Gap 4: The grep fallback is not a true lexical retrieval backend

### Current behavior

The fallback path called "grep" does not shell out to `rg` or use an indexed lexical engine. It loads all markdown files through `_load_memories()` and then does Python substring matching in `_grep_recall()`.

### Why this matters

This has three consequences:

- it scales with full file count and file size
- it does not exploit filename/path signal
- it is weaker than the repository’s own preferred retrieval toolchain (`rg`)

### Gap

The implementation and the naming diverge. The current fallback is a directory scan, not grep.

### Proposal

Either rename it to `scan` for accuracy or replace it with a real lexical fallback:

1. path prefilter with `rg --files`
2. body search with `rg -n -i`
3. parse only matched files
4. assemble section/snippet results from the matched spans

Even if SQLite FTS remains the default, a true lexical fallback is a better match for markdown-first systems than the current Python substring scan.

## Gap 5: No enforced file-size or memory-unit budget

### Current behavior

The docs recommend short memory content, but the code does not enforce:

- max body size
- max line count
- max appended growth
- auto-splitting of broad memories
- separate handling for checkpoint/session files vs preference/rule memories

### Why this matters

Pure file-based systems work best when each file is cohesive. Once a file becomes a gist dump, both retrieval quality and injection efficiency degrade.

### Gap

The current system depends on agent discipline rather than system-enforced memory shape.

### Proposal

Add hard and soft limits.

Recommended policy:

- soft warning above 800 to 1200 characters
- hard split or rejection above 4 KB body size
- do not append unrelated facts to an existing memory
- session checkpoints should be a different kind with different retrieval policy

Suggested kinds:

- `memory`: durable user/project fact
- `session`: episodic checkpoint, low recall priority
- `rule`: compact procedural instruction, high recall priority
- `article`: external reference

## Gap 6: Query formulation for auto-recall is too raw

### Current behavior

Automatic recall uses the entire latest user message as the search query.

### Why this matters

Long natural-language prompts often contain:

- filler tokens
- multiple intents
- transient wording
- shell/code content that should not dominate retrieval

FTS can tolerate some of this, but retrieval quality is still worse than using a compact topic query.

### Gap

There is no query rewriting, keyword extraction, or topic condensation before recall.

### Proposal

Add a cheap recall-query normalization step before search:

- extract 3 to 8 keywords from the user turn
- prefer nouns, entities, tools, frameworks, file names, and stable topics
- strip boilerplate and procedural language

This can be deterministic first. It does not need an LLM.

## Gap 7: Automatic memory injection has no source-class priority model

### Current behavior

All recalled memories are formatted similarly. Related memories are appended. There is no explicit priority distinction between:

- procedural rules
- stable preferences
- recent episodic notes
- session checkpoints

### Why this matters

Frontier agent systems increasingly separate:

- procedural memory that is almost instruction-like
- semantic memory for stable facts
- episodic traces for history

Without this distinction, low-value episodic material can crowd out higher-value operating constraints.

### Gap

The metadata schema is not yet strong enough to support priority-based recall and injection.

### Proposal

Promote source-class priority into retrieval and injection.

Suggested priority order:

1. `rule`
2. stable `memory`
3. linked `memory`
4. `session`

This can be implemented as a rerank feature before injection, even if the storage remains markdown.

## Gap 8: Retention is count-only, not budget-aware

### Current behavior

Retention uses `memory_max_count` only.

### Why this matters

In a markdown-first system, 200 tiny rule files and 200 large session dumps are not equivalent. Count-only retention does not control:

- disk growth
- retrieval set size
- average prompt injection cost

### Gap

The current retention policy controls file count but not information volume.

### Proposal

Keep count-based retention, but add a second budget:

- `memory_max_bytes` or `memory_max_chars`

Apply stricter retention or lower retrieval priority to `session` memories first.

## Gap 9: The current index model is more advanced for articles than for memory

### Current behavior

Articles support:

- chunk indexing
- chunk-level FTS
- chunk-level vector search
- chunk-aware reranking

Memory supports:

- doc-level FTS
- doc-level vector search
- no chunk-aware recall

### Why this matters

The system’s most agent-critical retrieval surface, memory recall, is less sophisticated than the article retrieval path.

### Gap

The architecture already contains the machinery needed for better memory retrieval, but memory is not using it.

### Proposal

Unify retrieval mechanics at the index layer:

- allow chunked indexing for memory documents above a threshold
- preserve small-memory fast path
- reuse existing chunk/rerank infrastructure for memory

This is the highest-leverage architectural improvement after fixing recall-side mutation.

## Proposed Architecture

## 1. Memory classes

Add explicit retrieval classes in frontmatter:

```yaml
kind: memory | session | rule | article
priority: high | normal | low
inject: auto | manual | never
```

Interpretation:

- `rule`: durable procedural memory, compact, high priority for auto injection
- `memory`: stable semantic user/project fact
- `session`: episodic checkpoint, searchable but rarely auto-injected
- `article`: external reference

## 2. Memory size policy

Add config and enforcement:

- `memory_soft_max_chars`
- `memory_hard_max_chars`
- `memory_auto_chunk_threshold`
- `memory_auto_inject_token_budget`

Defaults:

- `memory_soft_max_chars = 1200`
- `memory_hard_max_chars = 4096`
- `memory_auto_chunk_threshold = 1200`
- `memory_auto_inject_token_budget = 300`

## 3. Retrieval pipeline

Recommended retrieval order:

1. normalize query
2. lexical retrieval via FTS5
3. optional hybrid/rerank
4. section/chunk selection
5. per-class rerank
6. bounded context assembly

For fallback mode:

1. `rg --files` path scope
2. `rg -n -i` lexical match
3. load matched files only
4. construct snippets from matched headings/paragraphs

## 4. Recall output contract

Change memory recall return shape to support distinct consumers:

- `results`: structured result list
- `display`: user-facing formatted text
- `injection`: compact machine-facing context payload

`inject_opening_context()` should consume `injection`, not `display`.

## 5. Read-path purity

Preferred design:

- recall does not mutate files
- any dedup, touch, or consolidation happens on write or in maintenance jobs

If gravity is retained, store it outside the markdown body:

- index-side access stats table
- or separate per-project metadata file

That keeps markdown content stable while preserving usage signals.

## Recommended Implementation Order

## Phase 1: Correctness and budgeting

1. Stop using `display` as the prompt injection payload.
2. Add `injection` budget caps to `recall_memory()`.
3. Remove touch-on-read or reindex touched memories immediately.
4. Reindex the surviving memory after dedup-on-read, or remove dedup-on-read entirely.

This phase fixes correctness and prompt bloat without changing storage layout.

## Phase 2: Memory unit quality

1. Add file-size limits.
2. Add `kind` differentiation for `session` and `rule`.
3. Lower injection priority for `session`.
4. Add query normalization before recall.

This phase improves retrieval precision and prevents markdown sprawl.

## Phase 3: Chunked memory retrieval

1. Add chunk indexing for large memory files.
2. Reuse chunk-level reranking for memory.
3. Assemble injected context from matched sections, not whole files.

This phase brings memory recall up to the level already used for articles.

## Non-Goals

This proposal does not recommend:

- replacing markdown with a database as the source of truth
- removing FTS5 in favor of vector-only retrieval
- making semantic retrieval mandatory for all installs
- auto-injecting more memory by default

The right direction is still markdown-first, lexical-first, and budgeted.

## Final Recommendation

The repository should keep its current markdown-plus-derived-index architecture. That foundation is good.

The main change is not "replace file-based memory." It is "make file-based memory behave like a modern retrieval system":

- smaller memory units
- bounded injection
- chunked recall for larger memory files
- true lexical fallback
- read-only recall semantics

If only one thing is done next, it should be this:

`inject_opening_context()` should stop injecting `recall_memory()["display"]` and instead inject a compact, token-budgeted retrieval payload built from top snippets.
