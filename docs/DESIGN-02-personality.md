---
title: Personality System
nav_order: 3
---

# Personality System

## 1. What & How

The personality system delivers co's character structurally — injected into every model request as a system prompt layer, never left to the LLM to request voluntarily. Role identity is defined by files (souls, traits, behaviors), not Python dicts. Five `@agent.system_prompt` functions registered in `get_agent()` append their output to the static prompt before each model call. When no role is configured, the personality functions return empty strings and the model operates on the static prompt only.

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

### 2a. Static vs per-turn split

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

Five `@agent.system_prompt` functions are registered in `get_agent()` in `co_cli/agent.py`. pydantic-ai appends their return values to the static system prompt string before every model call. Functions returning empty string contribute nothing.

| Function | Condition | Content |
|----------|-----------|---------|
| `add_personality` | `ctx.deps.personality` is set | `compose_personality(role, depth)` — full `## Soul` block |
| `add_current_date` | Always | `"Today is {date}."` |
| `add_shell_guidance` | Always | Shell approval hint |
| `add_project_instructions` | `.co-cli/instructions.md` exists | Project-specific instructions |
| `add_personality_memories` | `ctx.deps.personality` is set | `## Learned Context` section (top 5 personality-context memories by recency) |

### 2b. Personality composition

`compose_personality(role, depth="normal")` in `co_cli/prompts/personalities/_composer.py`. Called by `add_personality()` before every model request.

```
load souls/{role}.md                              → role identity basis + voice fingerprint + anti-patterns
load traits/{role}.md                             → parse "key: value" lines into fresh dict
traits.update(_DEPTH_OVERRIDES.get(depth, {}))    → apply user depth overrides before behavior file selection
for each key, value in traits dict:
    load behaviors/{key}-{value}.md               → behavioral guidance (uses overridden values)
concatenate: "## Soul\n\n" + soul + behaviors + adoption mandate
```

`load_traits()` always returns a freshly constructed `dict[str, str]` — never a cached reference — so `traits.update()` is safe to mutate without affecting subsequent calls. Depth overrides are applied before file loading so the overridden trait values flow directly into behavior file selection.

Supporting functions:
- `load_soul(role)` — reads `souls/{role}.md`, raises `FileNotFoundError` if missing
- `load_traits(role)` — reads `traits/{role}.md`, parses `key: value` lines, returns a fresh `dict[str, str]` on every call; skips blank lines and lines without `:`
- `VALID_PERSONALITIES` — module-level list derived from `traits/` folder listing via `_discover_valid_personalities()`; no hardcoded dict

### 2c. Personality file structure

```
co_cli/prompts/personalities/
├── souls/
│   ├── finch.md       identity basis + voice fingerprint + ## Never anti-patterns
│   ├── jeff.md
│   ├── terse.md
│   └── inquisitive.md
├── traits/            what traits each role has (key: value wiring per role)
├── behaviors/         how each trait value behaves ({trait}-{value}.md)
└── _profiles/         (moved to co_cli/_profiles/ — authoring reference, never runtime-loaded)
```

`co_cli/_profiles/` contains one authoring reference file per role documenting the intended personality. These are source code assets, never loaded at runtime.

Each soul file opens with who co is from that role's perspective — the identity anchor is built into the soul, not extracted into a shared `_base.md`. Different roles have distinct identity expressions. `finch.md` grounds co as a terminal companion who teaches by doing. `terse.md` grounds co as a direct executor who respects the user's time. A shared base would collapse these into generic prose and erase the identity differentiation that makes each role feel distinct.

**5 traits** — grounded in Big Five personality research, mapped to CLI companion context:

| Trait | Values | Controls | Big Five mapping |
|-------|--------|----------|-----------------|
| `communication` | terse, balanced, warm, educational | Verbosity, formality, explanation depth | Extraversion |
| `relationship` | mentor, peer, companion, professional | Social dynamic with user | No equivalent — unique to companion vision |
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

Each soul file contains a `## Never` section listing role-specific behaviors to avoid. This mirrors peer precedent: soul.md (STYLE.md anti-patterns in soul.md-style systems), TinyTroupe (`dislikes` field), openclaw (SOUL.md boundary statements).

**Adding a new role** requires only files — no Python changes:
1. Write `souls/{name}.md` — open with 1-2 sentences establishing the identity basis, then voice fingerprint, then `## Never` section
2. Write `traits/{name}.md` — pick values from existing behaviors
3. `VALID_PERSONALITIES` updates automatically from `traits/` folder listing
4. Optionally write `co_cli/_profiles/{name}.md` — authoring reference (never runtime-loaded)

**Adding a new trait value** requires only one file — write `behaviors/{trait}-{value}.md`.

### 2d. Reasoning depth override

`reasoning_depth` is user-expressed session intent stored on `CoDeps`. It is a prompt assembly concern, not a personality property — the personality (role, soul, traits) is unchanged. At compose time it overrides specific trait lookups in behavior file selection so the assembled `## Soul` block reflects the user's desired depth without altering who co is.

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

Analysis of which traits are overridden for `quick` and why others are not:

| Trait | Finch default | Overridden in `quick`? | Reason |
|-------|--------------|------------------------|--------|
| `thoroughness` | comprehensive | **Yes → minimal** | Core depth control — skip verification, reasoning chain, rationale |
| `curiosity` | proactive | **Yes → reactive** | Proactive curiosity asks follow-up questions on every ambiguous input — directly opposes a user who wants a result, not questions back |
| `emotional_tone` | empathetic | No | Warmth is compatible with quick responses; verbose expression is already suppressed by the thoroughness override |
| `communication` | balanced | No | Already concise-leaning — no conflict |
| `relationship` | mentor | No | Social dynamic, not verbosity; expression depth is governed by thoroughness and curiosity |

`deep` overrides only `thoroughness` — curiosity, communication, relationship, and emotional_tone already support depth at their role defaults. `deep` is a no-op for roles that already have `thoroughness: comprehensive` (finch, inquisitive). `quick` is a no-op for `terse` — which already has `thoroughness: minimal` and `curiosity: reactive`.

**`/depth` slash command behavior:**
- `/depth` — prints current depth and lists valid values (`quick`, `normal`, `deep`)
- `/depth quick` — sets `ctx.deps.reasoning_depth = "quick"`, prints confirmation
- `/depth xyz` — prints error listing valid values
- `/depth <any>` when no personality is configured — sets the field but warns: "No personality configured — depth has no effect until a role is set"

`reasoning_depth` lives on `CoDeps` (not in message history), so it survives context compaction and remains active for the full session.

### 2e. Personality memories

`_load_personality_memories()` in `co_cli/tools/personality.py`. Called by `add_personality_memories()` per turn.

```
scan .co-cli/knowledge/memories/*.md for tag "personality-context"
sort by updated (or created) descending
take top 5
format as "## Learned Context\n\n- {content}\n- {content}\n..."
```

Returns empty string if no matching memories exist or the directory is absent. This provides session-to-session adaptation (co learns user preferences) without modifying the structural personality files.

### 2f. Compaction guard

When history is summarized (context window management), `_PERSONALITY_COMPACTION_ADDENDUM` in `co_cli/_history.py` is appended to the summarizer prompt when `personality_active=True`. It instructs the summarizer to preserve:
- Personality-reinforcing moments (emotional exchanges, humor, relationship dynamics)
- User reactions that shaped tone or communication style
- Explicit personality preferences or corrections from the user

Without this guard, compaction would lose relational context that makes personality feel continuous across long sessions.

### 2g. Design decisions

**Structural delivery, not voluntary.** All personality content is in the system prompt on every turn. The LLM never requests personality via a tool. If personality were tool-gated, the LLM's helpfulness bias would suppress it on most turns to be "efficient" — the fox-henhouse problem. Structural injection eliminates this entirely.

**Static vs per-turn split.** The static prompt (instructions + rules + quirks) is assembled once and passed to pydantic-ai's `Agent(system_prompt=...)`. It does not change between turns. Personality is per-turn because it reads from `ctx.deps.personality` and `ctx.deps.reasoning_depth` on the `CoDeps` dataclass. This separation means the static prompt works without personality and personality composition stays isolated in `_composer.py`.

**Role immutability within a session.** `CoDeps.personality` is a flat scalar set once at session start, read-only thereafter. `reasoning_depth` is the only in-session override available to the user. This prevents personality drift within a conversation.

**File-driven personality, no Python dicts.** Roles are defined by files (souls + traits), not Python dicts. Behaviors are defined by files, not TypedDicts. The folder structure is the schema. Adding a new role requires zero Python changes.

**Every trait has content.** All trait values map to behavior files with actual guidance — no trait is label-only. This ensures every trait value has measurable behavioral effect.

**Personality modulates, never overrides.** Personality shapes HOW rules are expressed. It never weakens safety, approval gates, or factual accuracy.

**Role personality is not self-modifiable.** Peers openclaw (agent writes to SOUL.md) and letta (agent edits own persona block via `core_memory_replace()`) allow the agent to mutate its own personality. Co does not. This prevents personality drift, loss of designed character coherence, and unpredictable cross-session behavior. The `## Learned Context` memories injected per turn already provide session-to-session adaptation without modifying the structural personality.

**No personality fragment composition.** Traits are hardwired per role in `traits/{role}.md`. The current 4 roles cover the useful personality space for a CLI companion. If a custom combination is needed, creating `traits/custom.md` requires only one file and no Python changes.

### 2h. Prompt budget (measured)

| Component | Chars |
|-----------|-------|
| Static prompt (instructions + 5 rules) | ~4,936 |
| Counter-steering (from quirk file body) | 0–500 |
| Soul section (identity basis + anti-patterns + 5 behaviors + mandate) | ~1,900–3,100 |
| Dynamic: personality memories | 0–500 |
| Dynamic: date + shell + project instructions | ~100–500 |
| **Total (with personality)** | **~7,000–8,500** |
| **Total (without personality)** | **~5,000–5,500** |
| **Budget ceiling** | **8,500** |

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
| `co_cli/prompts/_reasoning_depth_override.py` | `VALID_DEPTHS`, `_DEPTH_OVERRIDES` — user depth intent → trait override map; lives at prompts layer, not inside `personalities/` |
| `co_cli/prompts/personalities/_composer.py` | `load_soul()`, `load_traits()`, `compose_personality(role, depth)`, `VALID_PERSONALITIES` |
| `co_cli/prompts/personalities/souls/*.md` | Role identity basis + voice fingerprint + `## Never` anti-pattern sections (4 files) |
| `co_cli/prompts/personalities/traits/*.md` | Trait wiring (1 per role, 5 `key: value` lines each) |
| `co_cli/prompts/personalities/behaviors/*.md` | Behavioral guidance (16 files, `{trait}-{value}.md` naming) |
| `co_cli/_profiles/*.md` | Role authoring reference (source asset, never runtime-loaded; 1 per role) |
| `co_cli/agent.py` | `get_agent()` — registers 5 `@agent.system_prompt` functions including `add_personality` and `add_personality_memories` |
| `co_cli/tools/personality.py` | `_load_personality_memories()` — loads personality-context tagged memories |
| `co_cli/_history.py` | `_PERSONALITY_COMPACTION_ADDENDUM` — summarizer guard for personality moments |
| `co_cli/_commands.py` | Slash command registry — `_cmd_depth` handler + `"depth"` entry in `COMMANDS` |
| `co_cli/_debug_personality.py` | `run_debug_personality()` — diagnostic output for `co debug-personality`; shows which trait files are active under current depth with override annotations |
| `co_cli/config.py` | `_validate_personality()` — validates role name against `VALID_PERSONALITIES` |
| `evals/eval_personality_adherence.py` | Personality adherence eval — 6 heuristic check types, majority-vote scoring |
| `evals/p2-personality_adherence.jsonl` | 20 test cases across 4 roles |
