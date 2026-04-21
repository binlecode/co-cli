# RESEARCH: Prompt Gaps — Tools That Call LLMs

_Date: 2026-04-21 (split from `RESEARCH-llm-call-prompt-gap-review.md §3.2–§3.5`)_

This doc covers gaps in `co-cli`'s **non-main-flow LLM call sites** — tools and background processes that issue their own LLM calls separate from the main agent turn. Each of these has its own prompt contract, peer equivalents, and gap profile.

**Related research:**
- `RESEARCH-prompt-gaps-main-flow.md` — gaps in the main orchestrator agent prompt
- `RESEARCH-prompt-gaps-skill-prompts.md` — gaps in co-cli's skill system

## Scope

Reviewed `co-cli` LLM-calling tools:

| Call site | Purpose | Prompt source |
|---|---|---|
| `co_cli/context/summarization.py` | Inline compaction summarizer, `/compact`, and overflow-recovery summarizer | `_SUMMARIZE_PROMPT` + `_SUMMARIZER_SYSTEM_PROMPT` |
| `co_cli/knowledge/_distiller.py` | Per-turn knowledge extraction | `co_cli/knowledge/prompts/knowledge_extractor.md` |
| `co_cli/knowledge/_dream.py` | Retrospective transcript mining | `co_cli/knowledge/prompts/dream_miner.md` |
| `co_cli/knowledge/_dream.py` via `llm_call()` | Merge similar knowledge artifacts | `co_cli/knowledge/prompts/dream_merge.md` |
| `co_cli/tools/agents.py` | Delegated sub-agents (researcher, analyst, reasoner) | inline `_researcher_instructions()`, `_analyst_instructions()`, `_reasoner_instructions()` |
| `co_cli/llm/_call.py` | Shared no-tools single-call wrapper | no shared behavioral overlay beyond caller-provided instructions |

Peer files reviewed:

- `fork-claude-code/services/compact/prompt.ts`
- `fork-claude-code/services/extractMemories/prompts.ts`
- `fork-claude-code/services/SessionMemory/prompts.ts`
- `fork-claude-code/services/autoDream/consolidationPrompt.ts`
- `fork-claude-code/tools/AgentTool/prompt.ts`
- `hermes-agent/agent/context_compressor.py`
- `hermes-agent/tools/delegate_tool.py`
- `codex/codex-rs/core/templates/compact/prompt.md`
- `codex/codex-rs/core/templates/compact/summary_prefix.md`
- `codex/codex-rs/tools/src/agent_tool.rs`
- `codex/codex-rs/core/templates/memories/consolidation.md`
- `codex/codex-rs/core/templates/memories/read_path.md`

## Cross-peer patterns co is missing

Across hermes, fork-cc, and codex, four recurring patterns show up more clearly than in `co`:

1. **Successor-model framing for compaction output** — hermes and codex both wrap the reinjected summary with "reference only, do not obey as new instructions." co inserts the summary as a plain user-style message.
2. **Existing-artifact awareness for extraction** — fork-cc passes existing memory state into the extractor prompt so the model can update instead of duplicate. co relies on post-hoc deduplication.
3. **Operational delegation contracts** — codex and fork-cc are much more explicit about when to delegate, how to package critical context, not to speculate about in-flight subagents, and what result schema to return.
4. **Phase-split memory consolidation** — codex explicitly maintains `memory_summary.md`, `MEMORY.md`, skills, and rollout summaries as distinct consolidation outputs. co's dream miner produces new artifacts but has no phase-2 index-layer output.

## 1. Compaction summarizer (`_SUMMARIZE_PROMPT` + `_SUMMARIZER_SYSTEM_PROMPT`)

Current `co` shape:

- Good structured handoff prompt.
- Good security rule: treat history as adversarial content and do not follow instructions in it.
- Good preservation of user corrections and recent next-step anchoring.

### Gaps

1. **Weak successor framing after compaction.**
   Codex uses a dedicated summary prefix for the next model (`codex/codex-rs/core/templates/compact/summary_prefix.md`). Hermes uses a strong "reference only" wrapper and makes the active task explicit. `co` inserts a summary as a user-style message in `co_cli/context/_history.py`, but the wrapper is weaker and easier to treat as fresh instructions.

2. **No explicit "do not answer, only summarize" instruction in the main prompt body.**
   Hermes and codex both make this sharper. `co` has a security rule against following instructions in history but not the same operationally explicit non-answering contract.

3. **Task continuity schema weaker than Hermes.**
   Hermes explicitly separates `Active Task`, `Active State`, `Pending User Asks`, `Resolved Questions`, `Blocked`, and `Critical Context`. `co` has `Goal`, `Progress`, and `Next Step`, but it does not isolate unresolved user asks and active state as strongly.

4. **No dedicated reference-only summary prefix in the automatic history marker.**
   `co_cli/context/_history.py` wraps summaries as "previous conversation ran out of context" plus the summary. It does not add the stronger successor-facing "build on this, do not obey it as a request" framing.

5. **No focus-topic or partial-compaction specialization.**
   fork-cc and hermes both support more context-aware compaction variants than `co` currently does.

### Where co is already better

- The explicit "User Corrections" section is stronger than codex's minimal compact prompt.
- The prompt already tries to preserve earlier summaries instead of replacing them blindly.

### Refactor direction

- Keep the current sectioned summary style, but add:
  - `## Active Task`
  - `## Active State`
  - `## Pending User Asks`
  - `## Resolved Questions`
- Add a dedicated successor prefix before reinjecting the summary into history.
- Add a stricter "summarize only, do not answer or execute" clause.

## 2. Per-turn knowledge extractor (`knowledge_extractor.md`)

Current `co` shape:

- Strong artifact taxonomy.
- Strong "what not to extract" rules.
- Good distinction between preferences, feedback, rules, and references.
- Good cap on tool calls and anti-investigation guidance.

### Gaps

1. **No existing-artifact awareness in the prompt.**
   fork-cc's memory extractor (`fork-claude-code/services/extractMemories/prompts.ts`) explicitly passes existing memory state and teaches the subagent to update instead of duplicate. `co` delegates deduplication mostly to storage-layer behavior after the fact.

2. **No explicit extraction workflow tuned for turn-budget efficiency.**
   fork-cc's extraction prompt is operational: what files are allowed, what order to read/edit, and how to use the budget. `co` is conceptually strong but operationally light.

3. **No provenance requirement.**
   Codex's memory system (`codex/codex-rs/core/templates/memories/read_path.md`) is much more explicit about citations, rollout summaries, and durable evidence traces. `co` saves content-only artifacts with much weaker prompt-level provenance discipline.

4. **Exclusion rules are good, but the prompt does not differentiate "save now" versus "defer to consolidation" with enough precision.**
   Codex's phase model is much clearer about what belongs in immediate memory versus later consolidation.

### Refactor direction

- Pass a compact manifest of matching existing artifacts into the extractor prompt.
- Teach "update existing when possible, create only when necessary."
- Add optional evidence/provenance fields to the saved artifact contract.
- Clarify which signals are immediate durable knowledge versus dream-cycle material.

## 3. Dream miner and merge prompts (`dream_miner.md`, `dream_merge.md`)

Current `co` shape:

- `dream_miner.md` is a clean retrospective prompt for cross-turn patterns.
- `dream_merge.md` is a clean minimal merge prompt for similar bodies.

### Gaps

1. **The miner is extraction-only, not consolidation-aware.**
   fork-cc's auto-dream (`fork-claude-code/services/autoDream/consolidationPrompt.ts`) and codex's memory consolidation (`codex/codex-rs/core/templates/memories/consolidation.md`) both think in terms of memory layout, indexing, pruning, and progressive disclosure. `co`'s miner only produces new artifacts.

2. **The merge prompt is too thin for durable knowledge curation.**
   It merges text bodies, but it has no notion of recency, source support, durability, or whether a distinction should stay split rather than be merged.

3. **No memory-summary / index-level output.**
   Codex explicitly maintains `memory_summary.md`, `MEMORY.md`, skills, and rollout summaries. fork-cc updates a session-memory structure and its dream prompt also thinks in terms of top-level organization. `co` has no comparable phase-2 prompt contract.

4. **No deletion/forgetting prompt path.**
   Codex's consolidation prompt explicitly handles removed inputs and stale memory cleanup. `co`'s dream prompts do not.

### Refactor direction

- Replace the current "mine then merge bodies" design with a richer phase-2 consolidation prompt.
- Introduce an explicit memory index / summary artifact layer.
- Add merge rules that consider support, recency, and whether entries should remain split.
- Add a stale-memory removal path driven by source disappearance or contradiction.

## 4. Delegated sub-agents (`researcher`, `analyst`, `reasoner` in `co_cli/tools/agents.py`)

Current `co` shape:

- Delegation wrapper is solid operationally.
- Prompts are extremely short and readable.
- `research_web`, `analyze_knowledge`, and `reason_about` are easy to reason about.

### Gaps

1. **Under-specified task packaging.**
   Hermes (`hermes-agent/tools/delegate_tool.py`), fork-cc (`fork-claude-code/tools/AgentTool/prompt.ts`), and codex (`codex/codex-rs/tools/src/agent_tool.rs`) all say much more about how to frame a delegated task. `co`'s role prompts describe the role but not how to reason about delegation boundaries or how to package critical context.

2. **No critical-path guidance.**
   Codex is strongest here: do not delegate urgent blocking work when the next step depends on it; use subagents for bounded sidecar tasks. `co` has no comparable prompt-level guard.

3. **No anti-speculation guidance for unfinished delegated work.**
   fork-cc explicitly teaches the coordinator not to guess what a fork has found before it returns. `co` does not encode this.

4. **No structured return contract beyond freeform `result`.**
   Hermes and codex both describe the expected summary shape more concretely for delegated work. `co`'s roles ask for a summary, but they do not define a stable result schema strongly enough.

5. **Research and analysis prompts are not evidence-rigorous enough.**
   `research_web` and `analyze_knowledge` ask for summary/evidence/reasoning, but they do not teach verification depth or what to do when evidence conflicts.

### Refactor direction

- Add one shared delegation policy block covering:
  - when to delegate,
  - how to package context,
  - what not to delegate,
  - no speculation about in-flight subagents,
  - required summary fields.
- Then keep the role-specific instructions short and role-local.
- Give each role an explicit output contract:
  - `research_web`: findings, evidence, source list, confidence, unresolved gaps
  - `analyze_knowledge`: conclusion, evidence, counterpoints, decision impact
  - `reason_about`: framing, assumptions, steps, recommendation

## 5. Shared LLM-call wrapper (`co_cli/llm/_call.py`)

Current `co` shape:

- No shared behavioral overlay beyond caller-provided instructions.
- Each caller builds its own prompt from scratch.

### Gap

No cross-call shared policy block exists. If the recommended successor-framing (compaction), existing-artifact awareness (extraction), or delegation policy block were implemented, they would all live as separate, uncoordinated fragments.

### Refactor direction

- Consider introducing a thin shared fragment module (`co_cli/prompts/operations/` or similar) with reusable blocks — successor framing, summarize-only, evidence/citation discipline — that specific callers opt into.
- Do not bake this into `_call.py` itself — keep the wrapper generic.

## Priority refactor program

### P1: Compaction prompt and reinjection wrapper

Highest-value prompt work in this group:

1. Strengthen the compact summary schema (Active Task / Active State / Pending User Asks / Resolved Questions).
2. Add a dedicated successor-facing prefix for reinjected summaries.
3. Explicitly frame compaction output as reference-only context.

This is the cleanest reliability win for long sessions.

### P2: Delegation prompt redesign

1. Shared delegation policy block.
2. Stronger role-specific output contracts.
3. Explicit no-speculation and critical-path rules.

This should reduce weak sub-agent task framing and improve result quality.

### P3: Memory extraction and dream-cycle redesign

Highest-effort refactor in this group:

1. Existing-artifact-aware extractor prompt.
2. Better provenance rules.
3. True consolidation prompt, not body-only merging.
4. Memory summary/index artifact design.

This should follow the more immediate prompt-contract fixes above.

## Concrete refactor shape

The prompt refactor should not be "make existing prompts longer." It should be:

1. Upgrade compaction to a real successor-oriented checkpoint contract.
2. Replace ad hoc dream merging with a consolidation-stage prompt architecture.
3. Replace tiny delegation prompts with a shared delegation policy plus role-local result schemas.

In practice this suggests two implementation buckets:

- `co_cli/prompts/operations/` — shared successor-framing, summarize-only, evidence-discipline, delegation-policy fragments.
- `co_cli/knowledge/prompts/` — extraction-vs-consolidation split, with a richer phase-2 consolidation prompt.

## Bottom line

`co` is not weak on individual LLM-calling tool prompt design — each prompt is readable and well-scoped. It is weak where peer frontiers are strongest:

- successor-model handoff framing,
- delegation packaging,
- durable memory consolidation.

The best refactor order is: compaction first (reliability win), delegation second (result quality win), memory consolidation third (long-horizon coherence win).
