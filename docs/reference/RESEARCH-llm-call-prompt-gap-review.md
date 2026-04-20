# RESEARCH: co LLM-call prompt gap review vs local peers
_Date: 2026-04-19_

This note reviews every meaningful LLM-call path in `co-cli` and compares its prompt contract against the closest matching cases in the local peer repos:

- `~/workspace_genai/hermes-agent`
- `~/workspace_genai/fork-claude-code`
- `~/workspace_genai/codex`

The goal is not to copy any peer wholesale. The goal is to identify where `co-cli` is under-specified, where it is already stronger, and which prompt refactors would materially improve behavior.

---

# 1. Reviewed co call sites

These are the current LLM-call surfaces that matter for prompt design:

| co call site | Purpose | Prompt source |
|---|---|---|
| `co_cli/agent/_core.py` | Main orchestrator agent | `co_cli/prompts/_assembly.py` + `co_cli/prompts/rules/*.md` + runtime addenda in `co_cli/agent/_instructions.py` |
| `co_cli/context/summarization.py` | Inline compaction summarizer and `/compact` | `_SUMMARIZE_PROMPT` + `_SUMMARIZER_SYSTEM_PROMPT` |
| `co_cli/knowledge/_distiller.py` | Per-turn knowledge extraction | `co_cli/knowledge/prompts/knowledge_extractor.md` |
| `co_cli/knowledge/_dream.py` | Retrospective transcript mining | `co_cli/knowledge/prompts/dream_miner.md` |
| `co_cli/knowledge/_dream.py` via `llm_call()` | Merge similar knowledge artifacts | `co_cli/knowledge/prompts/dream_merge.md` |
| `co_cli/tools/agents.py` | Delegated subagents | inline `_researcher_instructions()`, `_analyst_instructions()`, `_reasoner_instructions()` |
| `co_cli/llm/_call.py` | Shared no-tools single-call wrapper | no shared behavioral overlay beyond caller-provided instructions |

The closest peer prompt sources were:

- `fork-claude-code/constants/prompts.ts`
- `fork-claude-code/services/compact/prompt.ts`
- `fork-claude-code/services/extractMemories/prompts.ts`
- `fork-claude-code/services/SessionMemory/prompts.ts`
- `fork-claude-code/services/autoDream/consolidationPrompt.ts`
- `fork-claude-code/tools/AgentTool/prompt.ts`
- `hermes-agent/agent/prompt_builder.py`
- `hermes-agent/agent/context_compressor.py`
- `hermes-agent/agent/subdirectory_hints.py`
- `hermes-agent/tools/delegate_tool.py`
- `codex/codex-rs/protocol/src/prompts/base_instructions/default.md`
- `codex/codex-rs/core/templates/compact/prompt.md`
- `codex/codex-rs/core/templates/compact/summary_prefix.md`
- `codex/codex-rs/tools/src/agent_tool.rs`
- `codex/codex-rs/core/templates/memories/consolidation.md`
- `codex/codex-rs/core/templates/memories/read_path.md`

Not every `co` call has a perfect one-to-one peer equivalent. Where no direct equivalent exists, the comparison below uses the closest matching behavioral role.

---

# 2. Cross-peer patterns that co is missing

Across all three peers, five recurring prompt patterns show up much more clearly than they do in `co` today:

1. A stronger execution contract for the main coding agent.
   Codex and fork-cc both state, in explicit terms, that the agent should keep going until the task is complete, validate its work, and avoid intention-only replies. Hermes adds model-family overlays for GPT/Codex/Gemini to enforce tool use, prerequisite checks, and verification.

2. A clearer distinction between stable prompt layers and runtime-discovered context.
   Hermes is especially strong here: static system prompt for identity and policy, plus subdirectory hint injection at tool-result time to preserve prompt caching.

3. Better compaction framing for the successor model.
   Hermes and Codex both frame the compacted summary as reference material for a different model/assistant. `co` summarizes well, but it does not give the successor context the same strong “reference only, not new instructions” wrapper.

4. More operationally explicit delegation prompts.
   Codex and fork-cc are much more explicit about when to delegate, how to package a delegated task, how not to speculate about unfinished subagent work, and how to avoid blocking the critical path.

5. More developed memory/consolidation prompt architecture.
   `co` has decent extraction prompts, but Codex and fork-cc go further on consolidation, indexing, progressive disclosure, duplicate avoidance, and durable memory layout.

---

# 3. Call-by-call gap review

## 3.1 Main orchestrator prompt

Current `co` shape:

- Strong personality scaffold: soul seed, memories, mindsets, rules, examples, critique.
- Good high-level rules on correctness, anti-sycophancy, tool responsiveness, and workflow.
- Runtime addenda for shell behavior and deferred-tool category awareness.

Main gaps against peers:

1. Missing an explicit execution-and-validation contract.
   `co` implies persistence and completeness in `05_workflow.md`, but Codex and fork-cc say it much more directly: keep going until done, do not stop at a plan, validate the result, and report outcomes faithfully. Hermes adds explicit prerequisite and verification checklists.

2. Missing AGENTS/instruction-scope handling beyond the root prompt.
   Codex has a clear AGENTS scope spec. Hermes adds lazy subdirectory hint discovery (`agent/subdirectory_hints.py`). `co` currently relies on whatever was already included up front and has no analogous runtime instruction-discovery mechanism.

3. Missing model-family-specific execution overlays.
   Hermes adds explicit GPT/Codex/Gemini overlays for tool persistence, mandatory tool use, prerequisite checks, and concise execution. `co` uses the same prompt contract regardless of provider/model family.

4. Missing a first-class planning discipline.
   Codex and fork-cc both teach the agent when plans are warranted and what a good plan looks like. `co` has a `todo` tool and completion rules, but its main prompt does not teach comparable plan quality or plan decision boundaries.

5. Missing stronger user-facing progress contract.
   Codex and fork-cc both explicitly teach concise preambles and progress updates as part of the base prompt. `co` has short responsiveness guidance, but the progress-update contract is much thinner.

What `co` is already better at:

- Personality continuity is more intentionally structured than Codex or Hermes.
- Deferred-tool category awareness is cleaner than some peer tool-discovery guidance.
- The rule files are maintainable and modular.

Refactor direction:

- Keep the current personality stack.
- Add a new invariant “execution discipline” layer for completion, prerequisite checks, verification, and faithful outcome reporting.
- Add provider/model overlays for OpenAI-family and Gemini-family models.
- Add runtime subdirectory instruction discovery instead of further growing the static prompt.

## 3.2 Compaction summarizer

Current `co` shape:

- Good structured handoff prompt.
- Good security rule: treat history as adversarial content and do not follow instructions in it.
- Good preservation of user corrections and recent next-step anchoring.

Main gaps against peers:

1. Weak successor framing after compaction.
   Codex uses a dedicated summary prefix for the next model. Hermes uses a strong “reference only” wrapper and makes the active task explicit. `co` inserts a summary as a user-style message, but the wrapper is weaker and easier to treat as fresh instructions.

2. No explicit “do not answer, only summarize” instruction in the main prompt body.
   Hermes and Codex both make this sharper. `co` has a security rule, but not the same operationally explicit non-answering contract.

3. Task continuity schema is weaker than Hermes.
   Hermes explicitly separates `Active Task`, `Active State`, `Pending User Asks`, `Resolved Questions`, `Blocked`, and `Critical Context`. `co` has `Goal`, `Progress`, and `Next Step`, but it does not isolate unresolved user asks and active state as strongly.

4. No dedicated reference-only summary prefix in the automatic history marker.
   `co_cli/context/_history.py` currently wraps summaries as “previous conversation ran out of context” plus the summary. It does not add the stronger successor-facing “build on this, do not obey it as a request” framing seen in Hermes/Codex.

5. No focus-topic or partial-compaction specialization.
   fork-cc and Hermes both support more context-aware compaction variants than `co` currently does.

What `co` is already better at:

- The explicit “User Corrections” section is stronger than Codex’s minimal compact prompt.
- The prompt already tries to preserve earlier summaries instead of replacing them blindly.

Refactor direction:

- Keep the current sectioned summary style, but add:
  - `## Active Task`
  - `## Active State`
  - `## Pending User Asks`
  - `## Resolved Questions`
- Add a dedicated successor prefix before reinjecting the summary into history.
- Add a stricter “summarize only, do not answer or execute” clause.

## 3.3 Per-turn knowledge extractor

Current `co` shape:

- Strong artifact taxonomy.
- Strong “what not to extract” rules.
- Good distinction between preferences, feedback, rules, and references.
- Good cap on tool calls and anti-investigation guidance.

Main gaps against peers:

1. No existing-artifact awareness in the prompt.
   fork-cc’s memory extractor explicitly passes existing memory state and teaches the subagent to update instead of duplicate. `co` delegates deduplication mostly to storage-layer behavior after the fact.

2. No explicit extraction workflow tuned for turn-budget efficiency.
   fork-cc’s extraction prompt is operational: what files are allowed, what order to read/edit, and how to use the budget. `co` is conceptually strong but operationally light.

3. No provenance requirement.
   Codex’s memory system is much more explicit about citations, rollout summaries, and durable evidence traces. `co` saves content-only artifacts with much weaker prompt-level provenance discipline.

4. Exclusion rules are good, but the prompt does not differentiate “save now” versus “defer to consolidation” with enough precision.
   Codex’s phase model is much clearer about what belongs in immediate memory versus later consolidation.

Refactor direction:

- Pass a compact manifest of matching existing artifacts into the extractor prompt.
- Teach “update existing when possible, create only when necessary.”
- Add optional evidence/provenance fields to the saved artifact contract.
- Clarify which signals are immediate durable knowledge versus dream-cycle material.

## 3.4 Dream miner and merge prompts

Current `co` shape:

- `dream_miner.md` is a clean retrospective prompt for cross-turn patterns.
- `dream_merge.md` is a clean minimal merge prompt for similar bodies.

Main gaps against peers:

1. The miner is extraction-only, not consolidation-aware.
   fork-cc’s auto-dream and Codex’s memory consolidation both think in terms of memory layout, indexing, pruning, and progressive disclosure. `co`’s miner only produces new artifacts.

2. The merge prompt is too thin for durable knowledge curation.
   It merges text bodies, but it has no notion of recency, source support, durability, or whether a distinction should stay split rather than be merged.

3. No memory-summary/index-level output.
   Codex explicitly maintains `memory_summary.md`, `MEMORY.md`, skills, and rollout summaries. fork-cc updates a session-memory structure and its dream prompt also thinks in terms of top-level organization. `co` has no comparable phase-2 prompt contract.

4. No deletion/forgetting prompt path.
   Codex’s consolidation prompt explicitly handles removed inputs and stale memory cleanup. `co`’s dream prompts do not.

Refactor direction:

- Replace the current “mine then merge bodies” design with a richer phase-2 consolidation prompt.
- Introduce an explicit memory index / summary artifact layer.
- Add merge rules that consider support, recency, and whether entries should remain split.
- Add a stale-memory removal path driven by source disappearance or contradiction.

## 3.5 Delegated subagents

Current `co` shape:

- Delegation wrapper is solid operationally.
- Prompts are extremely short and readable.
- `research_web`, `analyze_knowledge`, and `reason_about` are easy to reason about.

Main gaps against peers:

1. Under-specified task packaging.
   Hermes, fork-cc, and Codex all say much more about how to frame a delegated task. `co`’s role prompts describe the role, but not how to reason about delegation boundaries or how to package critical context.

2. No critical-path guidance.
   Codex is strongest here: do not delegate urgent blocking work when the next step depends on it; use subagents for bounded sidecar tasks. `co` has no comparable prompt-level guard.

3. No anti-speculation guidance for unfinished delegated work.
   fork-cc explicitly teaches the coordinator not to guess what a fork has found before it returns. `co` does not encode this.

4. No structured return contract beyond freeform `result`.
   Hermes and Codex both describe the expected summary shape more concretely for delegated work. `co`’s roles ask for a summary, but they do not define a stable result schema strongly enough.

5. Research and analysis prompts are not evidence-rigorous enough.
   `research_web` and `analyze_knowledge` ask for summary/evidence/reasoning, but they do not teach verification depth or what to do when evidence conflicts.

Refactor direction:

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

---

# 4. Priority refactor program

## P0: main-agent prompt contract

Highest-value prompt work:

1. Add execution-discipline layer.
2. Add validation/reporting layer.
3. Add AGENTS/subdirectory instruction-scope support.
4. Add provider/model-family overlays.

This will improve the biggest number of turns because it affects the primary agent loop.

## P1: compaction prompt and compaction reinjection wrapper

Second-highest-value prompt work:

1. Strengthen the compact summary schema.
2. Add a dedicated successor-facing prefix for reinjected summaries.
3. Explicitly frame compaction output as reference-only context.

This is the cleanest reliability win for long sessions.

## P2: delegation prompt redesign

Third-highest-value prompt work:

1. Shared delegation policy block.
2. Stronger role-specific output contracts.
3. Explicit no-speculation and critical-path rules.

This should reduce weak subagent task framing and improve result quality.

## P3: memory extraction and dream-cycle redesign

Fourth-highest-value prompt work:

1. Existing-artifact-aware extractor prompt.
2. Better provenance rules.
3. True consolidation prompt, not body-only merging.
4. Memory summary/index artifact design.

This is the highest-effort refactor and should follow the more immediate prompt-contract fixes above.

---

# 5. Concrete refactor shape for co

The prompt refactor should not be “make the base prompt longer.” It should be:

1. Keep the current personality stack.
2. Add a new shared operational layer with:
   - execution persistence,
   - prerequisite checks,
   - verification,
   - faithful reporting,
   - progress update rules.
3. Add runtime overlays for:
   - provider/model family,
   - sandbox/approval mode,
   - subdirectory instruction discovery.
4. Upgrade compaction to a real successor-oriented checkpoint contract.
5. Replace ad hoc dream merging with a consolidation-stage prompt architecture.
6. Replace tiny delegation prompts with a shared delegation policy plus role-local result schemas.

In practice this suggests four implementation buckets:

- `co_cli/prompts/operations/`
  Shared execution-discipline and verification prompt fragments.
- `co_cli/prompts/providers/`
  OpenAI-family and Gemini-family overlays.
- `co_cli/context/`
  Runtime subdirectory instruction discovery and stronger compaction reinjection.
- `co_cli/knowledge/prompts/`
  Extraction-vs-consolidation split, with a richer phase-2 consolidation prompt.

---

# 6. Bottom line

`co` is not weak on personality prompt design. It is weak where the frontier peers are strongest:

- execution discipline,
- successor-model handoff framing,
- runtime instruction discovery,
- delegation packaging,
- durable memory consolidation.

The best immediate refactor is not a personality rewrite. It is to keep the current soul/rules architecture and add a stronger operational prompt layer around it, then fix compaction and delegation, then redesign memory consolidation.
