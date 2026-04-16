# Proposal: Self-Generated Work Prompts and Skill Promotion

_Date: 2026-04-16_

This proposal describes how `co` should synthesize a task-scoped working prompt from the user's ask, persistent memory, and indexed knowledge, then optionally promote that prompt into a reusable skill for future work.

The goal is not "more prompt text." The goal is a better control surface:

- a compact prompt for the work happening now
- a durable skill for work that repeats later
- a clear separation between instructions, memory, knowledge, and reusable procedure

---

## 1. Why This Matters Now

Current `co` already has the core ingredients:

- static prompt assembly in `co_cli/prompts/_assembly.py`
- per-turn instruction layers in `co_cli/agent/_instructions.py`
- durable memory extraction and recall in `co_cli/memory/`
- indexed external knowledge in `co_cli/knowledge/`
- reusable slash skills in `co_cli/commands/_commands.py`

What it does not yet have is a first-class way to compile those inputs into a small, task-shaped prompt artifact.

Today the system mostly works like this:

- stable identity and workflow rules are assembled once
- memory and knowledge are injected or retrieved separately
- skills are hand-authored prompt overlays
- repeated successful task setups remain diffuse across docs, memories, and chat history

That is workable, but it is now behind the frontier shape.

Recent frontier systems are converging on the same pattern:

- **Anthropic** recommends simple composable agent patterns built from retrieval, tools, and memory, not oversized prompt frameworks. Their agent guidance treats memory as a first-class augmentation and specialized routing/prompts as explicit workflow stages.
- **Anthropic Claude Code** separates durable memory files from reusable subagents, with each subagent owning a specific prompt and its own context window.
- **OpenAI's April 15, 2026 Agents SDK update** explicitly calls out configurable memory, skills, `AGENTS.md`, and sandbox-aware orchestration as common primitives in frontier agent systems.
- **OpenAI's February 11, 2026 harness engineering writeup** argues that `AGENTS.md` should stay short and act as a table of contents while structured repo docs remain the system of record.
- **Google Vertex AI Memory Bank** treats memory as isolated, self-contained facts that are extracted, consolidated, and retrieved asynchronously instead of dumping raw conversation back into context.

The practical implication for `co` is straightforward:

- `co` should stop relying on prompt assembly alone
- `co` should compile a work prompt from evidence
- `co` should save stable procedures as skills, not as loose chat fragments

---

## 2. Product Thesis

`co` should have a **Prompt Synthesis Layer** that turns:

- the user's current ask
- relevant memories
- relevant knowledge articles
- relevant session context
- active repo instructions

into one of two artifacts:

1. **Current-work prompt**
   Used immediately for the active task, then discarded unless promoted.

2. **Future-work skill draft**
   A reusable procedural asset saved for later review and promotion.

The important distinction is:

- **memory** stores facts, preferences, and durable context
- **knowledge** stores reference material and source text
- **skills** store reusable procedures
- **current-work prompts** store short-lived execution framing

That separation is the design.

---

## 3. What Good Looks Like

The target system should satisfy these properties:

- **Evidence-backed**: synthesized prompts should cite which memories, articles, or session facts they came from.
- **Compact**: prompt synthesis should reduce prompt sprawl, not add another blob.
- **Task-scoped**: the current-work prompt should match the current ask, not become a generic personality overlay.
- **Reusable by default only when earned**: only repeated, stable procedures should become skills.
- **Inspectable**: users and maintainers should be able to see the synthesized prompt and why it was built that way.
- **Safe against drift**: synthesized prompts must never rewrite soul files, rule files, or core policy automatically.

---

## 4. Proposal

### 4.1 Add a prompt synthesis stage before execution

For eligible tasks, `co` should run a small synthesis pass before the main work turn.

High-level flow:

1. Classify the ask.
2. Retrieve relevant memory, knowledge, and recent session evidence.
3. Build a compact structured work prompt.
4. Decide whether the result is:
   - ephemeral for the current task
   - a candidate reusable skill
5. Execute using the synthesized prompt.
6. If the workflow proves reusable, offer promotion into a skill draft.

This should be implemented as an explicit workflow, not hidden prompt magic.

### 4.2 Introduce a first-class artifact: `WorkPromptArtifact`

A synthesized prompt should not be treated as plain text only. It should have structure.

Suggested shape:

| Field | Purpose |
| --- | --- |
| `goal` | normalized task objective |
| `mode` | `current_work` or `skill_candidate` |
| `constraints` | key limits from repo instructions, user feedback, and policy |
| `memory_refs` | memory IDs/slugs used to shape the prompt |
| `knowledge_refs` | article or search result paths used as grounding |
| `session_refs` | recent session IDs or snippets when relevant |
| `acceptance_checks` | concrete completion criteria |
| `prompt_text` | final compiled prompt for the model |
| `promotion_hint` | why this may deserve skill promotion |

The structured envelope matters because frontier systems increasingly separate agent state from raw prompt text. `co` should do the same.

### 4.3 Synthesize the prompt from layers, not one big blob

The prompt compiler should use this precedence order:

1. Core identity and policy
2. Repo/project instructions
3. Task-specific user ask
4. Retrieved memory and knowledge
5. Execution plan and acceptance checks

This preserves the current role of:

- soul seed and rule files as stable foundations
- memories as personalization and continuity
- knowledge articles as evidence
- skills as procedural reuse

It also aligns with the "AGENTS.md as table of contents" direction rather than turning `AGENTS.md` into a giant instruction dump.

### 4.4 Add a bundled skill that uses the compiler

The first user-facing version should be a bundled skill, not a silent always-on behavior.

Suggested behavior:

- `/forge <task>` or `/work-prompt <task>`
- the skill instructs `co` to:
  - compile a task prompt from memory and knowledge
  - show the synthesized prompt or a concise summary
  - use it immediately for the current task
  - note whether the pattern looks reusable

This keeps rollout legible and easy to evaluate.

### 4.5 Add promotion from successful work prompt to reusable skill

If a synthesized prompt proves useful and repeatable, `co` should be able to draft a reusable skill markdown file.

That draft should include:

- `name`
- `description`
- `argument-hint`
- optional `requires`
- generated body
- provenance comments listing the memory and article inputs that shaped it

Important boundary:

- **draft generation can be automatic**
- **durable save must be explicit and approved**

This follows the repo's existing bias toward inspectable files and avoids uncontrolled prompt self-modification.

---

## 5. Runtime Design

### 5.1 Retrieval inputs

The compiler should pull from existing stores rather than introducing a parallel memory system.

Primary sources:

- user ask
- `deps.skill_commands` and visible skill registry
- top relevant memory search results
- top relevant knowledge search results
- recent session search hits when the current task is continuation-heavy
- active repo instructions already available through `AGENTS.md` and docs

This means the system reuses:

- `search_memories`
- `search_knowledge`
- session index retrieval
- current static prompt and dynamic instruction layers

### 5.2 Prompt template

The compiler should render a short fixed-shape prompt, for example:

```text
Goal:
<normalized task>

Why this matters now:
<task-specific framing>

Relevant standing context:
<2-5 recalled memory facts>

Relevant source material:
<2-5 knowledge refs with one-line reasons>

Constraints:
<repo and user constraints>

Working plan:
<short execution shape>

Definition of done:
<acceptance checks>

If this pattern repeats:
<skill-promotion hint>
```

The model should receive a compact scaffold plus references, not a giant restatement of every source document.

### 5.3 Promotion heuristic

A current-work prompt should be considered a skill candidate only when most of these are true:

- the task pattern is likely to recur
- the procedure is more stable than the task content
- the prompt relies on durable procedure, not ephemeral facts
- there is a clear argument surface
- the workflow can be described as reusable steps

Examples that should promote well:

- "review an implementation against repo discipline"
- "plan a delivery doc from repo state and current ask"
- "prepare a bugfix execution brief from failing tests and active plan"

Examples that should not promote:

- one-off incident cleanup
- user-specific personal preference notes
- prompts whose value depends mostly on transient session state

---

## 6. Storage and Scope

### 6.1 Do not store synthesized prompts as memories

This is a critical design rule.

Synthesized prompts are not memories because they are not durable facts about the user or project. Storing them as memories would pollute recall and create self-reinforcing prompt drift.

### 6.2 Short-term artifact storage

For inspection and evals, `co` should optionally store recent synthesized prompt artifacts in a dedicated local directory such as:

- `~/.co-cli/work-prompts/`

These should be treated as debug/eval artifacts, not primary knowledge.

### 6.3 Long-term storage should be skills

If promoted, the artifact should become a proper skill file.

Longer term, `co` should add **project-local skills** in addition to bundled and user-global tiers. Frontier systems consistently support project and user scopes for reusable agent behavior, and generated skills are often project-specific.

Suggested future load order:

1. bundled
2. user-global
3. project-local `.co-cli/skills/`

Project-local promotion is the right default for repo-specific workflows.

---

## 7. Safety Boundaries

This feature should stay inside these constraints:

### 7.1 No automatic rewriting of core behavior files

The synthesis system must not directly rewrite:

- soul files
- rule files
- `AGENTS.md`
- specs
- existing skill files

without explicit user approval and normal file-edit flow.

### 7.2 No hidden prompt accumulation

Each synthesized prompt should be built from current evidence, not recursively built from previous synthesized prompts. Otherwise the system will optimize for its own artifacts instead of for the task.

### 7.3 No durable promotion without review

Generated skills should be drafted automatically at most. Promotion into a live skill should require explicit acceptance.

### 7.4 Keep prompt size bounded

The compiler should prefer references and distilled constraints over copying source text. Frontier long-context systems still reward compaction and reuse; they do not justify dumping everything into each turn.

---

## 8. Recommended Implementation Shape

### Phase 1: Current-work prompt synthesis

Goal:

- generate a task-scoped prompt for the current ask
- expose it through a bundled skill
- do not save durable artifacts by default

Likely code shape:

- new settings group: `PromptSynthesisSettings`
- new module: `co_cli/prompt_synthesis/`
- read-only synthesis tool returning `WorkPromptArtifact`
- bundled skill that invokes synthesis before main execution

### Phase 2: Inspectability and evals

Goal:

- save optional prompt artifacts for debugging
- measure whether synthesis improves task success and reduces prompt sprawl

Likely additions:

- artifact logging under `~/.co-cli/work-prompts/`
- evals comparing baseline vs synthesized execution
- telemetry for which evidence sources were used

### Phase 3: Skill draft generation

Goal:

- promote repeatable prompt artifacts into reusable skill drafts

Likely additions:

- `generate_skill_draft` path
- review-and-save flow
- provenance header in generated skill markdown

### Phase 4: Project-local skill scope

Goal:

- save repo-specific generated skills into `.co-cli/skills/`

This is the right time to expand skill loading order and promotion targets.

---

## 9. Success Criteria

This proposal is worthwhile only if it improves actual task performance and reuse.

The feature should be considered successful if it:

- reduces repeated user steering on similar tasks
- improves first-pass task framing for complex requests
- produces reusable skill drafts that are actually adopted
- keeps prompt growth bounded
- preserves or improves task success under current quality gates

Suggested eval questions:

- Does synthesized framing reduce the number of turns needed to get started?
- Does it increase tool selection quality?
- Does it reduce repeated instruction copying across sessions?
- Are promoted skills materially better than hand-written baseline skills?
- Does prompt size stay below existing context budgets?

---

## 10. Recommendation

`co` should adopt self-generated work prompts, but in a narrow and inspectable way:

- **yes** to task-scoped prompt synthesis
- **yes** to explicit evidence-backed prompt artifacts
- **yes** to reusable skill draft generation
- **no** to silent autonomous rewriting of core prompt assets
- **no** to storing synthesized prompts as memory

The best first version is:

1. add a read-only prompt compiler
2. expose it through a bundled skill for the current task
3. measure it
4. only then add promotion into project-local skills

That path aligns with frontier 2026 agent systems without copying their complexity blindly. It extends the architecture `co` already has instead of replacing it with a heavier framework.

---

## Sources

- Anthropic, "Building effective agents" (Dec 19, 2024): https://www.anthropic.com/engineering/building-effective-agents
- Anthropic docs, "Manage Claude's memory": https://docs.anthropic.com/en/docs/claude-code/memory
- Anthropic docs, "Subagents": https://docs.anthropic.com/en/docs/claude-code/sub-agents
- OpenAI, "The next evolution of the Agents SDK" (Apr 15, 2026): https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- OpenAI, "Harness engineering: leveraging Codex in an agent-first world" (Feb 11, 2026): https://openai.com/index/harness-engineering/
- Google Cloud, "Vertex AI Agent Engine Memory Bank overview": https://cloud.google.com/agent-builder/agent-engine/memory-bank/overview
- Google AI for Developers, "Long context": https://ai.google.dev/gemini-api/docs/long-context
