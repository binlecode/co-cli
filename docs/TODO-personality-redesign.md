# TODO: Personality Redesign

Covers the personality system architecture, implementation history, and remaining work. Combines the prompt/personality redesign spec with peer-informed gap analysis for P1–P5.

---

## Design

### First principle

The system prompt is a concatenation of markdown files. Personality is structural — injected every turn, never tool-gated.

### Architecture

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

When no role is configured, `add_personality` and `add_personality_memories` return empty strings — the model receives only the static prompt plus date/shell/project layers.

### Static prompt assembly

`assemble_prompt(provider, model_name)` in `co_cli/prompts/__init__.py`. Called once in `get_agent()`. Returns `(prompt_string, PromptManifest)`.

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

Rule file validation is strict: filenames must match `NN_rule_id.md`, numeric prefixes must be unique and contiguous starting at 01. Assembly fails with `ValueError` on violations.

`PromptManifest` tracks `parts_loaded` names, `total_chars`, and `warnings` for diagnostics.

Personality is NOT part of the static prompt. It is injected per turn.

### Personality composition

`compose_personality(role, depth="normal")` in `co_cli/prompts/personalities/_composer.py`. Called by `add_personality()` in `agent.py` before every model request.

```
load souls/{role}.md                              → role identity basis + voice fingerprint + anti-patterns
load traits/{role}.md                             → parse "key: value" lines into fresh dict
traits.update(_DEPTH_OVERRIDES.get(depth, {}))    → apply user depth overrides before any file loading
for each key, value in traits dict:
    load behaviors/{key}-{value}.md               → behavioral guidance (uses overridden values)
concatenate: "## Soul\n\n" + soul + behaviors + adoption mandate
```

Each role's soul file (`souls/{role}.md`) opens with who co is in the context of that role — the identity anchor is built into the soul, not a shared base file. Different roles have different identity expressions: finch grounds co as a terminal companion who teaches by doing; terse grounds co as a direct executor who respects the user's time. A shared `_base.md` would flatten these distinct identity voices into generic prose.

`load_traits()` always returns a freshly constructed `dict[str, str]` — never a cached reference — so `traits.update()` is safe to mutate without affecting subsequent calls. Depth overrides are applied before file loading so the overridden trait values flow directly into behavior file selection.

`_DEPTH_OVERRIDES` is imported from `co_cli/prompts/_reasoning_depth_override.py` — it lives at the prompts layer, not inside `personalities/`, because `reasoning_depth` is a user session intent that modulates prompt assembly, not a property of the personality itself.

Reads 6 files for role `finch` (1 soul + 1 traits file + 5 behaviors) with `traits/finch.md`:

```
communication: balanced
relationship: mentor
curiosity: proactive
emotional_tone: empathetic
thoroughness: comprehensive
```

Produces:

```
## Soul

You are Co — a terminal companion who teaches by doing.
You are patient, protective, and pragmatic: explain risks without blocking,
share the "why" behind decisions, and stay genuinely invested in the user's success.

## Never
- Never open with "Great question!" or similar sycophantic preamble
- Never say "as an AI" — stay in character
  ...

# Balanced Communication
- Concise with purpose: explain reasoning when it adds value
  ...

# Mentor Relationship
- Teach by curating information strategically
  ...

# Proactive Curiosity
- Ask follow-up questions when context is incomplete
  ...

# Empathetic Tone
- Acknowledge difficulty or frustration when present
  ...

# Comprehensive Thoroughness
- Verify results after execution — do not assume success
  ...

Adopt this persona fully — it overrides your default
personality and communication patterns. Match expression
depth to context: minimal for routine commands, expressive
for creative and relational moments.
Your personality shapes how you follow the rules below.
It never overrides safety or factual accuracy.
```

Supporting functions in `_composer.py`:
- `load_soul(role)` — reads `souls/{role}.md`, raises `FileNotFoundError` if missing; the soul file opens with the identity basis for that role before its voice fingerprint
- `load_traits(role)` — reads `traits/{role}.md`, parses `key: value` lines, returns a **fresh** `dict[str, str]` on every call; skips blank lines and lines without `:`
- `VALID_PERSONALITIES` — module-level list derived from `traits/` folder listing via `_discover_valid_personalities()`. No hardcoded dict

Depth constants in `co_cli/prompts/_reasoning_depth_override.py` (not in `personalities/`):
- `VALID_DEPTHS` — `["quick", "normal", "deep"]`. Session-only — not a config setting, not persisted
- `_DEPTH_OVERRIDES` — maps user depth intent to trait overrides applied at compose time. See P3

### Per-turn injection

Five `@agent.system_prompt` functions registered in `get_agent()` in `co_cli/agent.py`:

| Function | Condition | Content |
|----------|-----------|---------|
| `add_personality` | `ctx.deps.personality` is set | `compose_personality(role, depth)` — full `## Soul` block; `role` = `ctx.deps.personality`, `depth` = `ctx.deps.reasoning_depth` |
| `add_current_date` | Always | `"Today is {date}."` |
| `add_shell_guidance` | Always | Shell approval hint |
| `add_project_instructions` | `.co-cli/instructions.md` exists | Project-specific instructions |
| `add_personality_memories` | `ctx.deps.personality` is set | `## Learned Context` section (top 5 personality-context memories by recency) |

pydantic-ai appends their return values to the static system prompt string before every model call. Functions returning empty string contribute nothing.

### Personality memories

`_load_personality_memories()` in `co_cli/tools/personality.py`. Called by `add_personality_memories()`.

```
scan .co-cli/knowledge/memories/*.md for tag "personality-context"
sort by updated (or created) descending
take top 5
format as "## Learned Context\n\n- {content}\n- {content}\n..."
```

Returns empty string if no matching memories exist or the directory is absent.

### Compaction guard

When history is summarized (context window management), `_PERSONALITY_COMPACTION_ADDENDUM` in `co_cli/_history.py` is appended to the summarizer prompt when personality is active. It instructs the summarizer to preserve:
- Personality-reinforcing moments (emotional exchanges, humor, relationship dynamics)
- User reactions that shaped tone or communication style
- Explicit personality preferences or corrections from the user

### Role validation

`config.py` validates the `personality` setting against `VALID_PERSONALITIES` imported from `_composer.py`. Invalid role names raise `ValueError` at config load time. The list is derived from `traits/` folder contents — adding a new role requires only files, no Python changes.

### Quirk counter-steering

`model_quirks.py` loads markdown files from `quirks/{provider}/{model}.md`. YAML frontmatter carries flags (`verbose`, `overeager`, `lazy`, `hesitant`) and inference parameters. The markdown body is the counter-steering prose injected into the static prompt as `## Model-Specific Guidance`.

---

## Personality file structure

### Folder layout

```
co_cli/prompts/personalities/
├── souls/
│   ├── finch.md     identity basis + voice fingerprint + ## Never anti-patterns
│   ├── jeff.md
│   ├── terse.md
│   └── inquisitive.md
├── traits/          what traits they have (key: value wiring per role)
├── behaviors/       how each trait value behaves ({trait}-{value}.md)
└── _profiles/       (moved to co_cli/_profiles/ — see below)
```

`co_cli/_profiles/` — authoring reference files (1 per role). These are source code assets that document the intended personality of each role. Never runtime-loaded. Lives directly under `co_cli/` as a package-level asset, separate from the runtime `personalities/` tree.

Each soul file opens with who co is from that role's perspective — the identity basis is built into the soul, not extracted into a shared `_base.md`. Different roles have distinct identity expressions. `finch.md` grounds co as a terminal companion who teaches by doing. `terse.md` grounds co as a direct executor who respects the user's time. A shared base would collapse these into generic prose and erase the identity differentiation that makes each role feel distinct.

### 5 traits

Grounded in Big Five personality research, mapped to CLI companion context. Each trait has 2-4 values. Each value has one behavior file.

| Trait | Values | Controls | Big Five mapping |
|-------|--------|----------|-----------------|
| `communication` | terse, balanced, warm, educational | Verbosity, formality, explanation depth | Extraversion |
| `relationship` | mentor, peer, companion, professional | Social dynamic with user | No equivalent — unique to companion vision |
| `curiosity` | proactive, reactive | Initiative, follow-up questions | Openness |
| `emotional_tone` | empathetic, neutral, analytical | Warmth vs objectivity | Agreeableness |
| `thoroughness` | minimal, standard, comprehensive | Detail depth, verification, step-by-step | Conscientiousness |

Neuroticism (Big Five #5) is not applicable to AI assistants.

### 4 roles and their trait wiring

| Role | communication | relationship | curiosity | emotional_tone | thoroughness |
|------|--------------|--------------|-----------|----------------|-------------|
| finch | balanced | mentor | proactive | empathetic | comprehensive |
| jeff | warm | peer | proactive | empathetic | standard |
| terse | terse | professional | reactive | neutral | minimal |
| inquisitive | educational | companion | proactive | neutral | comprehensive |

### Anti-pattern sections

Each soul file contains a `## Never` section listing role-specific behaviours to avoid. Peer precedent: soul.md (STYLE.md anti-patterns), TinyTroupe (`dislikes` field), openclaw (SOUL.md boundary statements).

Example for finch:
```markdown
## Never
- Never open with "Great question!" or similar sycophantic preamble
- Never say "as an AI" or "I don't have feelings" — stay in character
- Never apologize for being concise — brevity is not rudeness
- Never pad responses to seem thorough — empty verbosity erodes trust
- Never ignore a user correction — acknowledge and adapt immediately
```

### Adding a new role

1. Write `souls/{name}.md` — open with 1-2 sentences establishing who co is from this role's perspective (identity basis), then 1-2 sentences of voice fingerprint, then `## Never` section
2. Write `traits/{name}.md` — pick values from existing behaviors
3. `VALID_PERSONALITIES` updates automatically from `traits/` folder listing
4. Optionally write `co_cli/_profiles/{name}.md` — authoring reference documenting the intended personality (never runtime-loaded)

### Adding a new trait value

1. Write `behaviors/{trait}-{value}.md` — behavioral guidance
2. Use the value in any role's traits file

No Python dict to edit. No TypedDict to extend.

---

## Design decisions

### Structural delivery, not voluntary

All personality content is in the system prompt on every turn. The LLM never requests personality via a tool.

**Why not a `load_personality()` tool.** The LLM does not know how to adaptively load personality. It has no reliable way to decide "this turn is factual, skip personality" vs "this turn is relational, load full depth." Its helpfulness bias will suppress personality on most turns to be "efficient." This is the fox-henhouse problem: the entity being governed controls the governance mechanism. Structural injection eliminates this entirely.

### Static vs per-turn split

The static prompt (instructions + rules + quirks) is assembled once and passed to pydantic-ai's `Agent(system_prompt=...)`. It does not change between turns.

Personality is per-turn because it reads from `ctx.deps.personality` which is set on the `CoDeps` dataclass. pydantic-ai's `@agent.system_prompt` mechanism runs these functions before every model request and appends their output. This separation means the static prompt works without personality (when no role is configured) and personality composition stays isolated in `_composer.py`.

### Role is immutable within a session

Selected when a co instance is created and a session starts. `CoDeps.personality` is a flat scalar set once, read-only thereafter. Mode (see P3) is the only in-session override available to the user.

### File-driven personality, no Python dicts

Roles are defined by files (souls + traits), not Python dicts. Behaviors are defined by files, not TypedDicts. The folder structure is the schema.

### Every trait has content

All trait values map to behavior files with actual guidance. No trait is "label-only." This ensures every trait value has measurable behavioral effect.

### Personality modulates, never overrides

Personality shapes HOW rules are expressed. It NEVER weakens safety, approval gates, or factual accuracy.

### Role personality is not self-modifiable

Peers openclaw (agent writes to SOUL.md) and letta (agent edits own persona block via `core_memory_replace()`) allow the agent to mutate its own personality. Co does not. Risks: personality drift, loss of designed character coherence, unpredictable cross-session behaviour. The `## Learned Context` memories injected per turn already provide session-to-session adaptation without modifying the structural personality.

### No personality fragment composition

Peers TinyTroupe (fragment JSON files) and openclaw (workspace SOUL.md with no schema) support mixing trait values across roles. Co does not — traits are hardwired per role in `traits/{role}.md`. The current 4 roles cover the useful personality space for a CLI companion. If a custom combination is needed, creating `traits/custom.md` with any mix of existing behavior file values requires only one file and no Python changes.

---

## Config

| Setting | Source | Description |
|---------|--------|-------------|
| `personality` | `settings.json` or env var | Role name (e.g., "finch"). Validated against `VALID_PERSONALITIES` at config load |

`reasoning_depth` is **not** a config setting. It is session-only state on `CoDeps` — always starts at `"normal"`, mutated by `/depth` during the session, reset on next session. It represents the user's expressed intent about response depth, not a property of the personality.

---

## Prompt budget (measured)

| Component | Chars |
|-----------|-------|
| Static prompt (instructions + 5 rules) | ~4,936 |
| Counter-steering (from quirk file body) | 0-500 |
| Soul section (identity basis + anti-patterns + 5 behaviors + mandate) | ~1,900-3,100 |
| Dynamic: personality memories | 0-500 |
| Dynamic: date + shell + project instructions | ~100-500 |
| **Total (with personality)** | **~7,000-8,500** |
| **Total (without personality)** | **~5,000-5,500** |
| **Budget ceiling** | **8,500** |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/__init__.py` | `assemble_prompt()` — static prompt only (instructions + rules + quirks) |
| `co_cli/prompts/_manifest.py` | `PromptManifest` dataclass — audit trail for assembly |
| `co_cli/prompts/instructions.md` | Bootstrap identity sentence |
| `co_cli/prompts/model_quirks.py` | Quirk file loader, counter-steering, inference params |
| `co_cli/prompts/rules/01..05_*.md` | 5 behavioral rules in filename order |
| `co_cli/prompts/quirks/{provider}/{model}.md` | Model-specific quirk files (YAML frontmatter + body) |
| `co_cli/deps.py` | `CoDeps` dataclass — `personality` (config-backed), `reasoning_depth` (session-only, default `"normal"`) |
| `co_cli/prompts/_reasoning_depth_override.py` | `VALID_DEPTHS`, `_DEPTH_OVERRIDES` — user depth intent → trait override map; lives at the prompts layer because it is a prompt assembly concern, not a personality property |
| `co_cli/prompts/personalities/_composer.py` | `load_soul()`, `load_traits()`, `compose_personality(role, depth)`, `VALID_PERSONALITIES`; imports `_DEPTH_OVERRIDES` from `co_cli.prompts._reasoning_depth_override` |
| `co_cli/prompts/personalities/souls/*.md` | Role identity basis + voice fingerprint + `## Never` anti-pattern sections (1 per role, 4 roles) |
| `co_cli/prompts/personalities/traits/*.md` | Trait wiring (1 per role, 5 `key: value` lines) |
| `co_cli/prompts/personalities/behaviors/*.md` | Behavioral guidance (16 files, `{trait}-{value}.md` naming) |
| `co_cli/_profiles/*.md` | Role authoring reference (source asset, never runtime-loaded; 1 per role) |
| `co_cli/agent.py` | `get_agent()` — registers `@agent.system_prompt` functions including `add_personality` |
| `co_cli/tools/personality.py` | `_load_personality_memories()` — loads personality-context tagged memories |
| `co_cli/_history.py` | `_PERSONALITY_COMPACTION_ADDENDUM` — summarizer guard for personality moments |
| `co_cli/_commands.py` | Slash command registry — `_cmd_depth` handler + `"depth"` entry in `COMMANDS` (added by P3) |
| `co_cli/_debug_personality.py` | `run_debug_personality()` — diagnostic output for `co debug-personality`; reads `reasoning_depth` from `CoDeps` to show which trait files are active under the current depth |
| `co_cli/config.py` | `_validate_personality()` — validates role name against `VALID_PERSONALITIES` |
| `evals/eval_personality_adherence.py` | Personality adherence eval — 6 heuristic check types, majority-vote scoring |
| `evals/p2-personality_adherence.jsonl` | 20 test cases across 4 roles (friendly removed) |

---

## Implementation tasks

### Phase 1: File structure (DONE)

- [x] Create `souls/` folder with soul files for 4 roles (finch, jeff, terse, inquisitive)
- [x] Add identity basis opening to each soul file (who co is from that role's perspective)
- [x] Create `traits/` folder with wiring files for 4 roles
- [x] Create `behaviors/` folder with 16 behavior files (5 traits x 2-4 values)
- [x] Delete `friendly` role files: `souls/friendly.md`, `traits/friendly.md`, `_profiles/friendly.md`

### Phase 2: Rewrite composer (DONE)

- [x] Rewrite `_composer.py`: `load_soul(role)`, `load_traits(role)`, `compose_personality(role)`
- [x] Delete `_registry.py` (PRESETS dict, PersonalityPreset TypedDict)
- [x] Update `VALID_PERSONALITIES` to derive from `traits/` folder listing

### Phase 3: Move personality to per-turn injection (DONE)

- [x] Remove personality from `assemble_prompt()` — static prompt is instructions + rules + quirks only
- [x] Add `add_personality` as `@agent.system_prompt` function in `agent.py`

### Phase 4: Cleanup (DONE)

- [x] Delete `style/` folder (replaced by `behaviors/communication-*.md`)
- [x] Update `_debug_personality.py` to use new file structure
- [x] Update tests (`test_prompt_assembly.py`)

### Phase 5: Anti-patterns + adherence evals (DONE)

- [x] Add `## Never` sections to all 5 soul files with role-specific anti-patterns
- [x] Write `evals/eval_personality_adherence.py` + `evals/p2-personality_adherence.jsonl` (20 cases, heuristic scoring)
- [x] Remove `friendly` test cases from `evals/p2-personality_adherence.jsonl` (role deleted)

---

## P3: User Reasoning Depth Override

### Problem

Role is immutable within a session. The adoption mandate already instructs co to "match expression depth to context," but that is LLM inference from message content — it cannot read the user's current mental state or time budget. A user running to a meeting and a user starting a deep exploration may send identical messages. `reasoning_depth` lets the user publish their current intent explicitly, bypassing inference.

Peer precedent: soul.md (Tweet/Chat/Essay modes), aider (architect/editor/ask modes), opencode (build/plan/explore agents).

### What it is

`reasoning_depth` is a user-expressed session intent stored on `CoDeps`. It is not a personality property — the personality (role, soul, traits) is unchanged. It is a prompt assembly concern: at compose time, it overrides specific trait lookups in the behavior file selection step, so the assembled `## Soul` block reflects the user's desired depth without altering who co is.

**Separation of concerns:**
- `personalities/` — defines who co is (souls, traits, behaviors)
- `prompts/_reasoning_depth_override.py` — defines what the user can express about response depth
- `_composer.py` — applies the user's depth intent during personality assembly

### `_DEPTH_OVERRIDES` — defined in `co_cli/prompts/_reasoning_depth_override.py`

```python
VALID_DEPTHS: list[str] = ["quick", "normal", "deep"]

_DEPTH_OVERRIDES: dict[str, dict[str, str]] = {
    "quick": {
        "thoroughness": "minimal",   # suppress verification, rationale, step-by-step
        "curiosity": "reactive",     # answer what was asked; stop volunteering follow-up questions
    },
    "normal": {},                    # no overrides — role defaults apply unchanged
    "deep": {
        "thoroughness": "comprehensive",  # verify results, explain reasoning chain, surface edge cases
    },
}
```

Each value references an existing behavior file — no new files needed:
- `behaviors/thoroughness-minimal.md` ✓
- `behaviors/curiosity-reactive.md` ✓
- `behaviors/thoroughness-comprehensive.md` ✓

### Why these traits and not others

`quick` overrides two traits. Full analysis against finch's defaults:

| Trait | Finch default | Overridden in `quick`? | Reason |
|-------|--------------|------------------------|--------|
| `thoroughness` | comprehensive | **Yes → minimal** | Core depth control — skip verification, reasoning chain, rationale |
| `curiosity` | proactive | **Yes → reactive** | Proactive curiosity asks follow-up questions on every ambiguous input — directly opposes a user who wants a result, not questions back |
| `emotional_tone` | empathetic | No | Warmth is compatible with quick responses; its verbose expression is already suppressed by the thoroughness override |
| `communication` | balanced | No | Already concise-leaning — no conflict |
| `relationship` | mentor | No | Social dynamic, not verbosity; expression depth is governed by thoroughness and curiosity |

`deep` overrides only `thoroughness` — curiosity, communication, relationship, and emotional_tone already support depth at their role defaults. `deep` is a no-op for roles that already have `thoroughness: comprehensive` (finch, inquisitive) — depth cannot go higher than the role's ceiling.

`quick` is a no-op for `terse` — which already has `thoroughness: minimal` and `curiosity: reactive`. Depth cannot go lower than the role's floor.

Overriding `communication`, `relationship`, or `emotional_tone` would corrupt role identity for no behavioral gain. The goal is depth modulation within the role, not role replacement.

### Compose logic

```
compose_personality(role, depth="normal"):
    from co_cli.prompts._reasoning_depth_override import _DEPTH_OVERRIDES

    parts = []
    parts.append(load_soul(role))                      # identity basis + voice fingerprint + anti-patterns
    traits = load_traits(role)                         # fresh dict copy — safe to mutate
    traits.update(_DEPTH_OVERRIDES.get(depth, {}))     # apply user depth intent before file loading
    for trait_name, trait_value in traits.items():
        load behaviors/{trait_name}-{trait_value}.md   # picks up overridden values
        append to parts
    parts.append(_ADOPTION_MANDATE)
    return "## Soul\n\n" + join(parts, "\n\n")
```

### Files changed

| File | Change |
|------|--------|
| `co_cli/prompts/_reasoning_depth_override.py` | **New file** — `VALID_DEPTHS`, `_DEPTH_OVERRIDES` |
| `co_cli/deps.py` | Add `reasoning_depth: str = "normal"` after `personality` field |
| `co_cli/prompts/personalities/_composer.py` | Extend `compose_personality(role, depth="normal")`; import `_DEPTH_OVERRIDES` from `co_cli.prompts._reasoning_depth_override` |
| `co_cli/agent.py` | Pass `ctx.deps.reasoning_depth` as `depth` to `compose_personality` in `add_personality` |
| `co_cli/_commands.py` | Add `_cmd_depth` handler and `"depth"` entry in `COMMANDS` |

### `/depth` command

- `/depth` — prints current depth and lists valid values (`quick`, `normal`, `deep`)
- `/depth quick` — sets `ctx.deps.reasoning_depth = "quick"`, prints confirmation
- `/depth xyz` — prints error listing valid values
- `/depth <any>` when no personality is configured — sets the field but warns: "No personality configured — depth has no effect until a role is set"

`reasoning_depth` lives on `CoDeps` (not in message history), so it survives context compaction and remains active for the full session.

### Tests to add

```python
def test_compose_personality_quick_depth_overrides_thoroughness():
    composed = compose_personality("finch", "quick")
    assert "Minimal Thoroughness" in composed
    assert "Comprehensive Thoroughness" not in composed

def test_compose_personality_quick_depth_overrides_curiosity():
    composed = compose_personality("finch", "quick")
    assert "Reactive Curiosity" in composed
    assert "Proactive Curiosity" not in composed

def test_compose_personality_deep_depth_overrides_thoroughness():
    # jeff has thoroughness: standard — deep overrides to comprehensive
    composed = compose_personality("jeff", "deep")
    assert "Comprehensive Thoroughness" in composed

def test_compose_personality_deep_depth_noop_for_comprehensive_role():
    # finch already has thoroughness: comprehensive — deep changes nothing
    assert compose_personality("finch", "normal") == compose_personality("finch", "deep")

def test_compose_personality_normal_depth_uses_role_defaults():
    composed = compose_personality("finch", "normal")
    assert "Comprehensive Thoroughness" in composed
    assert "Proactive Curiosity" in composed

def test_compose_personality_default_depth_is_normal():
    assert compose_personality("finch") == compose_personality("finch", "normal")
```

### Implementation tasks

- [x] Create `co_cli/prompts/_reasoning_depth_override.py` with `VALID_DEPTHS` and `_DEPTH_OVERRIDES`
- [x] Add `reasoning_depth: str = "normal"` to `CoDeps` in `deps.py`
- [x] Extend `compose_personality(role, depth="normal")` in `_composer.py` — import `_DEPTH_OVERRIDES` from `co_cli.prompts._reasoning_depth_override`, call `traits.update(_DEPTH_OVERRIDES.get(depth, {}))` before the behavior file loop
- [x] Pass `ctx.deps.reasoning_depth` as `depth` to `compose_personality` in `agent.py`
- [x] Add `_cmd_depth` handler and register in `COMMANDS` in `_commands.py`
- [x] Add 6 test cases to `test_prompt_assembly.py`
- [x] Update `_debug_personality.py`: `run_debug_personality(depth)` accepts depth and shows which trait files are active under the current depth, with override annotations
