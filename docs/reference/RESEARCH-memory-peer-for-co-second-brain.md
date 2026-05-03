# RESEARCH: Hermes-Agent, OpenClaw, and ReMe for Co's Second-Brain Mission

Scan date: 2026-04-23; co source updated: 2026-04-30

## 0. Why This Document Exists

This pass compares three memory peers against `co`'s core design goal:

- a personalized AI assistant
- general purpose, not task-siloed
- a second brain / second memory
- a local knowledgebase the user can inspect and own

This is not a generic memory survey. The question here is narrower:

> Which parts of `hermes-agent`, `openclaw`, and `ReMe` are actually good reference designs for `co`, given `co` already separates raw episodic memory from distilled reusable knowledge?

## 1. Source Update Status

Peer repos were refreshed before the scan where possible.

| Repo | Pull status | Revision used in this scan |
| --- | --- | --- |
| `openclaw` | `git pull` succeeded; fast-forwarded to latest upstream | `b4d19923388cd78082ccb568487aa04335b5e60d` |
| `ReMe` | `git pull` reported already up to date | `625d184ca12cb4bc2be69618b37b817b50febd07` |
| `hermes-agent` | fetch succeeded, but merge was blocked by local change in `uv.lock`; scanned current checkout and noted upstream drift | local `6ea7386a6f010320c8744cee6a1ac7835bc37ffc`, upstream `origin/main` at `ce089169d578b96c82641f17186ba63c288b22d8` |

Implication:

- `openclaw` and `ReMe` are scanned at current upstream state.
- `hermes-agent` is scanned from the current local checkout, with awareness that upstream is newer.

## 2. Co Baseline: What Problem Co Is Actually Solving

`co` has a three-channel recall model over two storage layers:

- **Session channel**: append-only `sessions/*.jsonl` transcripts indexed in `co-cli-search.db` (`source='session'`) for episodic recall
- **Knowledge channel**: reusable `knowledge/*.md` markdown artifacts indexed in the same `co-cli-search.db` (`source='knowledge'`) for artifact recall
- **Canon channel**: read-only `souls/{role}/memories/*.md` scenes scanned in-process with token-overlap scoring
- **Dream cycle**: optional session-end mining, merge, and decay that distill durable knowledge from raw history

Static personality content (soul seed, mindsets, rules) is injected once at agent construction — it is not a recall channel and does not depend on artifact search.

Key evidence:

- `docs/specs/memory.md` §1 defines the three-channel recall model and confirms static personality is not a channel.
- `co_cli/tools/memory/recall.py:194` implements `memory_search()` dispatching all three channels in parallel.
- `co_cli/tools/memory/read.py:69-73` implements the `memory_list` artifact inventory tool.
- `co_cli/memory/dream.py:379-453` has merge (`_identify_mergeable_clusters`, `_merge_similar_artifacts`) and decay (`_decay_sweep`) phases for reusable knowledge.

**Architecture note (April 28, v0.8.52):** The `co_cli/knowledge/` module was collapsed into `co_cli/memory/`. All artifact, dream, decay, and archive logic now lives under `co_cli/memory/`; the tool surface under `co_cli/tools/memory/` was unchanged. The two storage layers remain separate on disk; only the Python module boundary changed.

**Recall is pull-based, not push-based.** Knowledge artifacts are not injected into the context per-turn. The agent calls `memory_search()` when it suspects relevance — nothing is pre-loaded into the prompt from the knowledge corpus during normal turns.

So the design target is not "add memory to a stateless assistant." It is:

1. keep raw chronology (sessions)
2. distill durable knowledge (artifacts via dream cycle)
3. recall the right knowledge on demand (unified `memory_search`)
4. keep the local corpus inspectable and healthy over time (dream merge/decay)

That framing matters because some peers are strong on personalization but weak on local knowledge management, while others are strong on consolidation but weak on assistant identity.

## 3. Evaluation Criteria

Peers are scored here on relevance to `co`'s mission, not on total sophistication.

The comparison axes are:

1. personalization model
2. local knowledge ownership and inspectability
3. general-purpose applicability
4. self-learning and consolidation
5. retrieval layering and prompt-shaping
6. alignment to `co`'s existing two-layer architecture

## 4. Peer Scan

### 4.1 Hermes-Agent

#### What it is

`hermes-agent` has a dual memory model:

- built-in local files: `MEMORY.md` and `USER.md`
- one optional external memory provider, active alongside the built-in store

Key source files scanned:

- `tools/memory_tool.py`
- `agent/memory_provider.py`
- `agent/memory_manager.py`

#### What it gets right for `co`

The best thing in `hermes-agent` is its **personalization split**.

Its built-in store is explicitly divided into:

- `MEMORY.md`: assistant/world/project notes
- `USER.md`: what the agent knows about the user

That is a clean reference design for personalized assistant memory.

Evidence:

- `tools/memory_tool.py:5-14`
- `tools/memory_tool.py:49-56`
- `tools/memory_tool.py:124-140`

It also has a strong runtime seam around memory providers:

- static prompt block from the provider
- pre-turn prefetch
- post-turn sync
- end-of-session extraction
- delegation hook

Evidence:

- `agent/memory_provider.py:16-31`
- `agent/memory_provider.py:83-119`
- `agent/memory_provider.py:153-186`
- `agent/memory_manager.py:18-27`
- `agent/memory_manager.py:157-215`

This is good reference material for:

- separating user identity memory from general assistant memory
- layering static hot memory and dynamic recall
- defining lifecycle hooks around memory without coupling them to the whole agent core

#### Where it is misaligned with `co`

Its built-in model is **bounded curated memory**, not a second-brain knowledge system.

The built-in store is:

- small
- char-limited
- frozen into the system prompt at session start
- updated on disk mid-session but not reflected in the live prompt until the next session

Evidence:

- `tools/memory_tool.py:11-23`
- `tools/memory_tool.py:116-123`
- `tools/memory_tool.py:222-259`

That is excellent for stable personal profile memory, but weaker as a full local knowledgebase. It is closer to "persistent assistant profile" than "second brain with evolving local corpus."

#### Bottom line

`hermes-agent` is the best reference in this set for **personalization architecture**, but not the best overall reference for `co`'s full second-brain mission.

### 4.2 OpenClaw

#### What it is

`openclaw` is the strongest memory infrastructure system in the set. Its memory core includes:

- dream phases
- qmd/sqlite-backed local indexing
- hybrid retrieval
- temporal decay
- ranking infrastructure

Key source files scanned:

- `extensions/memory-core/src/dreaming-phases.ts`
- `extensions/memory-core/src/memory/hybrid.ts`
- `extensions/memory-core/src/memory/qmd-manager.ts`
- `extensions/memory-core/src/memory/manager-db.ts`

#### What it gets right for `co`

`openclaw` is the best reference here for **self-learning and corpus hygiene**.

It has a real dreaming subsystem, not just a compactor:

- managed light/REM phase infrastructure
- daily/session ingestion state
- promotion and narrative machinery
- memory-specific storage for dream artifacts

Evidence:

- `dreaming-phases.ts:47-91`
- `dreaming-phases.ts:59-77`
- `dreaming-phases.ts:93-117`

Its retrieval stack is also the strongest:

- vector + keyword merge
- temporal decay
- optional MMR re-ranking

Evidence:

- `memory/hybrid.ts:57-155`

Its local knowledge/indexing machinery is serious and operationally mature:

- sqlite-backed memory DB
- qmd-backed file and collection management
- file watching and sync logic

Evidence:

- `memory/manager-db.ts:1-14`
- `memory/qmd-manager.ts:60-103`
- `memory/qmd-manager.ts:252-260`

This is good reference material for:

- dream-loop design
- retrieval ranking
- decay and recency policy
- local index management over a knowledge corpus

#### Where it is misaligned with `co`

It is not primarily a personalization-first assistant memory model.

Its center of gravity is:

- search/index infrastructure
- retrieval quality
- dream/consolidation pipeline

not:

- user identity modeling
- personal profile memory
- simple inspectable user-facing mental model

It is a great subsystem reference, but a weaker product-shape reference for "my second brain assistant."

#### Bottom line

`openclaw` is the best reference in this set for **dreaming, retrieval, and hygiene**, but not the best primary blueprint for `co`'s personalized second-brain UX.

### 4.3 ReMe

#### What it is

`ReMe` is the most balanced "local memory as operational system" in this set.

It ships both:

- a file-based memory system (`ReMeLight`)
- a vector-based memory system (`ReMe`)

It also explicitly models different memory classes:

- personal
- procedural
- tool

Key source files scanned:

- `reme/reme_light.py`
- `reme/reme.py`
- `reme/memory/file_based/components/summarizer.py`
- `reme/memory/file_based/tools/memory_search.py`

#### What it gets right for `co`

`ReMe` is the strongest reference here for **second brain + local knowledgebase** as one coherent system.

Why:

1. It treats local memory as a real working set, not just a tiny personal profile.
2. It has pre-reasoning context management rather than naive always-inject behavior.
3. It includes async background summarization and compaction.
4. It supports personal memory and learned procedural memory in the same model.

Evidence for file-based local knowledge management:

- `reme_light.py:42-65`
- `reme_light.py:126-143`
- `reme_light.py:186-205`

Evidence for self-learning / summarization lifecycle:

- `reme_light.py:436-507`
- `reme_light.py:509-552`
- `reme_light.py:563-670`
- `summarizer.py:49-97`

Evidence for local search and recall:

- `memory_search.py:15-94`
- `reme_light.py:730-804`

Evidence for broader learned memory classes:

- `reme.py:96-127`
- `reme.py:147-165`
- `reme.py:189-240`

This is especially relevant to `co` because the mission is not just "remember the user." It is also:

- remember durable rules
- remember successful patterns
- remember tool usage knowledge
- maintain those locally

That is much closer to a second brain than `hermes-agent`'s bounded built-in store.

#### Where it is misaligned with `co`

`ReMe` is weaker than `hermes-agent` on the **clarity of user-vs-assistant identity split**.

It clearly supports personal memory, but its design emphasis is broader memory operations, retrieval, and summarization, not the very clean "USER.md vs MEMORY.md" conceptual separation.

It is also more memory-framework-like than `co` currently is.

#### Bottom line

`ReMe` is the best reference in this set for **local second-brain mechanics**: learning, compaction, retrieval, and durable local knowledge organization.

## 5. Comparison Against Co's Mission

### 5.1 Personalized AI Assistant

Winner: `hermes-agent`

Why:

- explicit `USER.md` vs `MEMORY.md`
- profile-scoped local storage
- strong hooks for memory lifecycle

`ReMe` supports personalization, but less cleanly at the product-model level.
`openclaw` is mostly not optimized around this problem.

### 5.2 General-Purpose Assistant

Winner: `ReMe`

Why:

- personal + procedural + tool memory types
- pre-reasoning memory management
- supports both local files and richer retrieval

`hermes-agent` is strong but more profile-centric.
`openclaw` is strong technically but feels more like a memory/search platform than a general assistant memory design.

### 5.3 Second Brain / Second Memory

Winner: `ReMe`

Why:

- durable local memory corpus
- compaction + summarization
- personal and learned task memory coexist
- memory is meant to survive and evolve, not just stay inside a bounded prompt cache

`hermes-agent` is a better profile memory model.
`ReMe` is a better second-brain model.

### 5.4 Local Knowledgebase

Winner: `ReMe`, with `openclaw` as the stronger subsystem reference

Why:

- `ReMe` has the more directly adoptable local knowledge-management shape for `co`
- `openclaw` has the stronger index/ranking/dream infrastructure if `co` needs deeper maintenance machinery later

### 5.5 Self-Learning and Hygiene

Winner: `openclaw`

Why:

- dreaming phases
- ranking and decay
- mature local index pipeline

`ReMe` is also strong here, but its compaction/summarization is more context-management-oriented. `openclaw` is the more complete post-write maintenance model.

## 6. Best Reference by Design Concern

| Design concern | Best peer reference | Why |
| --- | --- | --- |
| Personalization | `hermes-agent` | clean user-vs-assistant memory split |
| General-purpose assistant memory | `ReMe` | personal + procedural + tool memory in one local system |
| Second brain / second memory | `ReMe` | strongest balance of local corpus + learning + recall |
| Local knowledgebase | `ReMe` | most adoptable local knowledge shape for `co` |
| Dreaming / consolidation / decay | `openclaw` | strongest maintenance and retrieval infrastructure |
| Retrieval scoring and corpus hygiene | `openclaw` | hybrid ranking, temporal decay, qmd/sqlite runtime |

## 7. Recommendation for Co

There is no single best peer to copy wholesale.

The best reference stack for `co` is:

1. **Primary product reference: `ReMe`**
   Use it as the main reference for how a local second-brain assistant should manage durable memory, recall, compaction, and learned knowledge.

2. **Personalization overlay: `hermes-agent`**
   Borrow its clean split between "what the assistant knows about the user" and "what the assistant knows more generally."

3. **Dreaming/hygiene subsystem reference: `openclaw`**
   Borrow selective ideas for merge, decay, temporal scoring, and retrieval quality. Do not copy its full platform shape into `co`.

## 8. Practical Implications for Co

If `co` is optimizing for "my second-brand + second-memory + all my knowledge," then the most promising near-term moves are:

1. Keep `co`'s current two-layer architecture.
   It is already correct: raw episodic memory plus distilled reusable knowledge.

2. Add a clearer personalization split inside the knowledge layer.
   `hermes-agent` shows the value of explicitly separating user-profile memory from general/project/world memory.

3. Treat reusable knowledge as broader than user preferences.
   `ReMe` is the strongest reminder that procedural memory and tool memory matter for a second-brain assistant.

4. Strengthen dream/hygiene without turning `co` into a memory platform.
   `openclaw` is the right place to borrow merge/decay/ranking ideas, but not the right whole-product template.

## 9. Adoption Constraints from Co's Current Harness

The active Hermes adoption plan adds an important constraint layer to this research: some ideas are good in principle but do not fit `co`'s current runtime without redesign.

### 9.1 What the current harness allows

`co` already has a strong substrate for self-learning:

- reusable artifacts under `knowledge_dir/*.md`
- ranked retrieval through the derived search DB
- per-turn extraction
- retrospective merge/decay dreaming
- article persistence through the existing knowledge write path

That means the main gaps are not basic storage or retrieval. The remaining gaps are higher-level:

- corpus governance
- first-class research ingestion
- evidence discipline
- selective freshness workflows

### 9.2 Hard constraints that shape adoption

The Hermes adoption plan surfaced several current-runtime facts that matter:

1. Skills are slash-command overlays, not a mandatory model-visible runtime index.
   New self-learning capabilities should land as native tools, commands, and knowledge helpers, not as skill-only behavior.

2. Generic file tools are workspace-bound.
   `co` should not copy a literal Hermes-style external wiki path model. Knowledge operations should continue to target `knowledge_dir` directly.

3. The existing knowledge corpus is user-global and top-level only.
   Additive governance files belong under `knowledge_dir/_meta/`, not as first-class knowledge artifacts.

4. Obsidian is read-only today.
   `co` should not assume "assistant writes directly into an external vault" as the default knowledge-management path.

5. Web ingestion is text-first.
   Abstract-and-metadata-first research ingestion fits the current harness better than immediate PDF/full-document ingestion.

6. Background tasks are not a durable recurring scheduler.
   Continuous feed-watching should stay deferred until daemon/headless execution is explicit product scope.

These constraints reinforce the main conclusion of this document: `co` should borrow product ideas and subsystem patterns from peers, but not copy their runtime assumptions literally.

## 10. Cross-Reviewed Adoption Opportunities

The active Hermes research plan adds one more useful synthesis: the best next steps for `co` are not "more memory layers." They are operating-model improvements on top of the current knowledge layer.

### 10.1 Add first-class research ingestion

The Hermes-derived plan is also right that `co` needs a more native research ingestion path if it is going to function as a serious local knowledge assistant.

Most useful near-term addition:

- first-class arXiv discovery and import

Why this fits:

- `co` already has `article` and `reference` artifact kinds
- the current harness is well suited to metadata-first and abstract-first ingestion
- this reinforces the local knowledgebase mission directly

### 10.2 Add evidence discipline helpers

The best extract from Hermes's research workflows is not paper-writing scaffolding. It is:

- citation verification
- structured research journals

That matters because a second brain should preserve:

- what was learned
- why it should be trusted
- what remains unresolved

This is a higher-value addition to `co` than copying venue-specific research-writing flows.

### 10.3 Defer continuous monitoring

The plan is also correct to defer feed-watching and recurring monitoring.

`co` does not yet have a durable recurring scheduler, so source monitoring should remain out of scope until daemon/headless execution is a real product concern.

## 11. Recommended Execution Order

Cross-reviewing the plan against this research changes the recommendation from "which peer to copy" into "which layers to adopt first."

Recommended order:

1. Keep `co`'s current two-layer memory/knowledge architecture as the foundation.
2. Add a clearer personalization split in the knowledge layer, borrowing from `hermes-agent`.
3. Add first-class research ingestion, especially arXiv metadata/abstract import.
4. Add evidence-discipline helpers: citation verification and structured research journals.
5. Improve dreaming, ranking, and decay by selectively borrowing from `openclaw`.
6. Defer continuous monitoring and any daemon-like freshness loops.

This sequencing is consistent with the peer comparison:

- `hermes-agent` improves personalization
- `ReMe` improves the second-brain operating model
- `openclaw` improves consolidation and retrieval quality

## 12. Copy / Reject / Defer Matrix

This section answers the missing practical question directly: what should `co` copy from each peer, what should it reject, and what should remain deferred.

### 12.1 Hermes-Agent

**Copy**

- the explicit separation between user-profile memory and general assistant/world memory
- the runtime distinction between static hot memory and dynamic recall
- provider lifecycle seams: prefetch, post-turn sync, end-of-session extraction

**Reject**

- using a small bounded curated prompt store as the main long-term knowledge system
- freezing the primary knowledge state into a session-start snapshot as the dominant memory model for `co`

**Defer**

- external memory providers as a major extension surface until `co` has stronger first-party knowledge workflows

### 12.2 ReMe

**Copy**

- treating local memory as a working local corpus rather than just a profile cache
- explicit personal, procedural, and tool-memory concepts
- pre-reasoning memory management
- async summarization and compaction
- local hybrid search as part of normal assistant operation

**Reject**

- importing a memory-framework-style surface area wholesale
- over-abstracting `co` into a generic memory platform before the assistant-facing workflows are nailed down

**Defer**

- the full vector-heavy stack as a default requirement for all local knowledge operations

### 12.3 OpenClaw

**Copy**

- dreaming phases as a maintenance model
- retrieval ranking, temporal decay, and diversity-aware reranking ideas
- corpus hygiene patterns: merge, decay, recency weighting, and maintenance state

**Reject**

- adopting the full qmd/sqlite/platform shape as `co`'s primary product model
- centering the user experience around indexing/runtime infrastructure instead of assistant memory UX

**Defer**

- deeper daemonized maintenance loops until `co` has an approved headless runtime story

## 13. Co-Native Stack Target

This is the concrete target stack implied by the research.

### 13.1 Tools

The primary runtime surface should be native tools and commands, not skill-only behavior.

**Shipped tool surface** (as of April 30, `co_cli/tools/memory/`):

- `memory_search` (`recall.py:194`) — unified recall across knowledge artifacts, session transcripts, and canon in one call
- `memory_list` (`read.py`) — paginated artifact inventory
- `memory_create` (`write.py`) — save a new artifact (kinds: preference, feedback, rule, article, reference, note, decision); includes Jaccard dedup when `consolidation_enabled`
- `memory_modify` (`write.py`) — append or surgically replace a passage in an existing artifact

**Still aspirational** (not yet shipped — from original April 23 target list):

- `citation_verify` — evidence-discipline helper for references
- `research_journal_save` — structured note capture for what was learned, why it is trusted, and what remains open

**In-flight** (plan approved, pending implementation):

- `arxiv_search` + `research-import` skill — see `docs/exec-plans/active/2026-05-03-113954-arxiv-research-ingestion.md`

### 13.2 Skills

Skills should remain optional workflow overlays and documentation aids, not the primary self-learning runtime.

Recommended role of skills:

- document research workflows
- guide users through optional multi-step patterns
- provide slash-command ergonomics for research-heavy tasks

Rejected role for skills:

- acting as the main memory runtime
- being the only place where self-learning or knowledge-management behavior exists

### 13.3 Memory

`co`'s session layer stays raw and chronological.

Current state (`co_cli/memory/`):

- append-only `sessions/*.jsonl` as source of truth
- chunked (token-based, 400-token window, 80-token overlap) session indexing into `co-cli-search.db` (`source='session'`)
- recall via `memory_search` → BM25 chunk search → best chunk per unique session (no LLM)
- sessions and knowledge artifacts are co-equal kinds under the unified `co_cli/memory/` module (April 28 collapse)

Borrowed ideas:

- from `hermes-agent`: explicit user-profile vs general memory distinction, now shipped via `artifact_kind: user`
- from `ReMe`: memory classes beyond pure profile facts

### 13.4 Knowledge

The knowledge layer is the reusable second-brain layer.

Current state (`co_cli/memory/` + `knowledge/*.md`):

- flat `knowledge/*.md` markdown artifacts with YAML frontmatter as source of truth
- `KnowledgeStore` FTS5 BM25 ± RRF vector merge backend in `co-cli-search.db` (`source='knowledge'`)
- dream merge (`_identify_mergeable_clusters`, `_merge_similar_artifacts`) and decay (`_decay_sweep`) in `co_cli/memory/dream.py`
- artifact kinds shipped: preference, decision, rule, feedback, article, reference, note

Still aspirational:

- first-class research ingestion (arXiv metadata/abstract import)
- evidence-discipline helpers (citation verification, structured research journals)

Borrowed ideas:

- from `ReMe`: local corpus mentality and procedural/tool memory concepts
- from `openclaw`: merge/decay/ranking/hygiene logic
- from Hermes research adoption work: ingestion and evidence discipline

## 14. Final Verdict

If the question is:

- **"Which peer has the best personalization reference design?"**
  `hermes-agent`

- **"Which peer has the best self-learning + local knowledge management reference design?"**
  `ReMe`, with `openclaw` as the stronger dreaming/hygiene subsystem reference

- **"Which peer is most aligned to co's mission as a personalized second brain with a local knowledgebase?"**
  `ReMe` is the best primary reference, `hermes-agent` is the best personalization overlay, and `openclaw` is the best maintenance/retrieval subsystem reference.

## 15. Remaining Gaps Against Converged Best Practice

Updated: 2026-05-01. Reflects current source state after the April 28 module collapse.

### What is shipped

| Capability | Source | Peer origin |
| --- | --- | --- |
| Three-channel recall (sessions, knowledge, canon) | `co_cli/tools/memory/recall.py` | `ReMe` |
| Four unified `memory_*` tools | `co_cli/tools/memory/` | `ReMe` |
| Hybrid FTS5 + vector retrieval (BM25 ± RRF) | `co_cli/memory/knowledge_store.py` | `openclaw` |
| Dream cycle: mine, merge, decay | `co_cli/memory/dream.py` | `openclaw` + `ReMe` |
| Pull-based recall (no per-turn injection) | architecture | `ReMe` |
| Static personality injected once at construction | `co_cli/context/assembly.py` | `hermes-agent` |
| Age + access-based artifact decay | `co_cli/memory/dream.py:441` | `openclaw` |
| Artifact dedup (Jaccard on write) | `co_cli/memory/service.py` | `openclaw` |
| Personalization split (`artifact_kind: user`) | `co_cli/memory/artifact.py` + `co_cli/tools/memory/recall.py` | `hermes-agent` |

### Gap 2 — Corpus governance (high ROI, fits current harness)

**From:** Hermes adoption plan + `openclaw` index hygiene

**What's missing:** The `knowledge/` corpus has no schema, no machine-maintained catalog, and no mutation log. There is no way to audit what the corpus contains, enforce artifact conventions, or trace when an artifact was created or merged. The `_meta/` overlay proposed in Section 10.1 was never shipped.

**Minimum viable adoption:** Three files under `knowledge/_meta/`: `SCHEMA.md` (corpus conventions and kind taxonomy), `index.md` (auto-maintained catalog), `log.md` (append-only artifact mutation log written by `memory_create` and `memory_modify`).

### Gap 3 — Pre-reasoning memory selection (medium ROI, requires agent loop change)

**From:** `ReMe` pre-reasoning context management

**What's missing:** `memory_search` is available but nothing governs when the agent should call it before reasoning. Recall is entirely ad-hoc — the agent decides per-turn with no structural prompt or policy nudge to check memory before answering. `ReMe` makes pre-reasoning memory lookup a mandatory step, not an optional tool call.

**Minimum viable adoption:** A system-prompt instruction that explicitly primes the agent to call `memory_search` before answering questions that reference past sessions, user preferences, or established decisions. Requires no code change, only guidance policy.

### Gap 5 — Evidence discipline (medium ROI, new tooling required)

**From:** Hermes adoption plan

**What's missing:** The corpus can hold references but cannot flag unverified ones or track what remains open. There is no `citation_verify` tool and no structured research journal artifact. The second-brain goal of preserving *what was learned*, *why it should be trusted*, and *what remains unresolved* is unsupported.

**Minimum viable adoption:** A `research_journal` artifact kind with a schema that requires `source`, `confidence`, and `open_questions` fields. A `citation_verify` tool can come later.

### Gap 6 — Retrieval diversity reranking (low ROI, retrieval quality improvement)

**From:** `openclaw` MMR reranking

**What's missing:** The hybrid BM25 + vector retrieval stack is shipped, but diversity-aware reranking (Maximal Marginal Relevance or equivalent) is not. Result sets can cluster around semantically similar chunks, especially in artifact-dense corpora.

**Minimum viable adoption:** MMR pass after RRF merge in `knowledge_store.py`, gated by a config flag (`knowledge.rerank_diversity`). Low urgency until the corpus is large enough for clustering to be a visible problem.

### Gap 7 — Retrieval-aware decay (low ROI, hygiene improvement)

**From:** `openclaw` temporal scoring

**What's missing:** Decay today is age + explicit-access based (`decay_after_days`, `decay_protected`). `openclaw`'s recency-weighted retrieval scoring — penalizing artifacts that haven't appeared in recent search results, not just ones that haven't been explicitly read — is not adopted. This matters for large corpora where stale artifacts can still match queries without ever being acted on.

**Minimum viable adoption:** Track `last_recalled_at` in artifact frontmatter (written by `memory_search` on hit) and include it as a decay signal alongside `last_modified_at`.

### Priority order

| Priority | Gap | Effort | Peer source |
| --- | --- | --- | --- |
| 1 | Pre-reasoning memory selection | Low — guidance policy only | `ReMe` |
| 2 | Evidence discipline | Medium — new artifact kind + optional tool | Hermes plan |
| 3 | Retrieval diversity reranking | Medium — MMR pass in knowledge_store | `openclaw` |
| 4 | Retrieval-aware decay | Low — `last_recalled_at` tracking | `openclaw` |
