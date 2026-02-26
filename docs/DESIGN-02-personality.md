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
| **Static assembly** | `assemble_prompt()` in `prompts/__init__.py` | Once at agent creation | Behavioral policy: rules + model quirks |
| **Per-turn injection** | `@agent.system_prompt` functions in `agent.py` | Before every model call | Character: soul block + learned context + situational context |

The two subsystems are independent. Static assembly operates correctly with no personality configured. Per-turn functions return empty string when no role is set, contributing nothing.

### Personality composition pipeline

The core of per-turn injection. `compose_personality(role, depth)` takes two orthogonal inputs — role (who co is) and depth (how deeply to engage) — and produces the assembled `## Soul` block:

```
compose_personality(role, depth)
  │
  ├── role  → souls/{role}.md          identity anchor + voice fingerprint + ## Never
  │         → traits/{role}.md         5 trait:value pairs   [session-immutable]
  │
  ├── depth → _DEPTH_OVERRIDES         selectively replaces trait values before file selection
  │           quick / normal / deep    [user-mutable within session]
  │
  └── traits (post-override) → behaviors/{k}-{v}.md   one file per active trait value
  ──────────────────────────────────────────────────────────────────────────────────
  → ## Soul block  injected into system prompt each turn
```

Role and depth compose at trait-lookup time: role supplies the baseline trait values, depth overrides specific ones before behavior files are selected. Changing depth never changes who co is — it only shifts which behavior files are loaded.

### Session state

Two fields on `CoDeps` control personality composition at runtime:

| Field | Controls | Source | Default | Scope |
|-------|----------|--------|---------|-------|
| `personality` | Who co is (identity) | `CO_CLI_PERSONALITY` / config | `"finch"` | Immutable within session |
| `reasoning_depth` | How deeply to engage | `/depth` command | `"normal"` | Mutable; resets on next session |

### Design invariants

These four constraints govern every decision in the sections below:

1. **File structure is the schema** — roles, traits, and behaviors are discovered by listing directories; no Python dicts, no hardcoded lists
2. **Structural delivery** — personality is injected by the framework on every turn; the LLM never requests it via tool
3. **Traits are role-hardwired** — no mix-and-match fragment composition; a custom combination requires one new traits file, no Python changes
4. **Modulate, never override** — personality shapes HOW rules are expressed; it never weakens safety, approval gates, or factual accuracy

### Prompt layer map

```
┌─────────────────────────────────────────────────────────────────┐
│ Static system prompt  (assembled once at agent creation)        │
│                                                                 │
│   instructions.md                                               │
│   rules/01..05_*.md                                             │
│   quirks/{provider}/{model}.md  (when file exists for model)    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Per-turn layers  (@agent.system_prompt functions in agent.py)   │
│                                                                 │
│   add_personality      → ## Soul block  (when role is set)      │
│   add_current_date     → today's date                           │
│   add_shell_guidance   → shell approval hint                    │
│   add_project_instructions → .co-cli/instructions.md            │
│   add_personality_memories → ## Learned Context  (when role set)│
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

`assemble_prompt(provider, model_name)` in `co_cli/prompts/__init__.py` is called once in `get_agent()`. It builds the static prompt from instructions, rules, and quirks — personality is not included.

```
load instructions.md
for each rules/*.md in NN_ numeric order:
    validate filename format (NN_rule_id.md)
    validate order is contiguous from 01, no duplicates
    append content
if quirk file exists for provider/model:
    append "## Model-Specific Guidance\n\n" + body
join all parts with "\n\n"
```

Rule file validation is strict: filenames must match `NN_rule_id.md`, numeric prefixes must be unique and contiguous starting at 01. Assembly fails with `ValueError` on violations. `PromptManifest` tracks `parts_loaded` names, `total_chars`, and `warnings` for diagnostics.

**Behavioral rules** — five rule files define co's behavioral policy. Rules are cross-cutting principles; tool-specific guidance lives in tool docstrings, not rules. Target budget: < 1,100 tokens total across all 5 rules.

| Rule | File | Governs |
|------|------|---------|
| **01 Identity** | `01_identity.md` | Core traits: helpful, curious, adaptive, honest. Anti-sycophancy: prioritize technical accuracy over agreement; respectful correction over false validation |
| **02 Safety** | `02_safety.md` | Credential protection, source control caution, approval philosophy, memory constraints: never save workspace paths, transient errors, or session-specific output |
| **03 Reasoning** | `03_reasoning.md` | Verification-first; fact authority: tool output beats training data for deterministic state, user preference beats tool output for subjective decisions; two kinds of unknowns: discoverable facts (use tools) vs preferences (ask user with 2–4 options) |
| **04 Tool Protocol** | `04_tool_protocol.md` | 8–12 word preamble before tool calls; bias toward action; parallel when independent, sequential when dependent |
| **05 Workflow** | `05_workflow.md` | Three-category intent: **Directive** (action, may mutate state), **Deep Inquiry** (research, no mutation), **Shallow Inquiry** (default, single-lookup) |

Rules encode behavioral norms that soul files cannot — soul files define *who* co is, rules define *how it behaves under ambiguity*; the split prevents soul files from becoming policy documents. Anti-sycophancy is explicit in Rule 01 because base models trend toward agreement; a named principle at the identity layer is harder to suppress than a buried guideline. Three-category workflow classification prevents the model from modifying files when the user is asking a question — without it the approval gate catches the side effect but the model wastes turns attempting edits that will be rejected. "Two kinds of unknowns" prevents unnecessary clarification questions — a model without this asks "what language?" when it could run `ls *.py`.

**Model quirks** — four behavioral patterns observed across Gemini and Ollama models, each with counter-steering prose appended as `## Model-Specific Guidance`:

| Category | Symptom | Counter-steering |
|----------|---------|-----------------|
| `verbose` | Excessive prose, restates question, unnecessary hedging | Be concise. Skip preamble. Answer directly. |
| `overeager` | Modifies files or calls more tools than requested | Stay within literal scope. Do not make changes beyond what was asked. |
| `lazy` | Shortcut implementations, placeholder code, stub returns | Implement fully. No stubs, no TODOs, no placeholder comments. |
| `hesitant` | Asks too many clarification questions instead of acting | Act first on reasonable assumptions, clarify after only if needed. |

6 quirk files shipped: `gemini/{2.5-flash, 2.5-pro, 3-flash-preview, 3-pro-preview}.md` and `ollama/{qwen3, qwen3-coder-next}.md`. Each file contains YAML frontmatter (flags, inference params) plus the prose body.

### 2b. Per-turn injection

Five `@agent.system_prompt` functions registered in `get_agent()` in `co_cli/agent.py`. pydantic-ai appends their return values to the static system prompt before every model call. Functions returning empty string contribute nothing.

| Function | Condition | Content |
|----------|-----------|---------|
| `add_personality` | `ctx.deps.personality` is set | `compose_personality(role, depth)` — full `## Soul` block |
| `add_current_date` | Always | `"Today is {date}."` |
| `add_shell_guidance` | Always | Shell approval hint |
| `add_project_instructions` | `.co-cli/instructions.md` exists | Project-specific instructions |
| `add_personality_memories` | `ctx.deps.personality` is set | `## Learned Context` section (top 5 personality-context memories by recency) |

The static prompt is assembled once and never re-read between turns; the per-turn functions read from `ctx.deps` on every call. This means the static prompt works correctly with no personality configured, and personality composition stays isolated in `_composer.py`.

### 2c. Personality composition

`compose_personality(role, depth="normal")` in `co_cli/prompts/personalities/_composer.py`. Called by `add_personality()` before every model request.

```
load souls/{role}.md                              → role identity basis + voice fingerprint + anti-patterns
load traits/{role}.md                             → parse "key: value" lines into fresh dict
traits.update(_DEPTH_OVERRIDES.get(depth, {}))    → apply user depth overrides before behavior file selection
for each key, value in traits dict:
    load behaviors/{key}-{value}.md               → behavioral guidance (uses overridden values)
concatenate: "## Soul\n\n" + soul + behaviors + adoption mandate
```

`load_traits()` always returns a freshly constructed `dict[str, str]` — never a cached reference — so `traits.update()` is safe to mutate without affecting subsequent calls. Depth overrides are applied before file loading so overridden trait values flow directly into behavior file selection. `VALID_PERSONALITIES` is derived from `traits/` folder listing via `_discover_valid_personalities()` — no hardcoded list.

**File layout:**

```
co_cli/prompts/personalities/
├── souls/       identity basis + voice fingerprint + ## Never anti-patterns (1 per role)
├── traits/      key: value wiring per role (1 per role)
├── behaviors/   behavioral guidance ({trait}-{value}.md, 16 files)
└── _profiles/   (moved to co_cli/_profiles/ — authoring reference, never runtime-loaded)
```

The folder structure is the schema — behaviors, traits, and souls are discovered by listing directories, not declared in Python. Adding a role or trait value requires zero code changes.

Each soul file opens with who co is from that role's perspective — the identity anchor is built into the soul, not extracted into a shared `_base.md`. A shared base would collapse distinct role expressions into generic prose. Each soul file contains a `## Never` section listing role-specific anti-patterns; this mirrors peer precedent (TinyTroupe `dislikes` field, openclaw SOUL.md boundary statements).

**5 traits** — grounded in Big Five personality research, mapped to CLI companion context:

| Trait | Values | Controls | Big Five mapping |
|-------|--------|----------|-----------------|
| `communication` | terse, balanced, warm, educational | Verbosity, formality, explanation depth | Extraversion |
| `relationship` | mentor, peer, companion, professional | Social dynamic with user | *(no equivalent)* |
| `curiosity` | proactive, reactive | Initiative, follow-up questions | Openness |
| `emotional_tone` | empathetic, neutral, analytical | Warmth vs objectivity | Agreeableness |
| `thoroughness` | minimal, standard, comprehensive | Detail depth, verification, step-by-step | Conscientiousness |

Neuroticism (Big Five #5) is not applicable to AI assistants.

**4 roles and their trait wiring:**

| Role | communication | relationship | curiosity | emotional_tone | thoroughness |
|------|--------------|--------------|-----------|----------------|-------------|
| finch | balanced | mentor | proactive | empathetic | comprehensive |
| jeff | warm | peer | proactive | empathetic | standard |
| terse | terse | professional | reactive | neutral | minimal |
| inquisitive | educational | companion | proactive | neutral | comprehensive |

All trait values map to behavior files with actual guidance — no trait is label-only. This invariant ensures every trait value has a measurable behavioral effect.

**Adding a new role** requires only files — no Python changes:
1. Write `souls/{name}.md` — identity basis, voice fingerprint, `## Never` section
2. Write `traits/{name}.md` — pick values from existing behaviors
3. `VALID_PERSONALITIES` updates automatically from `traits/` folder listing
4. Optionally write `co_cli/_profiles/{name}.md` — authoring reference (never runtime-loaded)

**Adding a new trait value** requires only one file — write `behaviors/{trait}-{value}.md`.

### 2d. Reasoning depth override

`reasoning_depth` is user-expressed session intent stored on `CoDeps`. It is a prompt assembly concern, not a personality property — the personality (role, soul, traits) is unchanged. At compose time it overrides specific trait lookups so the assembled `## Soul` block reflects the user's desired depth without altering who co is.

`_DEPTH_OVERRIDES` is defined in `co_cli/prompts/_reasoning_depth_override.py` (not inside `personalities/`) because depth is a user session intent that modulates prompt assembly, not a property of the personality itself.

```
VALID_DEPTHS = ["quick", "normal", "deep"]

_DEPTH_OVERRIDES:
    "quick":
        thoroughness → minimal    # suppress verification, rationale, step-by-step
        curiosity → reactive      # answer what was asked; stop volunteering follow-up questions
    "normal":
        (no overrides — role defaults apply unchanged)
    "deep":
        thoroughness → comprehensive  # verify results, explain reasoning chain, surface edge cases
```

Which traits are overridden for `quick` and why the others are not:

| Trait | Overridden in `quick`? | Reason |
|-------|------------------------|--------|
| `thoroughness` | **Yes → minimal** | Core depth control — suppresses verification, reasoning chain, and rationale |
| `curiosity` | **Yes → reactive** | Proactive curiosity asks follow-up questions on every ambiguous input — directly opposes a user who wants a result, not questions back |
| `emotional_tone` | No | Warmth is compatible with quick responses; verbose expression is already suppressed by the thoroughness override |
| `communication` | No | Already concise-leaning at balanced — no conflict |
| `relationship` | No | Social dynamic, not verbosity; expression depth is governed by thoroughness and curiosity |

`deep` overrides only `thoroughness` — the other four traits already support depth at their role defaults. Both overrides are no-ops for roles already at those values: `quick` for `terse`, `deep` for `finch`/`inquisitive`.

**`/depth` slash command:**
- `/depth` — prints current depth and lists valid values
- `/depth quick` — sets `ctx.deps.reasoning_depth = "quick"`, prints confirmation
- `/depth xyz` — prints error listing valid values
- `/depth <any>` when no personality configured — sets field but warns depth has no effect

`reasoning_depth` lives on `CoDeps` (not in message history) — survives context compaction, active for the full session, resets on next session.

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

**System prompt** (string field — `Agent(system_prompt=...)` + per-turn `@agent.system_prompt` functions):

| Component | Chars | Notes |
|-----------|-------|-------|
| Static: instructions + 5 rules | ~4,948 | assembled once at agent creation |
| Static: counter-steering (quirk file body) | 0–500 | model-specific, when file exists |
| Per-turn: soul + 5 behaviors | ~2,300–2,750 | varies by role (terse: ~2,300, finch: ~2,750) |
| Per-turn: personality memories | 0–500 | top-5 personality-context memories |
| Per-turn: date + shell hint + project instructions | ~100–500 | always present |
| **System prompt total (with personality)** | **~7,500–9,200** | |
| **System prompt total (without personality)** | **~5,100–6,000** | |

**Tool schemas** (JSON schema field in API call — separate from system prompt):

| Component | Chars |
|-----------|-------|
| 16 registered tool docstrings | ~8,200 |
| **Grand total per API call** | **~15,700–17,400** |

**Peer comparison** (system prompt only; tool schemas are separate in all systems):

| System | System prompt | Has personality |
|--------|--------------|-----------------|
| co (with personality) | ~7,500–9,200 | Yes — soul + trait behaviors |
| co (without personality) | ~5,100–6,000 | No |
| Gemini-CLI | ~18,000 | No — heavier operational/workflow guidance |
| aider (editblock mode) | ~4,500 | No — pure edit-format guidance |

The static + soul split (61% / 34% of system prompt) is not a budget imbalance against tooling — tool schemas occupy a separate delivery channel and do not compete with personality for system prompt space. Co's system prompt is compact relative to gemini-cli while adding personality that peers omit entirely.

### 2h. Design decisions

**Structural delivery, not voluntary.** All personality content is in the system prompt on every turn — the LLM never requests it via a tool. If personality were tool-gated, the LLM's helpfulness bias would suppress it on most turns to be "efficient" (fox-henhouse problem). Structural injection eliminates this entirely.

**Role immutability within a session.** `CoDeps.personality` is set once at session start, read-only thereafter. `reasoning_depth` is the only in-session override. This prevents personality drift within a conversation.

**Personality modulates, never overrides.** Personality shapes HOW rules are expressed — never weakens safety, approval gates, or factual accuracy.

**No self-modification.** Peers openclaw (agent writes to SOUL.md) and letta (agent edits its own persona via `core_memory_replace()`) allow the agent to mutate its own personality. Co does not. `## Learned Context` memories already provide session-to-session adaptation without mutating structural files.

**No fragment composition.** Traits are hardwired per role in `traits/{role}.md`. If a custom combination is needed, creating `traits/custom.md` requires only one file and no Python changes.

---

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Role name — validated against `VALID_PERSONALITIES` at config load time by `_validate_personality()` in `config.py` |

`reasoning_depth` is session-only state on `CoDeps`, not a config setting. It always starts at `"normal"`, is mutated by `/depth` during the session, and resets on next session.

---

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/__init__.py` | `assemble_prompt()` — static prompt only (instructions + rules + quirks) |
| `co_cli/prompts/_manifest.py` | `PromptManifest` dataclass — audit trail for prompt assembly |
| `co_cli/prompts/instructions.md` | Bootstrap identity sentence |
| `co_cli/prompts/model_quirks.py` | Quirk file loader, counter-steering, inference params |
| `co_cli/prompts/rules/01..05_*.md` | 5 behavioral rules in filename order |
| `co_cli/prompts/quirks/{provider}/{model}.md` | Model-specific quirk files (YAML frontmatter + body) |
| `co_cli/deps.py` | `CoDeps` dataclass — `personality` (config-backed), `reasoning_depth` (session-only, default `"normal"`) |
| `co_cli/prompts/_reasoning_depth_override.py` | `VALID_DEPTHS`, `_DEPTH_OVERRIDES` — user depth intent → trait override map |
| `co_cli/prompts/personalities/_composer.py` | `load_soul()`, `load_traits()`, `compose_personality(role, depth)`, `VALID_PERSONALITIES` |
| `co_cli/prompts/personalities/souls/*.md` | Role identity basis + voice fingerprint + `## Never` anti-pattern sections (4 files) |
| `co_cli/prompts/personalities/traits/*.md` | Trait wiring (1 per role, 5 `key: value` lines each) |
| `co_cli/prompts/personalities/behaviors/*.md` | Behavioral guidance (16 files, `{trait}-{value}.md` naming) |
| `co_cli/_profiles/*.md` | Role authoring reference (source asset, never runtime-loaded; 1 per role) |
| `co_cli/agent.py` | `get_agent()` — registers 5 `@agent.system_prompt` functions |
| `co_cli/tools/personality.py` | `_load_personality_memories()` — loads personality-context tagged memories |
| `co_cli/_history.py` | `_PERSONALITY_COMPACTION_ADDENDUM` — summarizer guard for personality moments |
| `co_cli/_commands.py` | `/depth` slash command handler |
| `co_cli/_debug_personality.py` | `run_debug_personality()` — diagnostic for `co debug-personality` |
| `co_cli/config.py` | `_validate_personality()` — validates role name against `VALID_PERSONALITIES` |
Eval infrastructure (adherence + cross-turn) is documented in [DESIGN-02-eval.md](DESIGN-02-eval.md).
