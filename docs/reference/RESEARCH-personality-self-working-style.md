# RESEARCH: self-model and working-style quality
_Date: 2026-03-08_

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
- **Current co implementation:** `co_cli/agent.py`, `co_cli/_commands.py`, `co_cli/_history.py`, `co_cli/tools/personality.py`
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

The current stack is strong in three ways:

- **clear static identity anchor**: soul seed, examples, mindsets, and rule files are assembled into a stable base prompt
- **runtime continuity**: per-turn injection adds project instructions, learned context, and critique overlays
- **maintainer legibility**: behavior is defined in files, not hidden in deeply tangled Python branches

That is better than many systems that just stuff "be helpful, concise, warm" into one prompt blob.

But the current architecture is still primarily a **prompt-composition system**, not yet a robust self-model system.

Today co's self/personality behavior is encoded mostly as:

- prose in `souls/{role}/seed.md`
- prose in `mindsets/{role}/*.md`
- prose in `rules/*.md`
- prose in `souls/{role}/critique.md`
- runtime injection of `personality-context` memories

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

These dimensions are present in prose, but not represented as explicit fields or policies.

Consequence:

- behavior tuning is indirect
- changes are harder to review
- regressions appear as "prompt drift" rather than a visible contract change

## 4.2 Gap: context adaptation is file-driven, not state-driven

Mindsets give co task-shape adaptation, which is good, but the adaptation is still largely static.

The system does not yet have a formal runtime concept like:

- `task_mode=debugging`
- `user_stress=high`
- `risk_level=elevated`
- `decision_irreversibility=high`
- `response_contract=brief_direct`

Consequence:

- style selection is less precise than it could be
- multiple competing prompt files may be active without a strong resolution model
- the system has limited ability to deliberately shift style when stakes change

## 4.3 Gap: personality memories are a weak control surface

`personality-context` memories help with continuity, but they are still a rough mechanism.

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

- identity and voice
- safety and approval policy
- task workflow rules
- learned user-facing style preferences

The docs say these are conceptually distinct, but the runtime contract is still mostly "all of this becomes prompt text."

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
- which personality memories are in force
- which constraints outrank others

Consequence:

- maintainers can inspect the ingredients, but not the resolved self model
- users cannot easily understand why co answered in a certain way

---

# 5. What Good Looks Like

A high-quality self model for co should have these properties:

- **explicit**: core behavior dimensions are represented directly, not only described in prose
- **layered**: identity, policy, task-mode adaptation, and learned user-style preferences are separate layers
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

   Note: `load_character_memories()` already implements a **character base sublayer** within this layer. It loads `.co-cli/memory/*.md` entries tagged with both the role name and `"character"` — decay-protected, planted entries carrying scenes, speech patterns, and behavioral observations from source material. These are assembled into the static soul block at agent creation alongside `seed.md` and mindsets. P1 work does not need to build this from scratch — it builds on this existing mechanism.

2. **Policy layer**
   Purpose: safety, approval, truthfulness, tool-use, workflow constraints
   Source: existing `rules/*.md`

3. **Working-style schema**
   Purpose: explicit behavior dimensions with stable meaning
   Source: new structured config per role, loaded for the active instance only

4. **Situational adaptation layer**
   Purpose: task/risk/user-state modifiers
   Source: runtime resolver based on current context

5. **Learned style preference layer**
   Purpose: user-specific communication preferences
   Source: typed memories/preferences, not free-form recall only

6. **Resolved style contract**
   Purpose: the compact final behavior state injected per turn
   Source: generated from the layers above

This preserves the current prompt assets while giving them a clearer contract.

Implementation direction:

- keep `personality` in `CoConfig` as the active role selector for the instance
- add a structured role asset beside `seed.md` and `critique.md`
- compute resolved style per request in `agent.py` instruction injection or adjacent helper code
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

- add `load_soul_style(role)` in `co_cli/prompts/personalities/_composer.py`
- validate the schema at load time
- fail closed on malformed files rather than silently drifting
- expose the parsed schema through `CoConfig` or a dedicated runtime cache

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
- add `resolve_working_style(ctx, task_context)` in a new module such as `co_cli/_style_resolver.py`
- call it from a new `@agent.instructions` function before `inject_personality_critique`
- keep the rendered section short, for example `## Working Style`
- store the last resolved style in session/runtime state for inspection commands and trace output

## 6.4 Replace `personality-context` as the main style mechanism with typed preference records

The current tag-based memory approach should be demoted from primary mechanism to compatibility layer.

Add typed preference records such as:

- `communication.preference`
- `feedback.preference`
- `planning.preference`
- `challenge.preference`
- `format.preference`

Each record should carry:

- scope: global, project, relationship, task-type
- strength: hard, strong, soft
- recency
- source
- supersedes / superseded_by
- do_not_override: optional bounded flag

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
  timestamp: 2026-03-08
status: active
```

This turns user style learning into an explicit control surface rather than incidental recall.

Implementation direction:

- extend the existing memory schema rather than inventing a second storage system
- either add a new `kind: preference` or add a typed subtype under `kind: memory`
- update memory save/consolidation flows to emit structured preference records when the signal is clearly a stable style preference
- keep existing `personality-context` tag support as a backward-compatible fallback during migration

## 6.5 Separate "voice" from "behavior"

co should preserve identity, but maintain a clear split:

- **voice**: wording flavor, rhythm, relationship feel
- **behavior**: how much it challenges, explains, asks, warns, and acts

Why this matters:

- users often want the same usefulness with different tone
- maintainers should be able to tune task behavior without destroying identity
- different models may need different voice compensation while keeping the same behavior contract

Operationally:

- soul examples and seed remain the main voice surface
- working-style schema and resolver become the main behavior surface

Implementation detail:

- keep `seed.md`, `examples.md`, and mindset files prose-first
- keep style dimensions out of examples where possible
- use the schema/resolver to control behavior that should be reviewable and testable

## 6.6 Turn critique into a structured evaluation lens

Keep `critique.md`, but pair it with explicit behavior metrics or categories.

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

- add a small taxonomy file under the personality assets, or document the categories centrally
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

- `co_cli/prompts/personalities/souls/{role}/style.yaml`
  Defines role default dimensions and hard bounds
- typed preference records in the existing knowledge/memory system
- optional resolver output object in runtime state

Instance rule:

- one running agent session loads exactly one `style.yaml`
- future multi-character teamwork should orchestrate several agents, each with its own `style.yaml`

## 7.2 New runtime component

- `resolve_working_style(ctx, task_context) -> ResolvedStyle`

Inputs:

- role defaults
- policy bounds
- task type
- risk hints
- project instructions
- user style preferences

Outputs:

- compact resolved dimensions
- optional rationale list for inspection/debugging

Likely code touchpoints:

- `co_cli/agent.py`
- `co_cli/prompts/personalities/_composer.py`
- `co_cli/config.py` or deps construction
- `co_cli/tools/personality.py` (internal helper only — `_load_personality_memories()`; not a registered agent tool)
- `co_cli/_history.py`
- `co_cli/_commands.py`
- memory lifecycle code for typed preference extraction and update

## 7.3 Prompt contract change

Current:

- seed + rules + mindsets + memories + critique all become prompt text

Recommended:

- seed/examples remain prompt text
- rules remain prompt text
- resolved style becomes a short explicit section
- raw preference memories are not injected directly unless needed

This reduces prompt sprawl and makes the style state more legible.

---

# 8. Phased Plan

## P1: make the self model explicit for one instance

Design goal:

- preserve the current one-instance-one-character model
- make that character's working style explicit, inspectable, and bounded
- improve maintainability without destabilizing current behavior

Scope:

- add `style.yaml` per role
- add `load_soul_style(role)`
- add `ResolvedStyle` model and resolver
- inject a compact `## Working Style` section per turn
- add a read-only inspection command
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

- add typed preference records and migration path from `personality-context`
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

- keep voice in prose
- structure only the dimensions that materially affect trust and usefulness

## Risk: duplicate control surfaces

If souls, rules, mindsets, preferences, and resolver all compete, the system gets harder to reason about.

Mitigation:

- assign each layer one clear responsibility
- document precedence explicitly

## Risk: prompt growth

A naive implementation would add more prompt text instead of clarifying it.

Mitigation:

- inject only resolved style summary
- avoid dumping raw preference lists into every turn

## Risk: false precision

Labels like `high` or `medium` can look rigorous without actually improving outcomes.

Mitigation:

- tie dimensions to observable behavior
- validate with evals and review cases

---

# 10. Bottom Line

co's current personality system is already thoughtful and better than generic prompt styling.

Its main limitation is that it is still a **well-authored prompt stack**, not yet a **first-class self model**.

The frontier gap is not lack of charm. It is lack of:

- explicit behavior dimensions
- structured style preferences
- resolved per-turn working-style state
- inspection and quality loops

The right move is not to make co "more personality-driven." The right move is to make co's working style **more explicit, more bounded, more inspectable, and easier to improve without destabilizing trust**.
