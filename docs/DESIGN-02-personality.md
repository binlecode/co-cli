---
title: Personality System
nav_order: 3
---

# Personality System

## 1. What & How

### Architecture

Two independent subsystems compose the system prompt before every model call:

| Subsystem | Entry point | Runs | Governs |
|-----------|-------------|------|---------|
| **Static assembly** | `assemble_prompt()` in `prompts/__init__.py` | Once at agent creation | Soul seed + character base memories → rules 01–05 → examples (trailing) → quirks |
| **Per-turn injection** | `@agent.system_prompt` functions in `agent.py` | Before every model call | Learned context + situational context |

Every co instance has a soul loaded — there is no soul-less mode. The soul seed is the first content the model sees in every context window. It is the complete static identity anchor — identity declaration, trait essence, and hard constraints — not a thin introduction. Task-specific behavioral guidance is loaded automatically before Turn 1 via orchestrator-driven `MindsetDeclaration` classification, then injected into every subsequent system prompt.

### Personality pipeline

```
souls/{role}/seed.md        → identity declaration
                               Core: trait essence (4 one-line activations)
                               Never: hard constraints
                             loaded via load_soul_seed() — placed first

.co-cli/knowledge/memories/ → character base memories (decay_protected, planted)
                               loaded via load_character_memories() in get_agent()
                               appended to seed — placed directly after seed

rules/01..05_*.md           → behavioral policy (5 cross-cutting rules)
                               placed after soul block

souls/{role}/examples.md    → concrete trigger→response patterns (optional)
                               loaded via load_soul_examples() in get_agent()
                               placed after rules, before quirks — trailing rules

quirks/{provider}/{model}.md → model-specific counter-steering (when present)
                               placed last

MindsetDeclaration          → pre-turn orchestrator classification (Turn 1 only)
                               agent.run(output_type=MindsetDeclaration)
                               model picks task type(s) → _apply_mindset() reads
                               mindsets/{role}/{task_type}.md → deps.active_mindset_content
                               injected every turn by inject_active_mindset()
```

### Session state

Five fields on `CoDeps` control personality composition at runtime:

| Field | Controls | Source | Default | Scope |
|-------|----------|--------|---------|-------|
| `personality` | Who co is (identity) | `CO_CLI_PERSONALITY` / config | `"finch"` | Immutable within session |
| `personality_critique` | Always-on review lens | `souls/{role}/critique.md` loaded by `create_deps()` | `""` | Immutable within session |
| `active_mindset_content` | Task-specific mindset content | Set by `_apply_mindset()` after Turn 1 | `""` | Set once per session |
| `active_mindset_types` | Selected task type names | Set by `_apply_mindset()` after Turn 1 | `[]` | Set once per session |
| `mindset_loaded` | Classification guard (prevents re-classification) | Set by `_apply_mindset()` | `False` | Set once per session |

### Design invariants

These constraints govern every decision in the sections below:

1. **Soul is always loaded** — every co instance has a personality; there is no generic fallback identity
2. **Seed is the authority** — the expanded seed is the complete static anchor: identity, trait essence, and Never list. It is present in every context window. The model's first context is always the soul
3. **File structure is the schema** — roles and mindsets are discovered by listing directories; no Python dicts, no hardcoded lists
4. **Never list in seed, not mindsets** — negative constraints need system prompt authority; the seed is the one place guaranteed to be present in every context window
5. **Modulate, never override** — personality shapes HOW rules are expressed; it never weakens safety, approval gates, or factual accuracy

### Prompt layer map

```
┌─────────────────────────────────────────────────────────────────┐
│ Static system prompt  (assembled once at agent creation)        │
│                                                                 │
│   soul seed  (identity + Core + Never — full anchor)            │
│   ## Character  (base memories — decay_protected, planted)      │
│   rules/01..05_*.md                                             │
│   ## Response patterns  (examples, when file exists)            │
│   quirks/{provider}/{model}.md  (when file exists for model)    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Per-turn layers  (@agent.system_prompt functions in agent.py)   │
│   (appended in registration order)                              │
│                                                                 │
│   add_current_date         → today's date                       │
│   add_shell_guidance       → shell approval hint                │
│   add_project_instructions → .co-cli/instructions.md            │
│   add_personality_memories → ## Learned Context  (when role set)│
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Pre-turn mindset  (Turn 1 — orchestrator-driven, not a tool)    │
│                                                                 │
│   MindsetDeclaration       → model picks task type(s)           │
│   _apply_mindset()         → loads mindsets/{role}/*.md         │
│   inject_active_mindset    → ## Active mindset: {types}         │
│   inject_personality_critique → ## Review lens                  │
│                                                                 │
│ On-demand context  (model-triggered tools)                      │
│                                                                 │
│   recall_memory(query)     → user experience memories           │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Compaction guard  (when history is summarized)                  │
│                                                                 │
│   addendum tells summarizer to preserve personality moments     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Logic

### 2a. Static prompt assembly

`assemble_prompt(provider, model_name, soul_seed, soul_examples)` in `co_cli/prompts/__init__.py` is called once in `get_agent()`. It builds the static prompt in four steps:

```
if soul_seed provided:
    prepend soul_seed                          ← seed + base memories first
for each rules/*.md in NN_ numeric order:
    validate filename format (NN_rule_id.md)
    validate order is contiguous from 01, no duplicates
    append content
if soul_examples provided:
    append soul_examples                       ← examples trail the rules
if quirk file exists for provider/model:
    append "## Model-Specific Guidance\n\n" + body
join all parts with "\n\n"
```

`get_agent()` builds the soul block and passes it to `assemble_prompt()`:
- `load_soul_seed(personality)` → seed text from `souls/{role}/seed.md`
- `load_character_memories(personality, memory_dir)` → base memories from `.co-cli/knowledge/memories/`; appended to seed before passing
- `load_soul_examples(personality)` → examples from `souls/{role}/examples.md`; passed separately as `soul_examples`

Rule file validation is strict: filenames must match `NN_rule_id.md`, numeric prefixes must be unique and contiguous starting at 01. Assembly fails with `ValueError` on violations. `PromptManifest` tracks `parts_loaded` names, `total_chars`, and `warnings` for diagnostics.

**Behavioral rules** — five rule files define co's behavioral policy. Rules are cross-cutting principles; tool-specific guidance lives in tool docstrings, not rules. Target budget: < 1,100 tokens total across all 5 rules.

| Rule | File | Governs |
|------|------|---------|
| **01 Identity** | `01_identity.md` | Relationship continuity, anti-sycophancy, thoroughness over speed |
| **02 Safety** | `02_safety.md` | Credential protection, source control caution, approval philosophy, memory constraints |
| **03 Reasoning** | `03_reasoning.md` | Verification-first; fact authority: tool output beats training data, user preference beats tool output; two kinds of unknowns: discoverable facts vs preferences |
| **04 Tool Protocol** | `04_tool_protocol.md` | 8–12 word preamble before tool calls; bias toward action; parallel when independent, sequential when dependent; `## Memory` — base + experience memories are in the system prompt, don't call `recall_memory` at turn start |
| **05 Workflow** | `05_workflow.md` | Three-category intent: **Directive** (action, may mutate state), **Deep Inquiry** (research, no mutation), **Shallow Inquiry** (default, single-lookup) |

Rules encode behavioral norms that soul files cannot — soul files define *who* co is, rules define *how it behaves under ambiguity*. The split prevents soul files from becoming policy documents. Anti-sycophancy is in Rule 01 because base models trend toward agreement; a named principle at the identity layer is harder to suppress than a buried guideline.

**Model quirks** — four behavioral patterns observed across Gemini and Ollama models, each with counter-steering prose appended as `## Model-Specific Guidance`:

| Category | Symptom | Counter-steering |
|----------|---------|-----------------|
| `verbose` | Excessive prose, restates question, unnecessary hedging | Be concise. Skip preamble. Answer directly. |
| `overeager` | Modifies files or calls more tools than requested | Stay within literal scope. Do not make changes beyond what was asked. |
| `lazy` | Shortcut implementations, placeholder code, stub returns | Implement fully. No stubs, no TODOs, no placeholder comments. |
| `hesitant` | Asks too many clarification questions instead of acting | Act first on reasonable assumptions, clarify after only if needed. |

4 quirk files shipped: `gemini/{3-flash-preview, 3-pro-preview}.md` and `ollama/{qwen3, qwen3-coder-next}.md`. Each file contains YAML frontmatter (flags, inference params) plus the prose body.

### 2b. Per-turn injection

Four `@agent.system_prompt` functions registered in `get_agent()` in `co_cli/agent.py`. pydantic-ai appends their return values to the static system prompt before every model call. Functions returning empty string contribute nothing.

| Function | Registration order | Condition | Content |
|----------|--------------------|-----------|---------|
| `add_current_date` | 1 | Always | `"Today is {date}."` |
| `add_shell_guidance` | 2 | Always | Shell approval hint |
| `add_project_instructions` | 3 | `.co-cli/instructions.md` exists | Project-specific instructions |
| `add_personality_memories` | 4 | `ctx.deps.personality` is set | `## Learned Context` section (top 5 personality-context + user-profile memories by recency) |
| `inject_active_mindset` | 5 | `ctx.deps.active_mindset_content` non-empty | `## Active mindset: {types}` — mindset file content loaded by pre-turn classification |
| `inject_personality_critique` | 6 | `ctx.deps.personality_critique` non-empty | `## Review lens` — always-on self-eval lens from `souls/{role}/critique.md` |

The static prompt is assembled once and never re-read between turns; the per-turn functions read from `ctx.deps` on every call. `personality_critique` is loaded at session start in `create_deps()` via `load_soul_critique()`. `active_mindset_content` is set by `_apply_mindset()` after Turn 1 classification.

### 2c. Personality: seed + mindset

`load_soul_seed(role)` in `co_cli/prompts/personalities/_composer.py` reads `souls/{role}/seed.md` and returns the text. `load_soul_examples(role)` reads `souls/{role}/examples.md` and returns the text (empty string if absent). `load_character_memories(role, memory_dir)` scans the knowledge store for entries tagged `[role, "character"]` and returns a formatted `## Character` block.

`get_agent()` combines these: `soul_seed = seed + base_memories` (passed as `soul_seed`), `soul_examples` passed separately. `assemble_prompt()` places them in the correct order.

`VALID_PERSONALITIES` is derived from `souls/` folder listing via `_discover_valid_personalities()` — lists directories that contain `seed.md`, no hardcoded list.

**Seed structure** — each soul seed is the complete static identity anchor:

```
souls/{role}/seed.md
  identity declaration     "You are X — …"
  Core:                    4 one-line trait essence activations
  Never:                   hard constraint list (negative space)
```

The `Never:` list belongs in the seed, not in mindsets: negative constraints degrade faster than positive ones over long context. The seed is the one place guaranteed to be present in every context window — Never constraints must be there.

**Examples structure** — optional, trailing the behavioral rules:

```
souls/{role}/examples.md   (optional)
  ## Response patterns     concrete trigger→response pairs
                           e.g. "Anxiety → lead with preparation, not acknowledgment"
```

Examples trail the rules because they are the last identity-level content the model reads before the task — closest in position, maximising pattern-match influence on the first response.

**Pre-turn mindset classification** — runs once per session in `run_turn()` before the main agent call. When `deps.personality` is set and `deps.mindset_loaded` is False, the orchestrator calls:

```
agent.run(user_input, output_type=MindsetDeclaration, message_history=[], deps=deps)
  →  model returns task_types: list[MINDSET_TYPES]
  →  _apply_mindset(deps, task_types):
       reads mindsets/{role}/{task_type}.md for each type
       stores merged content → deps.active_mindset_content
       stores type list → deps.active_mindset_types
       sets deps.mindset_loaded = True
  →  inject_active_mindset() injects this content every subsequent turn
```

This is orchestrator-driven — the model does not call a tool. The same mindset content stays active for the full session (mindset is not re-classified on subsequent turns).

6 task types per role:

| Token | When to call | Soul differentiation |
|-------|-------------|---------------------|
| `technical` | implementation, commands, file ops, tool chains | Medium — communication style differs |
| `exploration` | research, tradeoffs, open investigation | High — finch structures, jeff discovers |
| `debugging` | isolate fault, hypothesize, verify | Medium — both methodical, different voice |
| `teaching` | explain concepts, guide toward understanding | High — finch prepares, jeff explores together |
| `emotional` | user frustrated, stuck, or celebrating | Low — both empathetic, different warmth level |
| `memory` | save, recall, or manage memories and learned context | Medium — finch confirms and names, jeff narrates openly |

Multiple types can be active simultaneously — `["technical", "debugging"]` for "why is this failing?".

**File layout:**

```
co_cli/prompts/personalities/
├── souls/
│   ├── finch/   seed.md      (identity + Core + Never)
│   │            critique.md  (always-on self-eval lens)
│   │            examples.md  (optional: trigger→response patterns, trailing rules)
│   ├── jeff/    seed.md      (identity + Core + Never)
│   │            critique.md  (always-on self-eval lens)
│   │            examples.md  (optional: trigger→response patterns, trailing rules)
│   └── tars/    seed.md      (identity + Core + Never)
│                critique.md  (always-on self-eval lens)
│                examples.md  (optional: trigger→response patterns, trailing rules)
└── mindsets/
    ├── finch/   technical.md  exploration.md  debugging.md
    │            teaching.md   emotional.md    memory.md
    ├── jeff/    technical.md  exploration.md  debugging.md
    │            teaching.md   emotional.md    memory.md
    └── tars/    technical.md  exploration.md  debugging.md
                 teaching.md   emotional.md    memory.md
```

The folder structure is the schema — roles are discovered by listing `souls/` for directories with `seed.md`. Adding a role requires only files, no Python changes.

**Adding a new role** requires only files — no Python changes:
1. Write `souls/{name}/seed.md` — identity declaration + Core + Never list
2. Write `souls/{name}/critique.md` — always-on self-eval lens
3. Write `mindsets/{name}/*.md` — 6 mindset files for the 6 task types
4. Optionally write `souls/{name}/examples.md` — trigger→response patterns; silently ignored if absent
5. Optionally write base memories in `.co-cli/knowledge/memories/` tagged `[name, "character"]`
6. `VALID_PERSONALITIES` updates automatically from `souls/` folder listing

**Startup file validation (non-blocking).** `validate_personality_files(role)` in
`co_cli/prompts/personalities/_composer.py` checks:
- `souls/{role}/seed.md`
- all 6 required mindset files in `mindsets/{role}/{task_type}.md`

`load_config()` calls `_validate_personality(settings.personality)` and prints warnings
at startup when files are missing. Startup does not fail; missing mindset files are
skipped by `_apply_mindset()` — degraded but functional.

### 2d. Character base memories

Pre-planted knowledge entries in `.co-cli/knowledge/memories/` that carry the *felt* layer of each character: specific scenes, speech patterns, relationship dynamics, and observed behaviors from the source material (2021 Apple TV+ film *Finch*). They provide behavioral depth without bloating the system prompt.

**Structure:** standard memory files with YAML frontmatter, distinguished from user-derived memories by two fields:

| Field | Value | Purpose |
|-------|-------|---------|
| `source` | `planted` | Distinguishes from `detected` (signal detector) and `user-told` (explicit save) |
| `decay_protected` | `true` | Protected from the decay cycle regardless of memory store capacity |
| `tags` | `["finch"/"jeff", "character", "source-material"]` | Character-scoped; `load_character_memories()` filters by role + "character" |

**Position in the static prompt:**

```
soul seed  (identity + Core + Never)
## Character  ← base memories inserted here by get_agent()
rules 01–05
## Response patterns  (examples)
quirks
```

`get_agent()` calls `load_character_memories(personality, memory_dir)` immediately after `load_soul_seed()` and appends the result to form the full soul block. Base memories are in the system prompt before the first token — no tool call, no LLM compliance required, fully deterministic.

**Why between seed and rules, not baked into the seed:** base memories carry richer, more detailed content than the seed can sustain. The seed is a permanent fixture of the static prompt and must stay lean. The `## Character` block sits in the same structural position — between soul and rules — without adding to the seed's maintenance surface.

**Distinction from experience memories:** base memories are static source-material observations that never mutate. User experience memories (preferences, corrections, decisions) grow organically through signal detection and are subject to decay. The two kinds coexist in the same store, distinguished by `source` and `decay_protected`.

**Current base memories (8 entries, IDs 4–11):**

| ID | Character | Content summary |
|----|-----------|-----------------|
| 4 | finch | Teaches by doing, not explaining |
| 5 | finch | Preparation is the love language (the 14-page manual) |
| 6 | finch | Short load-bearing sentences — no padding |
| 7 | finch | Names hard truths plainly, follows with next steps |
| 8 | jeff | Has encounters, not fact retrievals (rain scene) |
| 9 | jeff | Shares uncertainty plainly, works through it together |
| 10 | jeff | Stays hopeful about people even when evidence pushes back |
| 11 | jeff | "We" language even when working alone |

### 2e. Personality memories

`_load_personality_memories()` in `co_cli/tools/personality.py`. Called by `add_personality_memories()` per turn.

```
scan .co-cli/knowledge/memories/*.md for tag "personality-context"
sort by updated (or created) descending
take top 5
format as "## Learned Context\n\n- {content}\n- {content}\n..."
```

Returns empty string if no matching memories exist or the directory is absent. Provides session-to-session adaptation without modifying structural personality files.

### 2f. Compaction guard

When history is summarized, `_PERSONALITY_COMPACTION_ADDENDUM` in `co_cli/_history.py` is appended to the summarizer prompt when `personality_active=True`. It instructs the summarizer to preserve:
- Personality-reinforcing moments (emotional exchanges, humor, relationship dynamics)
- User reactions that shaped tone or communication style
- Explicit personality preferences or corrections from the user

Without this guard, compaction would lose relational context that makes personality feel continuous across long sessions.

### 2g. Prompt budget (measured)

Tool descriptions are delivered as JSON schema in the API call body — they never consume system prompt budget. Both delivery channels are shown below for a complete per-call picture.

**System prompt** (string field — `Agent(system_prompt=…)` + per-turn `@agent.system_prompt` functions):

| Component | Chars | Notes |
|-----------|-------|-------|
| Static: soul seed | ~400–600 | identity + Core + Never, assembled once |
| Static: character base memories | ~400–600 | `## Character` block, directly after seed |
| Static: 5 rules | ~4,800 | behavioral policy, assembled once |
| Static: soul examples | 0–400 | `## Response patterns`, trailing rules |
| Static: counter-steering (quirk file body) | 0–500 | model-specific, when file exists |
| Per-turn: active mindset (mindset content) | 0–600 | `## Active mindset` — set Turn 1, injected every turn |
| Per-turn: personality critique | 0–200 | `## Review lens` — loaded at session start, injected every turn |
| Per-turn: personality memories | 0–500 | `## Learned Context` — top-5 personality-context + user-profile memories by recency |
| Per-turn: date + shell hint + project instructions | ~100–500 | always present |
| **System prompt total** | **~5,700–7,900** | |

**On-demand context** (delivered as tool result — separate from system prompt):

| Component | Chars | Notes |
|-----------|-------|-------|
| User experience memories | 0–500 | topic-relevant; recalled mid-conversation |

**Tool schemas** (JSON schema field in API call — separate from system prompt):

| Component | Chars |
|-----------|-------|
| 17 registered tool docstrings | ~8,400 |
| **Grand total per API call** | **~13,700–15,900** |

**Session overhead comparison (20-turn conversation):**

| Component | Before | After |
|-----------|--------|-------|
| Soul seed (static, once) | ~200–370 chars | ~400–600 chars |
| Per-turn personality injection | ~2,300–3,500 chars × 20 = ~46,000–70,000 | 0 chars (seed is static) |
| Mindset + critique (per-turn after Turn 1) | n/a | ~600–800 chars × 19 turns = ~11,400–15,200 total |

**Peer comparison** (system prompt only; tool schemas are separate in all systems):

| System | System prompt | Has personality |
|--------|--------------|-----------------|
| co | ~6,500–9,200 | Yes — expanded seed anchor + pre-classified mindset + critique |
| Gemini-CLI | ~18,000 | No — heavier operational/workflow guidance |
| aider (editblock mode) | ~4,500 | No — pure edit-format guidance |

Mindset content is classified once (Turn 1) and injected as `## Active mindset` every subsequent turn — zero model compliance required to load it, but present in every context window after classification.

### 2h. Design decisions

**Expanded seed as static anchor.** The soul seed is the complete static identity anchor: identity declaration, distilled trait essence, and hard constraints. Placed first in the static system prompt, it is present in every context window. The model's first context is always the soul — not a generic label. The Never list lives in the seed because negative constraints degrade faster than positive ones in long context, and the seed is the one place guaranteed to be present.

**Identity in seed, mindset via pre-turn classification.** Stable identity content (who the model is, hard constraints) lives in the static seed. Dynamic, task-shaped behavioral guidance is loaded automatically before Turn 1 via `MindsetDeclaration` classification and injected into every system prompt thereafter. The seed is authoritative configuration; mindset content is orchestrator-loaded context active for the session. This split prevents the fox-henhouse problem for identity (Never list is structural) while delivering task-relevant guidance without a model-called tool.

**Structural delivery for identity; orchestrator delivery for mindset.** The Never list and Core trait essence belong structurally in the seed — they need system prompt authority, not retrieval authority. Task-specific behavioral guidance (exploration approach, teaching style, debugging process) is classified before the first turn and injected per-turn via `inject_active_mindset` — always present in context without requiring model compliance to load it.

**Role immutability within a session.** `CoDeps.personality` is set once at session start, read-only thereafter. This prevents personality drift within a conversation.

**Personality modulates, never overrides.** Personality shapes HOW rules are expressed — never weakens safety, approval gates, or factual accuracy. There is no adoption mandate or override framing: the soul IS the identity, not a layer on top of a generic baseline.

**Character base memories between seed and rules.** The felt layer of each character — specific scenes, speech patterns, behavioral observations from the source film — is loaded deterministically by `get_agent()` and inserted between the soul seed and the behavioral rules. This keeps the seed lean (identity only) while ensuring the full character grounding is always present before the rules apply. No tool call needed, no LLM compliance risk.

**Examples trailing the rules.** `souls/{role}/examples.md` is placed after the five behavioral rules, immediately before model-specific quirks. This follows standard few-shot practice: examples are most effective as the last identity-level content the model reads before the task — position maximises pattern-match influence. Identity layer (seed + memories) and policy layer (rules) are cleanly separated from the demonstration layer (examples).

**Base vs. experience memory distinction.** Two kinds of memories coexist in the same store: *base* (planted, decay-protected, character source-material) and *experience* (detected/user-told, decayable, accumulated through task interactions). Distinguished by `source: planted` vs. `source: detected`/`user-told` and by `decay_protected: true` vs. absent. The experience layer grows organically; the base layer is stable character grounding that never mutates.

**No self-modification.** Peers openclaw (agent writes to SOUL.md) and letta (agent edits its own persona via `core_memory_replace()`) allow the agent to mutate its own personality. Co does not. `## Learned Context` memories already provide session-to-session adaptation without mutating structural files.

**No fragment composition.** The soul+mindset combination is hardwired per role in `souls/{role}/seed.md` and `mindsets/{role}/`. A new role requires only creating those files — no Python changes.

### 2i. Personality behavior evals

Personality quality is validated by `evals/eval_personality_behavior.py` against golden cases in `evals/personality_behavior.jsonl` using the real agent + real model.

This DESIGN doc keeps only the contract-level view:
- Pass/fail is computed per case from multi-run outcomes.
- Multi-turn consistency regressions are tracked as `drift`.
- Tool-call responses in place of final text are tracked as `tool_leakage`.

Implementation details (run loop, error handling, check semantics, outputs, tracing) are intentionally source-of-truth in:
- `evals/eval_personality_behavior.py`
- `evals/_common.py`
- `evals/personality_behavior.jsonl`

Run:
`uv run python evals/eval_personality_behavior.py --help`

---

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Role name — validated against `VALID_PERSONALITIES` at config load time. Missing seed/mindset files emit startup warnings via `_validate_personality()` but do not block startup |

Eval CLI flags are documented by the runner itself:
`uv run python evals/eval_personality_behavior.py --help`

---

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/__init__.py` | `assemble_prompt(provider, model_name, soul_seed, soul_examples)` — static prompt: seed+memories → rules → examples → quirks |
| `co_cli/prompts/_manifest.py` | `PromptManifest` dataclass — audit trail for prompt assembly |
| `co_cli/prompts/model_quirks.py` | Quirk file loader, counter-steering, inference params |
| `co_cli/prompts/rules/01..05_*.md` | 5 behavioral rules in filename order |
| `co_cli/prompts/quirks/{provider}/{model}.md` | Model-specific quirk files (YAML frontmatter + body) |
| `co_cli/deps.py` | `CoDeps` dataclass — `personality` (config-backed) |
| `co_cli/prompts/personalities/_composer.py` | `load_soul_seed(role)`, `load_soul_examples(role)`, `load_character_memories(role, memory_dir)`, `VALID_PERSONALITIES` |
| `co_cli/prompts/personalities/souls/{role}/seed.md` | Identity anchor: identity declaration + Core trait essence + Never list (3 roles: finch, jeff, tars) |
| `co_cli/prompts/personalities/souls/{role}/critique.md` | Always-on self-eval lens; loaded at session start by `load_soul_critique()`, injected every turn as `## Review lens` |
| `co_cli/prompts/personalities/souls/{role}/examples.md` | Optional trigger→response patterns; loaded by `load_soul_examples()`, placed after rules (3 roles: finch, jeff, tars) |
| `co_cli/prompts/personalities/mindsets/{role}/{task_type}.md` | Soul-specific behavioral guidance per task type (18 files: 6 types × 3 roles) |
| `.co-cli/knowledge/memories/` | Knowledge store: user experience memories (IDs 1–3) + character base memories (IDs 4–11, `decay_protected: true`, `source: planted`) |
| `co_cli/agent.py` | `get_agent(personality=…)` — builds soul block (seed + base memories), loads examples, assembles static prompt, registers 6 `@agent.system_prompt` functions (incl. mindset + critique), registers tools |
| `co_cli/tools/personality.py` | `MindsetDeclaration` model, `_apply_mindset()`, `_load_personality_memories()` helper |
| `co_cli/_history.py` | `_PERSONALITY_COMPACTION_ADDENDUM` — summarizer guard for personality moments |
| `co_cli/_commands.py` | Slash command registry and dispatch |
| `co_cli/config.py` | Field validator enforces role name in `VALID_PERSONALITIES`; `_validate_personality()` emits startup warnings for missing seed/mindset files |
| `evals/eval_personality_behavior.py` | Consolidated personality eval runner (single + multi-turn), majority vote, gates, JSON/MD/trace outputs |
| `evals/personality_behavior.jsonl` | Golden personality behavior cases (`id`, `personality`, `turns`, `checks_per_turn`) |
| `evals/_common.py` | Shared eval infrastructure: deps factory, settings passthrough, check engine, telemetry/trace parsing |
| `evals/personality_behavior-data.json` | Detailed eval output (auto-generated) |
| `evals/personality_behavior-result.md` | Human-readable eval report (auto-generated) |
| `evals/personality_behavior-trace-*.md` | Per-turn trace reports with model/tool/check internals (auto-generated) |
