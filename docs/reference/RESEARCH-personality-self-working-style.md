# RESEARCH: self-model and working-style quality
_Date: 2026-05-27 (co v0.8.258)_

Handoff-quality design review for a TL and implementation team — gap framing, target
architecture, and phased goals. Not a TODO breakdown.

The question is not "does co have a personality?" It clearly does. The question is whether co
has a high-quality **self model**: an explicit, durable, inspectable definition of how it should
behave across tasks, contexts, and time.

Thesis:

- personality is a **working-style layer**, subordinate to trust, truthfulness, approvals, and task completion
- working style should be explicit enough to tune and inspect
- one `co` deployment is **one character instance**; future multi-character teamwork composes
  multiple instances, never collapses several characters into one prompt

---

# 1. Current co Shape (v0.8.258)

The base abstraction is already right: one instance, one selected role, one working relationship.
A future team setting is separate `finch` / `jeff` / `tars` instances, each with its own self
model and memory — teamwork between instances, not inside one blended prompt.

## 1.1 Architecture

**Roles** — `finch`, `jeff`, `tars`, auto-discovered from `souls/`. Selected via
`Settings.personality` (`co_cli/config/core.py`, default `"tars"`, env `CO_PERSONALITY`).

**Soul assets** — `co_cli/personality/prompts/souls/{role}/`:
- `seed.md` — identity anchor (required)
- `mindsets/*.md` — six task-shape files (`technical`, `exploration`, `debugging`, `teaching`,
  `emotional`, `memory`); all required, validated non-blocking in `prompts/validator.py`
- `critique.md` — review lens (optional)
- `canon/*.md` — character base: decay-protected scenes, speech patterns, behavioral observations

**Loaders** — `co_cli/personality/prompts/loader.py`: `load_soul_seed`, `load_soul_mindsets`,
`load_soul_critique`. Called at agent construction, not per-turn.

**Static assembly** — `co_cli/context/assembly.py` → `build_static_instructions(config)`,
assembled once per session in strict order:
1. Soul seed
2. Mindsets (all six joined into one block)
3. Behavioral rules — numbered `01_identity` … `07_memory_protocol` (identity, safety, reasoning,
   tool_protocol, workflow, skill_protocol, memory_protocol)

Canon and critique are **not** in this assembly.

**Critique injection** — `co_cli/agent/orchestrator.py`: a static instruction provider loads
`critique.md` and appends it as a `## Review lens` section after operational guidance. Skipped if
absent.

**Per-turn instructions** — only structural safety guardrails and ephemeral date/time grounding.
**No per-turn style injection** exists; all personality content is in the static (cached) block.

**Canon** — indexed at bootstrap under `source='canon'` into the shared FTS5 index (BM25),
alongside user memory. Recalled for personality auto-injection only; **never returned by any
model-callable tool**. (There is no longer a bespoke `canon_recall.py` / `search_canon()` /
token-overlap scorer — removed when canon merged into FTS5.)

**User preferences** — `co_cli/memory/item.py`: `MemoryItem` with `MemoryKindEnum`
(`USER`, `RULE`, `ARTICLE`, `NOTE`, `CANON`). Flat, domain-agnostic schema. User communication and
feedback style live as `USER`-kind items. No typed subtypes, scope, strength, or supersedes fields.

## 1.2 Strengths

- **Clear static identity anchor** — seed + mindsets + rules assembled with a defined order contract
- **Layered asset separation** — identity, policy, task guidance, character memory in distinct
  files with distinct load paths
- **Maintainer legibility** — behavior is plain markdown, not Python branches

This beats systems that stuff "be helpful, concise, warm" into one prompt blob. But it remains a
**prompt-composition system**, not yet a self-model system: the behavior contract is distributed
across prose rather than represented as explicit dimensions with stable semantics.

> None of the proposals below have been built. There is no `style.yaml`, `ResolvedStyle`,
> `resolve_working_style()`, typed preference schema, or `/style` command in the codebase.

## 1.3 Peer / frontier grounding

Gaps below are cross-checked against the repo's `RESEARCH-personality-peer-survey.md` (opencode,
codex, ElizaOS, SillyTavern, Soul.md + the Anthropic functional-emotions paper). Honest verdict:

- **Directly peer-backed:** §2.5 (precedence / conflict resolution). The peer survey's §7c.6 says co
  "should formalize priority and conflict resolution rather than relying on implicit ordering."
- **Directional only:** §2.2. SillyTavern's Character's Note (runtime depth injection), Soul.md
  interaction-mode transitions, and ElizaOS per-medium `style` are more dynamic than co's static
  load — but none has `risk_level`/`task_mode` runtime state, and the survey rates co's
  mindset-per-task as the strongest role layer of all systems surveyed. co is ahead, not behind.
- **Frontier aspiration, not peer-demonstrated:** §2.1, §2.4, §2.6, §2.7. No surveyed peer has
  tunable behavior dimensions, a self-governance loop, multi-instance identity isolation, or a
  resolved-self-model inspection surface.
- **Unsourced:** §2.3 (typed preferences). The "structured state beats prose / Letta–Mem0 typed
  blocks" lesson has no independent memory peer survey in the repo backing it.

**Tension to hold honestly:** the survey's strongest empirical lesson is *context-over-command*
("fewer rules, clearer anchors") and a warning that co's assembly may already be **over-specified**
(§7c.6). The central proposal here — add a `style.yaml` schema + resolver — adds structure, which
pushes against that evidence. The survey's own gap list is also largely *different* (emotion-aware
safety monitoring, calm-as-invariant, personality-aware compaction, sycophancy↔harshness
calibration, user-emotion modeling); it overlaps this doc on exactly one item (§2.5). Treat
structuring as a hypothesis to validate with evals, not a settled win.

---

# 2. Gap Analysis

## 2.1 Self model is mostly implicit

co has a soul and rules but no explicit schema for its behavior dimensions. Dimensions present
only in prose today: directness vs expansiveness, challenge vs deference, warmth vs neutrality,
action bias vs deliberation, confidence vs uncertainty surfacing, initiative vs waiting.

Consequence: tuning is indirect (rewrite prose, not a value); diffs on `seed.md` don't reveal
which dimension moved; regressions look like "prompt drift" rather than a visible contract change.

Caveat: no surveyed peer has tunable dimensions either (codex has named *stances*, not a schema),
and the peer survey warns against over-specification. This is a frontier aspiration, not a
peer-demonstrated win.

## 2.2 Context adaptation is file-driven, not state-driven

All six mindsets are loaded together at agent construction and joined into one static `## Mindsets`
block (`load_soul_mindsets`). Each is a clearly labeled subsection (`## Debugging`, `## Technical`,
…), so the model self-selects which applies — there is no system-level gating, no runtime concept
like `task_mode=debugging`, `risk_level=elevated`, or `response_contract=brief_direct`.

The selection is not even nudged at the prompt level. The mindsets block ships with no preamble:
`load_soul_mindsets` emits `## Mindsets` followed by the six joined files and nothing else. No rule
instructs a classify-then-focus step — `03_reasoning.md` covers verification and when-to-ask, never
task-shape selection — and neither seed nor critique references mindsets. The only structuring
signal is the heading labels themselves. So co relies on emergent attention to labeled sections: the
model *may* weight the relevant heading by relevance, but nothing prompts it to, and on a
non-reasoning model no explicit selection step need happen at all.

Consequence: mindset selection is the model's inference, not a system decision. No mechanism exists
to deliberately activate the right stance when stakes rise, suppress the other five, or verify the
intended mindset was applied.

Two interventions, cheapest first:
- **Prompt-level selection nudge** — a one-line preamble on the block ("identify which task shape
  this turn is and lead with that mindset; treat the others as background") converts flat awareness
  into an instructed classify-then-focus step. Cheap, but adds prompt weight the peer survey warns
  about and stays unverifiable.
- **Per-turn injection of only the relevant heading(s)** — the real fix, but requires the runtime
  resolver (§4.2) to do the picking.

(SillyTavern/Soul.md/ElizaOS have lighter dynamic mechanisms, but co's static load is rated the most
sophisticated role layer — so this is direction-of-travel, not lag.)

## 2.3 Preference state is a weak control surface

User style preferences are flat `USER`-kind `MemoryItem`s — markdown with a description, no
structured fields. They work for "prefers blunt feedback" / "dislikes long preambles" but not for:
this applies only in coding tasks; this is soft not mandatory; this correction supersedes older
ones; this must never override safety.

Consequence: preferences can be remembered without being applied with nuance; prompt-level style
and memory-level preferences can conflict with no arbitration rule. (No repo peer survey backs the
typed-preference direction — treat as unsourced until validated.)

## 2.4 Critique exists, but self-governance is thin

`critique.md` gives an always-on review lens — prose, not a self-governance loop. Missing: explicit
style failure categories, lightweight self-checks tied to task/risk level, persistent signals about
where behavior repeatedly misses. co can be asked to critique itself but accumulates no reliable
model of its behavioral weaknesses.

## 2.5 No clean separation between identity, policy, and learned style

Identity/voice (seed), safety/approval policy (rules 02, 04), workflow rules (rule 05), and learned
user-facing style (USER-kind items, recalled dynamically with no formal injection contract) all
collapse into "this becomes prompt text" with no explicit precedence.

Consequence: maintainers can use identity text to paper over policy gaps; style customization can
drift into operational rules; hard to reason about what may change dynamically. (This is the one gap
with direct peer-survey support — §7c.6 calls for formalized priority and conflict resolution.)

## 2.6 Instance vs multi-agent identity not separated strongly

The design is compatible with one-instance-one-character but doesn't state the boundary firmly.
Future `finch`/`jeff`/`tars` teamwork must not become one prompt with several personalities, one
memory store with blended preferences, or one resolver emulating several operators. If the boundary
stays implicit, team features risk identity leakage.

## 2.7 No inspection surface for the self model

Maintainers can inspect the ingredients (files) but not the resolved self model: what co currently
believes its working style is, which modifiers are active this session, which preferences are in
force, which constraints outrank others. Users can't easily understand why co answered as it did.

---

# 3. What Good Looks Like

- **explicit** — core behavior dimensions represented directly, not only in prose
- **layered** — identity, policy, task-mode adaptation, learned user-style as separate layers with defined precedence
- **bounded** — no style layer overrides safety, approval, truthfulness, or uncertainty discipline
- **situational** — adapts by task, risk, and user state without becoming erratic
- **inspectable** — maintainers (eventually users) can see the resolved working-style state
- **repairable** — bad behavior corrected by changing a small explicit layer, not rewriting the soul
- **instance-scoped** — one instance, one self model; cross-character collaboration is a higher composition concern

---

# 4. Target Architecture

Keep the soul files; redefine their role within a layered stack. One running session loads exactly
one role; multi-character teamwork orchestrates several agents, each with its own assets.

| Layer | Purpose | Source |
|-------|---------|--------|
| Identity | stable voice, posture, relationship feel | `souls/{role}/seed.md`, `canon/*.md` |
| Policy | safety, approval, truthfulness, tool-use, workflow | `co_cli/context/rules/01–07_*.md` |
| Working-style schema | explicit behavior dimensions with stable meaning | **new** per-role structured config |
| Situational adaptation | task / risk / user-state modifiers | **new** runtime resolver |
| Learned style preferences | user-specific communication preferences | typed memory items with scope/strength |
| Resolved style contract | compact final state injected per turn | generated from the layers above |

The identity layer's character base (canon) already exists — proposed work builds on it.

## 4.1 Working-style schema

A small structured representation per role anchors (does not replace) prose:

```yaml
role: finch
defaults: {directness: medium_high, warmth: medium, challenge: medium_high,
           initiative: medium, verbosity: low, uncertainty_style: explicit,
           planning_style: structured, approval_posture: conservative}
bounds:   {safety_overrides_style: true, approval_policy_overrides_initiative: true,
           truthfulness_overrides_reassurance: true}
```

Maintainers change one dimension without rewriting a soul; review diffs become legible; runtime
resolution becomes more deterministic. Add `souls/{role}/style.yaml`, `load_soul_style(role)` in
the loader, schema validation in `validator.py` (fail closed on malformed files).

## 4.2 Runtime style resolver

Before each model call, derive a compact resolved state from active task type, risk level,
directive-vs-inquiry, preference records, project instructions, and recent corrections:

```yaml
resolved_style: {response_mode: brief_structured, directness: high, warmth: low_medium,
                 challenge: high, initiative: medium, uncertainty_style: explicit,
                 approval_posture: conservative,
                 rationale: ["coding task", "user prefers brevity", "action may mutate files"]}
```

Only the compact summary is injected (`## Working Style`), not the derivation. Add `ResolvedStyle`
+ `resolve_working_style(ctx, task_context)` in `co_cli/personality/_style_resolver.py`; call from a
`@agent.instructions` provider in the orchestrator; store last resolved style for inspection/trace.

## 4.3 Typed style preferences

Extend `MemoryItem` (or a dedicated preference kind) with optional fields so user style learning
becomes an explicit control surface rather than incidental recall:

- `type`: communication / feedback / planning / challenge / format
- `scope`: global / project / relationship / task-type
- `strength`: hard / strong / soft
- `supersedes` / `superseded_by`: item references
- `do_not_override`: optional bounded flag

Update `memory_create` / `memory_modify` to accept these; emit structured preference records when a
signal is clearly a stable style preference.

## 4.4 Separate voice from behavior

- **voice** (seed, canon, mindsets) — wording flavor, rhythm, relationship feel; prose-first, unchanged
- **behavior** (schema + resolver) — how much it challenges, explains, asks, warns, acts; reviewable and testable

Lets users keep the same usefulness with different tone, and lets maintainers tune task behavior
without destroying identity (or compensate voice per model without touching the behavior contract).

## 4.5 Structured critique categories

Keep the `## Review lens` injection, but pair it with explicit behavior categories: verbosity
drift, sycophancy drift, overreach/extra work, under-action/needless hesitation, uncertainty
masking, approval-boundary looseness, poor adaptation to brevity preference. Use them in prompt
tuning review, delivery audits, and evals — a real improvement loop, not just a prose reminder.
Avoid automatic self-rewriting; keep human-reviewed edits as the control point.

## 4.6 Inspection surface

A read-only `/style` command prints active dimensions and why they were chosen (defaults vs project
instructions vs learned preferences); debug trace includes resolved-style metadata. Answers "why
was co so direct here?", "why did it refuse to act?", "why ask instead of proceeding?".

## 4.7 Keep it small

The right MVP is 6–10 stable dimensions, typed preference records, one resolver, one inspection
surface. Do **not** build autonomous prompt self-editing, free-form self-rewriting in memory, a
dozens-of-dimensions engine, or graph-heavy behavioral state. Each layer gets one clear
responsibility with documented precedence — otherwise souls, rules, mindsets, preferences, and
resolver compete and the system gets harder to reason about.

---

# 5. Phased Goals

## P1 — make the self model explicit for one instance

Preserve one-instance-one-character; make that character's working style explicit, inspectable, and
bounded without destabilizing current behavior.

- `style.yaml` per role + `load_soul_style` + schema validation
- `ResolvedStyle` and `resolve_working_style()` in `co_cli/personality/_style_resolver.py`
- compact `## Working Style` per-turn section via the orchestrator's instruction providers
- read-only `/style` command
- defined precedence between seed, rules, style schema, project instructions, learned preferences

Outcome: behavior becomes easier to reason about; personality edits become smaller and safer; a
running instance has a first-class self model, not just a prompt bundle.

## P2 — make learned style preferences structured and composable

Turn user-specific style learning into a typed, scoped, updateable layer; prepare for multi-instance
teamwork without blending identities.

- typed preference fields on the memory schema (type, scope, strength, supersedes)
- update `memory_create` / `memory_modify` and extraction/consolidation to recognize durable preferences
- conflict resolution / precedence across global/project/task scopes
- behavior-quality evals and critique categories
- define how collaborative flows preserve distinct self models per instance

Outcome: style adaptation becomes precise and less prompt-fragile; corrections easier to preserve
and supersede; `finch`/`jeff`/`tars` can later collaborate as distinct instances with clean identity
boundaries.

---

# 6. Risks

- **Over-structuring** — too-rigid schema feels mechanical, and the peer survey's context-over-command
  evidence warns the assembly may already be over-specified. Keep voice in prose; structure only
  dimensions that affect trust and usefulness; validate the schema's value with evals before committing.
- **Duplicate control surfaces** — assign each layer one responsibility; document precedence.
- **Prompt growth** — inject only the resolved summary per turn; never dump raw preference lists;
  optionally suppress irrelevant mindsets when task type is clear.
- **False precision** — `high`/`medium` labels can look rigorous without improving outcomes. Tie
  dimensions to observable behavior; validate with evals.

---

# 7. Bottom Line

co's personality system is thoughtful and better than generic prompt styling: personality lives in
its own package, assembly is centralized, and rules/mindsets/critique/canon are clearly separated.
Its remaining limitation is that it is a **well-authored prompt stack**, not yet a **first-class
self model**.

The gap is not charm. It is: explicit behavior dimensions (`style.yaml`), structured style
preferences (typed, scoped, precedence), resolved per-turn working-style state (resolver), and
inspection/quality loops (`/style`, critique categories). Of these, only precedence/conflict
resolution (§2.5) is directly peer-backed; the rest are frontier aspirations to validate, not
settled wins. The move is not "more personality-driven" — it is working style that is **more
explicit, more bounded, more inspectable, and easier to improve without destabilizing trust**.
