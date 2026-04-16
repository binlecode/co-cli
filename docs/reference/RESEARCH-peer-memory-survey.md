# RESEARCH: Peer Memory Survey vs co-cli

Scan date: 2026-04-15

## 1. Scope

Peer systems covered in this survey, ordered by **relevance** to `co-cli`'s CLI/file-based architecture:

1. `fork-cc` (highly relevant: CLI, Markdown-first, background hygiene)
2. `goose` (highly relevant: CLI, local/global text files, explicit tools)
3. `hermes-agent` (highly relevant: CLI, Markdown-first, static hot + dynamic cold)
4. `openclaw` (medium: SQLite but strong background consolidation patterns)
5. `letta` (medium: block-structured/DB, but excellent explicit edit model)
6. `elizaos` (low: heavy DB, multi-agent enterprise scale)

Peer systems ordered by **maturity** (robustness, automated hygiene, retrieval sophistication):

1. `elizaos` (enterprise-grade, memory graphs, token-aware state, time-decay pruning)
2. `letta` (strong mutation history, archival DB/vector search, multi-agent sharing)
3. `openclaw` (three-phase dreaming, vector/FTS search, SQLite chunk stores)
4. `fork-cc` (autoDream background consolidation, layered scoping)
5. `hermes-agent` (pluggable providers, write-path safety scanning, static core)
6. `goose` (functional MCP CRUD tools, but fully manual without automated hygiene)

Current `co-cli` implementation checked directly:

- [docs/specs/memory.md](/Users/binle/workspace_genai/co-cli/docs/specs/memory.md)
- [co_cli/memory/recall.py](/Users/binle/workspace_genai/co-cli/co_cli/memory/recall.py)
- [co_cli/memory/_extractor.py](/Users/binle/workspace_genai/co-cli/co_cli/memory/_extractor.py)
- [co_cli/tools/memory.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py)
- [co_cli/context/_history.py](/Users/binle/workspace_genai/co-cli/co_cli/context/_history.py)
- [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py)
- [co_cli/agent/_instructions.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_instructions.py)
- [co_cli/commands/_commands.py](/Users/binle/workspace_genai/co-cli/co_cli/commands/_commands.py)
- [co_cli/knowledge/_frontmatter.py](/Users/binle/workspace_genai/co-cli/co_cli/knowledge/_frontmatter.py)

This pass intentionally focuses on persistent memory behavior. It excludes general tool catalogs, articles/library indexing except where the shared file model matters, and broader multi-agent orchestration except where it directly shapes memory.

## 2. co-cli Latest Baseline

The current `co-cli` memory system is a flat-file store with YAML frontmatter, a small read-first model-visible surface, and a background extractor that owns new memory creation.

### 2.1 Storage and schema actually present in code

`co-cli` stores one Markdown file per item in the configured memory directory. The same frontmatter parser and validator support both `kind: memory` and `kind: article`, but the memory-specific paths only query `kind="memory"`.

Current high-signal fields exposed on `MemoryEntry` or enforced by frontmatter validation:

| Axis | Current `co-cli` shape |
|------|-------------------------|
| Storage unit | one `.md` file per memory |
| Core identity | `id`, `created`, `path`, `content` |
| Classification | `kind`, `type`, `artifact_type` |
| Recall hints | `tags`, `related`, `always_on`, `description` |
| Lifecycle | optional `updated` |
| Validation model | frontmatter is validated on load; malformed files warn and are skipped |

### 2.2 Read and injection paths actually present in code

`co-cli` has three distinct read paths:

- standing context via `load_always_on_memories()` with a hard cap of 5 entries
- per-turn dynamic recall via `_recall_for_context()` on each new user turn
- explicit inventory/search via `list_memories()` and `search_memories()`

Current behavior:

| Axis | Current `co-cli` shape |
|------|-------------------------|
| Standing injection | explicit `always_on=True` entries, capped at 5 |
| Turn-time recall | runs on every new user turn, max 3 results injected |
| Injection site | trailing `SystemPromptPart` appended to the message list |
| Search backend | `knowledge_store.search(source="memory", kind="memory")` when available |
| Local grep helper | `grep_recall()` is used by REPL-style memory management, not by model-visible recall |

### 2.3 Write and edit paths actually present in code

New memory creation is owned by the background extractor agent. The extractor builds a compact text window from recent turns, then calls `save_memory()` zero or more times. `save_memory()` always creates a new file and immediately re-indexes it when the knowledge store is available.

Edits are more nuanced than the model-visible tool surface suggests:

| Axis | Current `co-cli` shape |
|------|-------------------------|
| New memory writes | `save_memory()` |
| Write trigger | cadence-gated post-turn background extraction on clean turns |
| Write semantics | always create new file; no dedup in `save_memory()` |
| Explicit edits | `update_memory()` and `append_memory()` exist |
| Edit exposure | edit helpers are defined, but not registered on the main native toolset |
| Edit safety | resource lock, exact-match replacement guard, atomic temp-file rewrite |

### 2.4 Structural profile

The current `co-cli` memory system is best described as:

- flat-file first
- read-first in the main agent surface
- extraction-owned for new writes
- dynamic on every user turn rather than session-frozen
- intentionally simple on retrieval compared with peers that add provider plugins, vector stores, or consolidation loops

## 3. Comparison Axes

The peer notes converge on six high-signal axes:

1. storage model and schema
2. context delivery and retrieval
3. write triggers and edit model
4. consolidation, drift control, and retention
5. scope, sharing, and isolation
6. extensibility and safety

## 4. Survey by Axis

### 4.1 Storage model and schema

| System | Storage shape | Comparison to current `co-cli` |
|--------|---------------|--------------------------------|
| `elizaos` | DB-backed memory schema (`id`, `entityId`, `roomId`, `content`, `embedding`) split by type (Working, Long-term, Knowledge) | heavier architecture than `co-cli`; explicit vector/DB focus |
| `fork-cc` | topic files plus a `MEMORY.md` entrypoint index; frontmatter is minimal (`name`, `description`, `type`) | same Markdown-first instinct as `co-cli`, but with a stronger directory-level index and a thinner per-file schema |
| `goose` | Flat `.txt` files by category in local (`.goose/memory`) or global (`~/.config/goose/memory`), with tags prefixed by `# ` | simpler and more explicit than `co-cli`, directly exposed as an MCP server |
| `hermes-agent` | two built-in Markdown files (`MEMORY.md`, `USER.md`) plus an optional external provider tier | much more tiered than `co-cli`; built-in tier is simpler, external tier is much richer |
| `letta` | labeled in-context memory blocks plus archival passages in SQL/vector storage | materially different model; memory is block-structured rather than file-per-fact |
| `openclaw` | per-agent SQLite chunk store with FTS/vector search and optional session-memory files | more index-centric than `co-cli`; files are input material, not the primary memory abstraction |
| `co-cli` | one Markdown file per memory with validated frontmatter fields for classification and recall hints | simpler than `letta` and `openclaw`, richer per-file metadata than `fork-cc`, less tiered than `hermes-agent` |

High-signal read: peers split into two camps. `fork-cc`, `goose`, `hermes`, and `co-cli` keep Markdown/text as the canonical memory format; `elizaos`, `letta`, and `openclaw` move the center of gravity into structured stores. `co-cli` is currently the simplest of the Markdown-first systems in storage topology while retaining rich metadata.

### 4.2 Context delivery and retrieval

| System | Delivery / retrieval model | Comparison to current `co-cli` |
|--------|-----------------------------|--------------------------------|
| `elizaos` | Dynamic token-aware state composition (`composeState`) and hybrid retrieval (recency + semantic/vector + keyword) | richer retrieval stack than `co-cli` and token-aware context windowing |
| `fork-cc` | session prompt carries the memory index; relevant memory files are selected per turn by a separate relevance pass | similar goal, but `fork-cc` spends more machinery on semantic file selection and freshness guidance |
| `goose` | MCP router injects all categorized memories as standing system instructions; supports targeted tool retrieval | simpler than `co-cli`, uses raw text injection with explicit tools over background recall |
| `hermes-agent` | built-in memory is frozen at session start; external providers prefetch per turn and inject fenced memory context into the user message | strongest contrast to `co-cli`: static hot memory plus dynamic cold retrieval, instead of one fully dynamic path |
| `letta` | core memory blocks are always in prompt; archival memory is searched separately | much hotter default memory than `co-cli`; less dependence on per-turn recall |
| `openclaw` | retrieval is query-driven hybrid search over chunks with configurable vector/text weighting | richer retrieval stack than `co-cli`, but less explicit standing-context support |
| `co-cli` | explicit always-on entries plus per-turn recall injected as trailing system context | narrower retrieval model than `hermes`, `elizaos` and `openclaw`, but more dynamic than `hermes` built-in memory and lighter than `fork-cc`'s extra relevance pass |

High-signal read: the main design split is not "files vs database"; it is "session-frozen hot memory" vs "fresh per-turn recall". `co-cli` sits on the fresh-recall side and keeps standing context explicit through `always_on`. `elizaos` highlights the value of token-aware context windowing and state composition.

### 4.3 Write triggers and edit model

| System | Write and edit shape | Comparison to current `co-cli` |
|--------|----------------------|--------------------------------|
| `elizaos` | Strong lifecycle focus with explicit metadata tracing (`importance`, `source`); agent actions and triggers write automatically | automated background recording vs `co-cli`'s summarized extractor |
| `fork-cc` | direct file edits by the main agent plus background extraction and autoDream consolidation | broader than `co-cli`; the main agent can write memory directly, while `co-cli` routes new writes through the extractor |
| `goose` | Explicit model-invoked tools (`remember_memory`, `remove_memory_category`) prompted by user trigger words ("remember") | model-driven CRUD vs `co-cli`'s background extraction |
| `hermes-agent` | model uses a built-in `memory` tool; external providers can mirror writes and run review hooks | richer write lifecycle than `co-cli`; plugin hooks make writes first-class integration events |
| `letta` | memory blocks support structured edit operations (`replace`, `insert`, patch-like updates) with history | the strongest explicit edit model in the peer set; `co-cli` has targeted edit helpers but does not expose them broadly |
| `openclaw` | memory quality evolves mainly through indexing and dreaming passes rather than direct user-facing edit APIs | more batch-oriented than `co-cli`; less emphasis on single-memory surgical editing |
| `co-cli` | background extractor owns new writes; `save_memory()` always appends new files; surgical edit helpers exist but are not main-agent tools | deliberately narrower and safer than `fork-cc`/`letta` and `goose`, but also less capable for active memory maintenance |

High-signal read: `co-cli` treats new memory creation as a lifecycle concern, not a general interactive tool. That keeps the default surface small, but it also means active memory maintenance is underpowered compared with the peers that expose direct edit primitives like `goose` or `letta`.

### 4.4 Consolidation, drift control, and retention

| System | Hygiene model | Comparison to current `co-cli` |
|--------|---------------|--------------------------------|
| `elizaos` | Robust background consolidation (`consolidateToLongTerm`), exponential decay modeling, and tiered pruning | much stronger automated hygiene and lifecycle management than `co-cli` |
| `fork-cc` | autoDream periodically consolidates, prunes the entrypoint index, and nudges merging rather than duplication | substantially richer background hygiene than `co-cli` |
| `goose` | Wholly reliant on the user/model to explicitly forget/clear categories | fully manual, similar lack of background consolidation to `co-cli` |
| `hermes-agent` | optional background review nudge, trust scoring in HRR provider, contradiction search, but no default expiry | richer than `co-cli` when external providers are enabled |
| `letta` | mutation history is strong, but automatic consolidation/retention is weak | different strength profile: auditability over cleanup |
| `openclaw` | three-phase dreaming handles dedup, deeper synthesis, recovery, and pattern discovery over time | broadest consolidation pipeline in the set |
| `co-cli` | cursor-based extraction retry discipline, but no built-in dedup in `save_memory()`, no background consolidation loop, no TTL/retention loop in the current memory path | materially narrower than every peer except `letta` and `goose` on automated hygiene |

High-signal read: this is the clearest gap. Almost every persistent-memory peer invests in some form of background memory hygiene. Current `co-cli` memory is disciplined about when extraction runs, but not about what happens after the files accumulate.

### 4.5 Scope, sharing, and isolation

| System | Scope model | Comparison to current `co-cli` |
|--------|-------------|--------------------------------|
| `elizaos` | Supports multi-agent shared memory spaces with read/write permissions | multi-agent platform scope far beyond `co-cli` |
| `fork-cc` | user, project, and local scopes plus snapshot sync for team distribution | much broader scoping story than `co-cli` |
| `goose` | Project-local (`.goose/memory`) and global (`~/.config/goose`) storage scopes | natively supports local/global splits which `co-cli` currently lacks |
| `hermes-agent` | user-scoped built-in files plus one active external provider; session snapshots matter more than file scopes | more runtime-oriented than file-scope-oriented |
| `letta` | memory blocks and archives can be shared across agents, identities, and groups | multi-agent platform scope far beyond `co-cli` |
| `openclaw` | per-agent isolated SQLite stores | stronger agent isolation than `co-cli`, but aimed at a different multi-agent operating model |
| `co-cli` | effectively one configured memory space for one CLI agent; no team-sync, multi-agent sharing, or project/user/local split in the current design | simplest scope model in the survey |

High-signal read: `co-cli` is unapologetically single-agent and single-store. That keeps the implementation legible, but it also means the survey peers are solving sharing and isolation problems that `co-cli` currently chooses not to have. `goose` demonstrates that local/global scopes can be implemented trivially with a clean directory hierarchy.

### 4.6 Extensibility and safety

| System | Extensibility / safety shape | Comparison to current `co-cli` |
|--------|------------------------------|--------------------------------|
| `elizaos` | Pluggable providers, State composition hooks, memory graphs, multi-level caching | enterprise-grade extensibility, far more complex than `co-cli` |
| `fork-cc` | feature-flagged rollout, tool-constrained sub-agents, path validation, freshness cues | stronger operational control surface than `co-cli` |
| `goose` | Standard MCP protocol boundaries; standard file-system isolation | relies on MCP constraints rather than internal safety checks |
| `hermes-agent` | pluggable `MemoryProvider` layer and write-path content safety scanning | materially richer extension seam and stronger write safety than `co-cli` |
| `letta` | DB-backed memory abstractions, history, optimistic locking | stronger mutation integrity model than `co-cli` |
| `openclaw` | provider-pluggable embeddings, caching, watcher-based sync, configurable retrieval | much more infrastructure-heavy than `co-cli` |
| `co-cli` | malformed memory files are skipped safely on read; edit helpers use locks and atomic rewrites; no plugin memory provider layer and no dedicated memory write safety scanner | safer than a naive flat-file system, but less extensible and less guarded than several peers |

High-signal read: `co-cli` already has the right instincts on read-time validation and edit-time atomicity. The remaining gap is not basic correctness; it is the lack of a broader memory runtime around provider plugins, scoring layers, and write-path safety policy.

## 5. Direct Comparison Summary

### 5.1 Where `co-cli` already matches converged peer patterns

- Markdown/Text remains a viable canonical representation for agent memory (`goose`, `fork-cc`, `hermes`).
- Memory writes are model-directed, not purely rule-based ingestion.
- There is a meaningful split between standing context and query-conditioned recall.
- Context injection stays explicit and inspectable rather than hidden inside opaque retrieval middleware.

### 5.2 Where `co-cli` is materially narrower than peers

- no background consolidation or dreaming loop (unlike `elizaos`, `fork-cc`, `openclaw`)
- no memory-provider/plugin architecture (unlike `elizaos`, `hermes`)
- no trust score, contradiction pass, or richer retrieval beyond current search
- no session-frozen hot-memory tier
- no mutation history or versioned audit trail
- no project/local/global scope model (unlike `fork-cc`, `goose`)

### 5.3 Where `co-cli` has a distinct profile

- the main agent memory surface is intentionally small: search, list, recall, and extractor-owned writes
- standing context is explicit per entry through `always_on`, not implied by block type or file name
- retrieval is dynamic on each user turn instead of session-frozen
- memory and article storage share one frontmatter/file model while staying behaviorally separate
- the system favors a legible flat-file architecture over richer but heavier memory runtimes

### 5.4 Bottom line

The peer notes do not point to one converged "best" memory architecture. They point to three recurring design pressures:

- keep hot prompt memory small and intentional
- make cold memory retrieval query-conditioned
- invest in hygiene once the memory corpus starts to drift

Current `co-cli` already satisfies the first two in a lightweight way. The missing layer, relative to the peer set as a whole, is corpus hygiene after write time: consolidation, contradiction handling, retention, or trust-weighted decay. A secondary gap is support for global vs project-local memory boundaries, which peers like `goose` and `fork-cc` manage effectively without sacrificing simplicity.
