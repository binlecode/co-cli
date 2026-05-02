# RESEARCH: self-model and working-style quality
_Date: 2026-03-08 | Revised: 2026-05-01_

This document expands the brief `6.0 Self/personality model quality` note in `docs/reference/RESEARCH-peer-systems.md`.

This document is intended as a handoff-quality design review for a tech lead and implementation team. It is not a TODO breakdown. It should provide:

- the gap framing
- the target architecture
- the main implementation shape
- phased goals that can later be split into delivery work

The question here is not "does co have a personality?" It clearly does. The question is whether co has a high-quality **self model**: an explicit, durable, inspectable definition of how it should behave across tasks, contexts, and time.

The practical thesis is:

- personality should be treated as a **working-style layer**
- working style should be explicit enough to tune and inspect
- working style should be subordinate to trust, truthfulness, approvals, and task completion
- one `co` deployment should be modeled as **one character instance**
- future multi-character teamwork should be built by composing multiple `co` instances, not by collapsing several characters into one instance

---

# 1. What Was Reviewed

- **Current co design/docs:** `docs/DESIGN-personalization.md`, `docs/DESIGN-context-engineering.md`, `docs/DESIGN-system.md`, `docs/reference/RESEARCH-peer-systems.md`, `docs/reference/ROADMAP-co-evolution.md`
- **Current co implementation (as of 2026-05-01):**
  - `co_cli/personality/prompts/loader.py` — soul asset loading
  - `co_cli/personality/prompts/validator.py` — role discovery and file validation
  - `co_cli/personality/prompts/souls/` — 42 `.md` files across finch, jeff, tars
  - `co_cli/context/assembly.py` — static instruction assembly
  - `co_cli/context/rules/` — behavioral policy (01–05)
  - `co_cli/agent/core.py` — agent construction and critique injection
  - `co_cli/memory/artifact.py` — knowledge artifact schema including preference kind
  - `co_cli/tools/memory/` — memory read/write/search toolset
  - `co_cli/config/core.py` — personality config field
- **Frontier/peer patterns already captured in repo research:** OpenAI memory/agent direction, Anthropic Claude Code memory + subagents, Letta typed memory blocks, Mem0 structured memory operations, Codex/Claude Code prompt-and-policy discipline

---

# 2. Current co Shape

co already has a meaningful personality system.

In product terms, the current model is already closest to:

- one active `co` instance
- one selected role/personality for that instance
- one working relationship built around that role

That is the right base abstraction. A future team setting should look like:

- `finch` instance
- `jeff` instance
- `tars` instance

Each with its own self model, memory state, and working-style defaults. Teamwork should happen between instances, not inside one blended prompt identity.

## 2.1 Actual architecture (as of 2026-05-01)

The personality system has been refactored into its own package. The key components are:

**Role discovery and validation** — `co_cli/personality/prompts/validator.py`

Three roles are supported: `finch`, `jeff`, `tars`. They are auto-discovered from the `souls/` directory. Six mindset task types are required for each role: `technical`, `exploration`, `debugging`, `teaching`, `emotional`, `memory`. Missing files surface as warnings at config load time.

**Soul asset loading** — `co_cli/personality/prompts/loader.py`

Three load functions: `load_soul_seed(role)`, `load_soul_mindsets(role)`, `load_soul_critique(role)`. These are called during agent construction, not per-turn.

**Static instruction assembly** — `co_cli/context/assembly.py` → `build_static_instructions(config)`

Assembled once per agent session in strict order:
1. Soul seed (`souls/{role}/seed.md`) — identity anchor
2. Soul mindsets (all 6 task-type guidance files joined) — behavioral stance per task shape
3. Behavioral rules (numbered `01_identity.md` through `05_workflow.md`) — policy layer
4. Recency-clearing advisory — explains `[tool result cleared…]` placeholders to the model

**Critique injection** — `co_cli/agent/core.py` → `build_agent()`

After toolset guidance is assembled, `load_soul_critique(role)` is appended as a `## Review lens` section in the static instructions block. This gives each role an always-on review posture without a separate per-turn call.

**Per-turn instructions** — two `@agent.instructions` callbacks in `core.py`

- `safety_prompt` — structural behavioral guardrails (conditional per session state)
- `current_time_prompt` — ephemeral grounding (date/time)

No per-turn style injection exists. All personality content is in the static (cached) block.

**Character memory (canon)** — `souls/{role}/memories/*.md`

YAML-frontmattered files with `decay_protected: true`, `tags: [role, character]`. Loaded on demand via `search_canon()` in `co_cli/tools/memory/_canon_recall.py` using token-overlap scoring (title weighted 2×, score ≥ 2 required). Canon hits return full body inline as part of `memory_search()`. This sublayer carries scenes, speech patterns, and behavioral observations from source material.

**Config** — `co_cli/config/core.py` → `Settings.personality`

Single string field. Validated against `VALID_PERSONALITIES` at load time. Default: `"tars"`. Override via `CO_PERSONALITY` env var.

**Knowledge artifacts with preference kind** — `co_cli/memory/artifact.py`

`ArtifactKindEnum.PREFERENCE` exists as a generic kind for user communication and feedback style. No typed subtypes, scoped strength, or supersedes fields are present yet.

## 2.2 What has changed since the March 2026 draft

The March draft correctly diagnosed the gap but described the codebase as it existed at that time. Since then:

- Personality code moved from `co_cli/tools/personality.py` to `co_cli/personality/` package
- `build_static_instructions()` was centralized in `co_cli/context/assembly.py` (previously scattered)
- Critique injection moved into `build_agent()` in `co_cli/agent/core.py`
- Rule files were renumbered and consolidated under `co_cli/context/rules/`
- Canon recall was formalized in `co_cli/tools/memory/_canon_recall.py` with token-overlap scoring

None of the P1/P2 proposals from the March draft have been built: no `style.yaml`, no `ResolvedStyle` resolver, no typed preference subtypes, no `/style` inspection command.

## 2.3 Strengths of the current stack

The current stack is strong in three ways:

- **Clear static identity anchor**: soul seed, mindsets, and rule files are assembled into a stable base prompt with a defined order contract
- **Layered asset separation**: identity (seed + critique), policy (rules), task guidance (mindsets), and character memory (canon) are in distinct files with distinct load paths
- **Maintainer legibility**: behavior is defined in plain markdown files, not hidden in Python branches

That is better than many systems that just stuff "be helpful, concise, warm" into one prompt blob.

But the current architecture is still primarily a **prompt-composition system**, not yet a robust self-model system.

Today co's self/personality behavior is encoded mostly as:

- prose in `co_cli/personality/prompts/souls/{role}/seed.md`
- prose in `co_cli/personality/prompts/souls/{role}/mindsets/*.md`
- numbered rule files in `co_cli/context/rules/`
- prose in `co_cli/personality/prompts/souls/{role}/critique.md`
- on-demand recall of `souls/{role}/memories/*.md` via `memory_search()`

This is coherent, but it has a limitation: the behavior contract is distributed across prompt text rather than represented as explicit dimensions with stable semantics.

---

# 3. Frontier Standard

The frontier does not converge on theatrical personas. It converges on:

- reliable behavioral constraints
- scoped personalization
- inspectable memory and preferences
- context-sensitive adaptation
- stable trust posture around actions

The strongest systems use "personality" as a thin layer over more durable control surfaces:

- **OpenAI / Anthropic direction**: stable defaults, memory controls, project scope, agent behavior bounded by clear product policy
- **Claude Code / Codex pattern**: strong operational discipline and permission posture matter more than expressive style
- **Letta / Mem0 lesson**: structured state beats diffuse prose when behavior must evolve over time

So the relevant benchmark is not "is co charming?" It is:

- can co adapt without losing identity?
- can maintainers intentionally tune that adaptation?
- can users predict how co will behave in risky or ambiguous situations?
- can the system improve style without destabilizing trust?

---

# 4. Gap Analysis

## 4.1 Gap: self model is mostly implicit

co has a soul and rules, but not yet an explicit schema for its own behavior dimensions.

Examples of dimensions that exist implicitly today:

- directness vs expansiveness
- challenge vs deference
- warmth vs neutrality
- action bias vs deliberation
- confidence expression vs uncertainty surfacing
- initiative vs waiting for instruction

These dimensions are present in prose (seed.md and mindsets), but not represented as explicit fields or policies.

Consequence:

- behavior tuning is indirect — you rewrite prose, not a value
- changes are harder to review — a diff on seed.md doesn't tell you which dimension moved
- regressions appear as "prompt drift" rather than a visible contract change

## 4.2 Gap: context adaptation is file-driven, not state-driven

Mindsets give co task-shape adaptation, which is good, but the adaptation is still largely static. The six mindset files are all loaded together and joined into one block — the model must infer which applies.

The system does not yet have a formal runtime concept like:

- `task_mode=debugging`
- `user_stress=high`
- `risk_level=elevated`
- `decision_irreversibility=high`
- `response_contract=brief_direct`

Consequence:

- style selection relies on the model's implicit pattern matching across all six mindsets in one block
- the system has limited ability to deliberately shift style when stakes change
- no mechanism exists to suppress less-relevant mindsets during a specific task shape

## 4.3 Gap: preference artifacts are a weak control surface

`ArtifactKindEnum.PREFERENCE` entries help with continuity, but the schema is still a rough mechanism. A preference artifact is just markdown with a description — no structured fields for scope, strength, recency, or resolution precedence.

They work well for reminders like:

- "the user prefers blunt feedback"
- "the user dislikes long preambles"

They work less well for more precise behavior control like:

- this preference applies only in coding tasks
- this preference is soft, not mandatory
- this preference was corrected last week and should supersede older ones
- this preference should never override safety or factual caution

Consequence:

- user preferences can be remembered without being applied with enough nuance
- prompt-level style and memory-level preferences can conflict without a clear arbitration rule

## 4.4 Gap: critique exists, but self-governance is thin

`critique.md` gives co an always-on review lens. That is useful, but it is still a prose layer, not a real self-governance loop.

What is missing:

- explicit style failure categories
- lightweight self-checks tied to task/risk level
- persistent quality signals about where co's behavior repeatedly misses
- structured revision workflow for personality tuning

Consequence:

- co can be asked to critique itself, but it does not yet accumulate a reliable model of its behavioral weaknesses

## 4.5 Gap: no clean separation between identity, policy, and learned style

Today several things live close together in the prompt stack:

- identity and voice (seed.md)
- safety and approval policy (rules/02_safety.md, rules/04_tool_protocol.md)
- task workflow rules (rules/05_workflow.md)
- learned user-facing style preferences (preference artifacts — recalled dynamically, but no formal injection contract)

The docs say these are conceptually distinct, but the runtime contract is still mostly "all of this becomes prompt text" with no explicit precedence definition.

Consequence:

- maintainers can accidentally use identity text to compensate for policy gaps
- user-facing style customization can drift too close to core operational rules
- it is harder to reason about what is allowed to change dynamically

## 4.6 Gap: instance identity and future multi-agent identity are not yet separated cleanly

The current personality design is compatible with "one instance, one character," but the architecture does not yet state that boundary strongly enough.

That matters because future teamwork between `finch`, `jeff`, and `tars` should not mean:

- one prompt containing several personalities
- one memory store holding blended character preferences
- one resolver trying to emulate several operator identities

It should mean:

- one self model per instance
- one memory/preference state per instance
- explicit inter-agent collaboration between instances

Consequence:

- if this boundary stays implicit, future team features may create identity leakage and muddled behavior contracts

## 4.7 Gap: no inspection surface for the self model

co has inspectable files for maintainers, but not a real first-class view of:

- what co currently believes its working style is
- which style modifiers are active in this session
- which preference artifacts are in force
- which constraints outrank others

Consequence:

- maintainers can inspect the ingredients, but not the resolved self model
- users cannot easily understand why co answered in a certain way

---

# 5. What Good Looks Like

A high-quality self model for co should have these properties:

- **explicit**: core behavior dimensions are represented directly, not only described in prose
- **layered**: identity, policy, task-mode adaptation, and learned user-style preferences are separate layers with defined precedence
- **bounded**: no style layer can override safety, approval, truthfulness, or uncertainty discipline
- **situational**: the system can adapt by task, risk, and user state without becoming erratic
- **inspectable**: maintainers and eventually users can see the resolved working-style state
- **repairable**: bad behavior can be corrected by changing a small explicit layer, not rewriting the whole soul
- **instance-scoped**: one deployed `co` instance has one self model; cross-character collaboration is a higher-level composition concern

---

# 6. Detailed Solution

## 6.1 Reframe the system from "personality prompt" to "self-model stack"

co should keep soul files, but redefine their role.

The unit of this design should be:

- one role
- one instance
- one self model

Not:

- many roles blended into one runtime identity

Recommended stack:

1. **Identity layer**
   Purpose: stable voice, posture, relationship feel
   Source: `souls/{role}/seed.md`, optional examples

   Note: the **character base sublayer** already exists. `souls/{role}/memories/*.md` entries tagged with the role name and `"character"` are decay-protected, planted entries carrying scenes, speech patterns, and behavioral observations from source material. These are loaded on demand via `search_canon()` in `co_cli/tools/memory/_canon_recall.py`. P1 work builds on this existing mechanism.

2. **Policy layer**
   Purpose: safety, approval, truthfulness, tool-use, workflow constraints
   Source: existing `co_cli/context/rules/01–05_*.md`

3. **Working-style schema**
   Purpose: explicit behavior dimensions with stable meaning
   Source: new structured config per role, loaded for the active instance only

4. **Situational adaptation layer**
   Purpose: task/risk/user-state modifiers
   Source: runtime resolver based on current context

5. **Learned style preference layer**
   Purpose: user-specific communication preferences
   Source: typed preference artifacts with scope/strength fields, not free-form recall only

6. **Resolved style contract**
   Purpose: the compact final behavior state injected per turn
   Source: generated from the layers above

This preserves the current prompt assets while giving them a clearer contract.

Implementation direction:

- keep `personality` in `Settings` (`co_cli/config/core.py`) as the active role selector
- add a structured role asset beside `seed.md` and `critique.md` under `souls/{role}/`
- compute resolved style per request in `co_cli/agent/core.py` or a new adjacent module
- do not let the runtime load multiple role style schemas for one agent session

## 6.2 Add an explicit working-style schema

Introduce a small structured representation for each role, for example:

```yaml
role: finch
defaults:
  directness: medium_high
  warmth: medium
  challenge: medium_high
  initiative: medium
  verbosity: low
  uncertainty_style: explicit
  planning_style: structured
  approval_posture: conservative
bounds:
  safety_overrides_style: true
  approval_policy_overrides_initiative: true
  truthfulness_overrides_reassurance: true
```

This should not replace prose entirely. It should anchor prose.

Why this helps:

- maintainers can change one dimension without rewriting a soul
- review diffs become legible
- runtime resolution becomes more deterministic

Implementation detail:

- add `souls/{role}/style.yaml` under `co_cli/personality/prompts/souls/`
- add `load_soul_style(role)` in `co_cli/personality/prompts/loader.py` (alongside existing `load_soul_seed`, `load_soul_mindsets`, `load_soul_critique`)
- validate the schema at load time in `co_cli/personality/prompts/validator.py`
- fail closed on malformed files rather than silently drifting
- expose the parsed schema through `Settings` or a dedicated runtime cache in `co_cli/config/`

## 6.3 Add a runtime style resolver

Before each model call, co should derive a compact resolved working-style state from:

- active task type
- risk level
- whether the turn is directive vs inquiry
- user preference records
- project instructions
- any relevant recent corrections

Illustrative output:

```yaml
resolved_style:
  response_mode: brief_structured
  directness: high
  warmth: low_medium
  challenge: high
  initiative: medium
  uncertainty_style: explicit
  explanation_depth: low
  approval_posture: conservative
  rationale: ["coding task", "user prefers brevity", "action may mutate files"]
```

Only a compact summary should be injected into the model context, not the entire derivation trace.

This lets co adapt intentionally instead of relying on whichever prompt fragments dominate locally.

Implementation direction:

- add `ResolvedStyle` and `StyleRationale` dataclasses or Pydantic models
- add `resolve_working_style(ctx, task_context)` in a new module `co_cli/personality/_style_resolver.py`
- call it from a new `@agent.instructions` function in `co_cli/agent/core.py`, before or alongside `inject_personality_critique`
- keep the rendered section short, e.g. `## Working Style`
- store the last resolved style in session/runtime state for inspection commands and trace output

## 6.4 Replace flat preference artifacts as the main style mechanism with typed preference records

The current `ArtifactKindEnum.PREFERENCE` kind should be extended, not replaced.

Add typed fields to preference artifacts:

- `type`: `communication.preference`, `feedback.preference`, `planning.preference`, `challenge.preference`, `format.preference`
- `scope`: global, project, relationship, task-type
- `strength`: hard, strong, soft
- `supersedes` / `superseded_by`: artifact id references
- `do_not_override`: optional bounded flag

Example:

```yaml
kind: preference
type: communication.preference
scope: global
strength: strong
value:
  prefers_brevity: true
  dislikes_praise_preamble: true
source:
  kind: user_correction
  timestamp: 2026-05-01
status: active
```

This turns user style learning into an explicit control surface rather than incidental recall.

Implementation direction:

- extend `KnowledgeArtifact` in `co_cli/memory/artifact.py` with the new fields (optional, defaulting to unset)
- update `memory_create()` in `co_cli/tools/memory/write.py` to accept and store typed preference fields
- update memory save/consolidation flows to emit structured preference records when the signal is clearly a stable style preference
- keep existing flat `PREFERENCE` artifacts as a backward-compatible fallback during migration

## 6.5 Separate "voice" from "behavior"

co should preserve identity, but maintain a clear split:

- **voice**: wording flavor, rhythm, relationship feel
- **behavior**: how much it challenges, explains, asks, warns, and acts

Why this matters:

- users often want the same usefulness with different tone
- maintainers should be able to tune task behavior without destroying identity
- different models may need different voice compensation while keeping the same behavior contract

Operationally:

- soul examples and seed remain the main voice surface (no change needed)
- working-style schema and resolver become the main behavior surface

Implementation detail:

- keep `seed.md`, `memories/*.md`, and mindset files prose-first
- keep style dimensions out of examples where possible
- use the schema/resolver to control behavior that should be reviewable and testable

## 6.6 Turn critique into a structured evaluation lens

Keep `critique.md` injection (currently appended in `build_agent()` as `## Review lens`), but pair it with explicit behavior metrics or categories.

Suggested categories:

- verbosity drift
- sycophancy drift
- overreach / extra work
- under-action / needless hesitation
- uncertainty masking
- approval-boundary looseness
- poor adaptation to user brevity preference

Use these categories in:

- prompt tuning review
- delivery audits for behavior changes
- optional post-turn sampling or evals

This creates a real improvement loop rather than just an always-on prose reminder.

Implementation direction:

- add a small taxonomy file under `co_cli/personality/prompts/souls/` or `co_cli/context/`
- use it in eval prompts, delivery audits, and prompt-review workflows
- avoid automatic self-rewriting of prompts; keep human-reviewed edits as the control point

## 6.7 Add a self-model inspection surface

co should expose a read-only way to inspect its resolved self model.

MVP options:

- `/style` prints active working-style dimensions and why they were chosen
- `/why-style` explains which preference or policy layers affected the current turn
- debug trace includes resolved style metadata

This is important for trust. Users and maintainers should be able to answer:

- why was co so direct here?
- why did it refuse to act?
- why did it ask a follow-up instead of proceeding?

Implementation direction:

- add a slash command in `co_cli/_commands.py`
- return the active role, resolved dimensions, and top influencing preference/policy inputs
- optionally print whether the output came from defaults, project instructions, or learned preferences

## 6.8 Keep the model small

The solution should stay deliberately narrow.

Do not build:

- full autonomous prompt self-editing
- free-form self-rewriting instructions stored in memory
- a heavy personality engine with dozens of dimensions
- graph-heavy behavioral state without strong evidence

The right MVP is:

- 6-10 stable dimensions
- typed preference records
- one resolver
- one inspection surface

That is enough to materially improve quality without creating a maintenance trap.

---

# 7. Proposed Architecture Changes

## 7.1 New data/assets

- `co_cli/personality/prompts/souls/{role}/style.yaml`
  Defines role default dimensions and hard bounds
- Extended `KnowledgeArtifact` schema in `co_cli/memory/artifact.py` with typed preference fields
- Optional resolver output object in runtime state

Instance rule:

- one running agent session loads exactly one `style.yaml`
- future multi-character teamwork should orchestrate several agents, each with its own `style.yaml`

## 7.2 New runtime component

- `resolve_working_style(ctx, task_context) -> ResolvedStyle`

Location: `co_cli/personality/_style_resolver.py`

Inputs:

- role defaults (from `style.yaml`)
- policy bounds (from rules)
- task type
- risk hints
- project instructions
- user style preferences (typed preference artifacts)

Outputs:

- compact resolved dimensions
- optional rationale list for inspection/debugging

Likely code touchpoints:

- `co_cli/agent/core.py` — call resolver and add `@agent.instructions` callback
- `co_cli/personality/prompts/loader.py` — add `load_soul_style(role)` alongside existing loaders
- `co_cli/personality/prompts/validator.py` — schema validation for `style.yaml`
- `co_cli/config/core.py` — expose parsed style schema or resolver cache
- `co_cli/memory/artifact.py` — extend `KnowledgeArtifact` with preference type/scope/strength fields
- `co_cli/tools/memory/write.py` — accept and store typed preference fields in `memory_create()`
- `co_cli/_commands.py` — add `/style` inspection command

## 7.3 Prompt contract change

Current:

- seed + mindsets (all 6 joined) + rules + toolset guidance + critique all become static prompt text
- no per-turn style injection
- user preferences recalled on demand via `memory_search()` with no formal injection contract

Recommended:

- seed/examples remain static prompt text
- rules remain static prompt text
- all six mindsets remain static; optionally suppress low-relevance ones via resolver
- resolved style becomes a short explicit per-turn section (`## Working Style`)
- raw preference artifacts are not injected directly unless needed

This reduces prompt sprawl and makes the style state more legible.

---

# 8. Phased Plan

## P1: make the self model explicit for one instance

Design goal:

- preserve the current one-instance-one-character model
- make that character's working style explicit, inspectable, and bounded
- improve maintainability without destabilizing current behavior

Scope:

- add `style.yaml` per role under `co_cli/personality/prompts/souls/{role}/`
- add `load_soul_style(role)` to `co_cli/personality/prompts/loader.py`
- add schema validation in `co_cli/personality/prompts/validator.py`
- add `ResolvedStyle` model and `resolve_working_style()` in `co_cli/personality/_style_resolver.py`
- inject a compact `## Working Style` section per turn via `@agent.instructions` in `co_cli/agent/core.py`
- add `/style` read-only inspection command in `co_cli/_commands.py`
- define precedence between seed, rules, style schema, project instructions, and learned preferences

Expected outcome:

- current behavior becomes easier to reason about
- future personality edits become smaller and safer
- one running `co` instance has a first-class self model rather than only a prompt bundle

## P2: make learned style preferences structured and composable

Design goal:

- turn user-specific style learning into a typed, scoped, updateable state layer
- support cleaner adaptation while preserving one-instance-one-character boundaries
- prepare for future multi-instance teamwork without blending identities

Scope:

- extend `KnowledgeArtifact` in `co_cli/memory/artifact.py` with typed preference fields (type, scope, strength, supersedes)
- update `memory_create()` and `memory_modify()` in `co_cli/tools/memory/write.py`
- update memory extraction/consolidation to recognize durable style preferences
- add conflict resolution and precedence logic across global/project/task scopes
- add behavior-quality evals and critique categories
- define how collaborative multi-agent flows preserve distinct self models per instance

Expected outcome:

- style adaptation becomes more precise and less prompt-fragile
- user corrections become easier to preserve and supersede
- `finch`, `jeff`, and `tars` can later collaborate as distinct `co` instances with clean identity boundaries

---

# 9. Risks and Tradeoffs

## Risk: over-structuring behavior

If the schema is too rigid, co may feel mechanical.

Mitigation:

- keep voice in prose (seed.md, memories/)
- structure only the dimensions that materially affect trust and usefulness

## Risk: duplicate control surfaces

If souls, rules, mindsets, preferences, and resolver all compete, the system gets harder to reason about.

Mitigation:

- assign each layer one clear responsibility
- document precedence explicitly

## Risk: prompt growth

A naive implementation would add more prompt text instead of clarifying it.

Mitigation:

- inject only resolved style summary per turn
- avoid dumping raw preference lists into every turn
- optionally suppress irrelevant mindsets when task type is clear

## Risk: false precision

Labels like `high` or `medium` can look rigorous without actually improving outcomes.

Mitigation:

- tie dimensions to observable behavior
- validate with evals and review cases

---

# 10. Bottom Line

co's current personality system is already thoughtful and better than generic prompt styling. The refactoring since March 2026 has improved maintainability: personality lives in its own package (`co_cli/personality/`), assembly is centralized (`co_cli/context/assembly.py`), and rules, mindsets, critique, and canon are clearly separated.

Its main remaining limitation is that it is still a **well-authored prompt stack**, not yet a **first-class self model**.

The frontier gap is not lack of charm. It is lack of:

- explicit behavior dimensions (style.yaml per role)
- structured style preferences (typed, scoped, with precedence)
- resolved per-turn working-style state (resolver injecting `## Working Style`)
- inspection and quality loops (`/style` command, critique categories)

The right move is not to make co "more personality-driven." The right move is to make co's working style **more explicit, more bounded, more inspectable, and easier to improve without destabilizing trust**.
