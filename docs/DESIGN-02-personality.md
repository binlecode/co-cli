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
| **Static assembly** | `assemble_prompt()` in `prompts/__init__.py` | Once at agent creation | Soul seed (identity declaration) + behavioral policy: rules + model quirks |
| **Per-turn injection** | `@agent.system_prompt` functions in `agent.py` | Before every model call | Full soul body + behaviors + learned context + situational context |

Every co instance has a soul loaded — there is no soul-less mode. The soul seed (identity declaration) is the first content the model sees in every context window. Per-turn injection reinforces it with behavioral detail.

### Personality composition pipeline

`compose_personality(role)` takes a role (who co is) and produces the full `## Soul` block for per-turn injection:

```
compose_personality(role)
  │
  └── role  → souls/{role}/body.md     soul body: ## Never, ## Voice, anti-patterns
            → traits/{role}.md         4 trait:value pairs   [session-immutable]
            → behaviors/{k}-{v}.md     one file per active trait value
  ──────────────────────────────────────────────────────────────────────────────────
  → ## Soul block  injected into system prompt each turn
```

### Session state

One field on `CoDeps` controls personality composition at runtime:

| Field | Controls | Source | Default | Scope |
|-------|----------|--------|---------|-------|
| `personality` | Who co is (identity) | `CO_CLI_PERSONALITY` / config | `"finch"` | Immutable within session |

### Design invariants

These constraints govern every decision in the sections below:

1. **Soul is always loaded** — every co instance has a personality; there is no generic fallback identity
2. **Soul seed anchors static context** — the identity declaration is placed first in the static system prompt via `get_agent(personality=…)`; the model's first context is always the soul
3. **File structure is the schema** — roles, traits, and behaviors are discovered by listing directories; no Python dicts, no hardcoded lists
4. **Structural delivery** — personality is injected by the framework on every turn; the LLM never requests it via tool
5. **Traits are role-hardwired** — no mix-and-match fragment composition; a custom combination requires one new traits file, no Python changes
6. **Modulate, never override** — personality shapes HOW rules are expressed; it never weakens safety, approval gates, or factual accuracy

### Prompt layer map

```
┌─────────────────────────────────────────────────────────────────┐
│ Static system prompt  (assembled once at agent creation)        │
│                                                                 │
│   soul seed  (identity declaration — "You are X…")             │
│   rules/01..05_*.md                                             │
│   quirks/{provider}/{model}.md  (when file exists for model)    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Per-turn layers  (@agent.system_prompt functions in agent.py)   │
│   (appended in registration order)                              │
│                                                                 │
│   add_current_date     → today's date                           │
│   add_shell_guidance   → shell approval hint                    │
│   add_personality      → ## Soul block  (when role is set)      │
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

`assemble_prompt(provider, model_name, soul_seed)` in `co_cli/prompts/__init__.py` is called once in `get_agent()`. It builds the static prompt from the soul seed, rules, and quirks.

```
if soul_seed provided:
    prepend soul_seed                          ← identity first
for each rules/*.md in NN_ numeric order:
    validate filename format (NN_rule_id.md)
    validate order is contiguous from 01, no duplicates
    append content
if quirk file exists for provider/model:
    append "## Model-Specific Guidance\n\n" + body
join all parts with "\n\n"
```

`get_agent()` extracts the soul seed via `load_soul_seed(personality)` and passes it to `assemble_prompt()`. The soul seed is read directly from `souls/{role}/seed.md` — no runtime parsing.

Rule file validation is strict: filenames must match `NN_rule_id.md`, numeric prefixes must be unique and contiguous starting at 01. Assembly fails with `ValueError` on violations. `PromptManifest` tracks `parts_loaded` names, `total_chars`, and `warnings` for diagnostics.

**Behavioral rules** — five rule files define co's behavioral policy. Rules are cross-cutting principles; tool-specific guidance lives in tool docstrings, not rules. Target budget: < 1,100 tokens total across all 5 rules.

| Rule | File | Governs |
|------|------|---------|
| **01 Identity** | `01_identity.md` | Relationship continuity, anti-sycophancy, thoroughness over speed |
| **02 Safety** | `02_safety.md` | Credential protection, source control caution, approval philosophy, memory constraints |
| **03 Reasoning** | `03_reasoning.md` | Verification-first; fact authority: tool output beats training data, user preference beats tool output; two kinds of unknowns: discoverable facts vs preferences |
| **04 Tool Protocol** | `04_tool_protocol.md` | 8–12 word preamble before tool calls; bias toward action; parallel when independent, sequential when dependent |
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

Five `@agent.system_prompt` functions registered in `get_agent()` in `co_cli/agent.py`. pydantic-ai appends their return values to the static system prompt before every model call. Functions returning empty string contribute nothing.

| Function | Registration order | Condition | Content |
|----------|--------------------|-----------|---------|
| `add_current_date` | 1 | Always | `"Today is {date}."` |
| `add_shell_guidance` | 2 | Always | Shell approval hint |
| `add_personality` | 3 | `ctx.deps.personality` is set | `compose_personality(role)` — full `## Soul` block |
| `add_project_instructions` | 4 | `.co-cli/instructions.md` exists | Project-specific instructions |
| `add_personality_memories` | 5 | `ctx.deps.personality` is set | `## Learned Context` section (top 5 personality-context memories by recency) |

The static prompt is assembled once and never re-read between turns; the per-turn functions read from `ctx.deps` on every call. Personality composition stays isolated in `_composer.py`.

### 2c. Personality composition

`compose_personality(role)` in `co_cli/prompts/personalities/_composer.py`. Called by `add_personality()` before every model request.

```
load souls/{role}/body.md      → soul body: ## Never, ## Voice, anti-patterns
load traits/{role}.md          → parse "key: value" lines into fresh dict
for each key, value in traits dict:
    load behaviors/{key}-{value}.md   → behavioral guidance
concatenate: "## Soul\n\n" + body + behaviors
```

`load_traits()` always returns a freshly constructed `dict[str, str]` — never a cached reference. `VALID_PERSONALITIES` is derived from `traits/` folder listing via `_discover_valid_personalities()` — no hardcoded list.

**Soul file structure** — each soul is split into two files in its own subfolder:

```
souls/{role}/
├── seed.md   identity declaration ("You are X — …")
│             placed at top of static prompt via load_soul_seed()
│             loaded once at agent creation, never repeated per-turn
│
└── body.md   ## Never, ## Voice, and other behavioral detail sections
              loaded per-turn into ## Soul block via compose_personality()
```

Each soul `body.md` contains a `## Never` section listing role-specific anti-patterns; this mirrors peer precedent (TinyTroupe `dislikes` field, openclaw SOUL.md boundary statements). `load_soul(role)` combines both files and is used by diagnostic tools only — the runtime path always loads seed and body separately.

**File layout:**

```
co_cli/prompts/personalities/
├── souls/
│   ├── finch/   seed.md + body.md
│   └── jeff/    seed.md + body.md
├── traits/      key: value wiring per role (1 per role)
└── behaviors/   behavioral guidance ({trait}-{value}.md, 13 files)

co_cli/_profiles/
└── {role}.md    authoring reference (never runtime-loaded)
```

The folder structure is the schema — behaviors, traits, and souls are discovered by listing directories, not declared in Python. Adding a role or trait value requires zero code changes.

**4 traits** — grounded in Big Five personality research, mapped to CLI companion context:

| Trait | Values | Controls | Big Five mapping |
|-------|--------|----------|-----------------|
| `communication` | terse, balanced, warm, educational | Verbosity, formality, explanation depth | Extraversion |
| `relationship` | mentor, peer, companion, professional | Social dynamic with user | *(no equivalent)* |
| `curiosity` | proactive, reactive | Initiative, follow-up questions | Openness |
| `emotional_tone` | empathetic, neutral, analytical | Warmth vs objectivity | Agreeableness |

Thoroughness is task-scoped behavior calibrated from request shape and conversation context — not a personality trait. The soul already expresses depth preference through identity and voice. Neuroticism (Big Five #5) is not applicable to AI assistants.

**2 roles and their trait wiring:**

| Role | communication | relationship | curiosity | emotional_tone |
|------|--------------|--------------|-----------|----------------|
| finch | balanced | mentor | proactive | empathetic |
| jeff | warm | peer | proactive | empathetic |

All trait values map to behavior files with actual guidance — no trait is label-only. This invariant ensures every trait value has a measurable behavioral effect.

**Adding a new role** requires only files — no Python changes:
1. Write `souls/{name}/seed.md` — identity declaration ("You are X…"), voice fingerprint
2. Write `souls/{name}/body.md` — `## Never` anti-patterns, optional `## Voice` section
3. Write `traits/{name}.md` — pick values from existing behaviors
4. `VALID_PERSONALITIES` updates automatically from `traits/` folder listing
5. Optionally write `co_cli/_profiles/{name}.md` — authoring reference (never runtime-loaded)

**Adding a new trait value** requires only one file — write `behaviors/{trait}-{value}.md`.

### 2d. Personality memories

`_load_personality_memories()` in `co_cli/tools/personality.py`. Called by `add_personality_memories()` per turn.

```
scan .co-cli/knowledge/memories/*.md for tag "personality-context"
sort by updated (or created) descending
take top 5
format as "## Learned Context\n\n- {content}\n- {content}\n..."
```

Returns empty string if no matching memories exist or the directory is absent. Provides session-to-session adaptation without modifying structural personality files.

### 2e. Compaction guard

When history is summarized, `_PERSONALITY_COMPACTION_ADDENDUM` in `co_cli/_history.py` is appended to the summarizer prompt when `personality_active=True`. It instructs the summarizer to preserve:
- Personality-reinforcing moments (emotional exchanges, humor, relationship dynamics)
- User reactions that shaped tone or communication style
- Explicit personality preferences or corrections from the user

Without this guard, compaction would lose relational context that makes personality feel continuous across long sessions.

### 2f. Prompt budget (measured)

Tool descriptions are delivered as JSON schema in the API call body — they never consume system prompt budget. Both delivery channels are shown below for a complete per-call picture.

**System prompt** (string field — `Agent(system_prompt=…)` + per-turn `@agent.system_prompt` functions):

| Component | Chars | Notes |
|-----------|-------|-------|
| Static: soul seed | ~200–370 | identity declaration, assembled once |
| Static: 5 rules | ~4,800 | behavioral policy, assembled once |
| Static: counter-steering (quirk file body) | 0–500 | model-specific, when file exists |
| Per-turn: soul body + 4 behaviors | ~2,300–3,500 | varies by role (finch: ~2,300, jeff: ~3,500) |
| Per-turn: personality memories | 0–500 | top-5 personality-context memories |
| Per-turn: date + shell hint + project instructions | ~100–500 | always present |
| **System prompt total** | **~7,400–9,700** | |

**Tool schemas** (JSON schema field in API call — separate from system prompt):

| Component | Chars |
|-----------|-------|
| 16 registered tool docstrings | ~8,200 |
| **Grand total per API call** | **~15,800–18,100** |

**Peer comparison** (system prompt only; tool schemas are separate in all systems):

| System | System prompt | Has personality |
|--------|--------------|-----------------|
| co | ~7,400–9,700 | Yes — soul seed + soul body + trait behaviors |
| Gemini-CLI | ~18,000 | No — heavier operational/workflow guidance |
| aider (editblock mode) | ~4,500 | No — pure edit-format guidance |

The static + soul split is not a budget imbalance against tooling — tool schemas occupy a separate delivery channel and do not compete with personality for system prompt space. Co's system prompt is compact relative to gemini-cli while adding personality that peers omit entirely.

### 2g. Design decisions

**Soul seed anchors static context.** The identity declaration ("You are X…") is placed first in the static system prompt. The model's first context is always the soul — not a generic label, not a framework preamble. The full soul body and behaviors reinforce it per turn.

**Structural delivery, not voluntary.** All personality content is in the system prompt on every turn — the LLM never requests it via a tool. If personality were tool-gated, the LLM's helpfulness bias would suppress it on most turns to be "efficient" (fox-henhouse problem). Structural injection eliminates this entirely.

**Role immutability within a session.** `CoDeps.personality` is set once at session start, read-only thereafter. This prevents personality drift within a conversation.

**Personality modulates, never overrides.** Personality shapes HOW rules are expressed — never weakens safety, approval gates, or factual accuracy. There is no adoption mandate or override framing: the soul IS the identity, not a layer on top of a generic baseline.

**No self-modification.** Peers openclaw (agent writes to SOUL.md) and letta (agent edits its own persona via `core_memory_replace()`) allow the agent to mutate its own personality. Co does not. `## Learned Context` memories already provide session-to-session adaptation without mutating structural files.

**No fragment composition.** Traits are hardwired per role in `traits/{role}.md`. If a custom combination is needed, creating `traits/custom.md` requires only one file and no Python changes.

### 2h. Personality behavior evals

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
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Role name — validated against `VALID_PERSONALITIES` at config load time by `_validate_personality()` in `config.py` |

Eval CLI flags are documented by the runner itself:
`uv run python evals/eval_personality_behavior.py --help`

---

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/__init__.py` | `assemble_prompt(provider, model_name, soul_seed)` — static prompt: seed + rules + quirks |
| `co_cli/prompts/_manifest.py` | `PromptManifest` dataclass — audit trail for prompt assembly |
| `co_cli/prompts/model_quirks.py` | Quirk file loader, counter-steering, inference params |
| `co_cli/prompts/rules/01..05_*.md` | 5 behavioral rules in filename order |
| `co_cli/prompts/quirks/{provider}/{model}.md` | Model-specific quirk files (YAML frontmatter + body) |
| `co_cli/deps.py` | `CoDeps` dataclass — `personality` (config-backed) |
| `co_cli/prompts/personalities/_composer.py` | `load_soul()`, `load_soul_seed()`, `load_traits()`, `compose_personality(role)`, `VALID_PERSONALITIES` |
| `co_cli/prompts/personalities/souls/{role}/seed.md` | Identity declaration — placed first in static prompt (2 roles: finch, jeff) |
| `co_cli/prompts/personalities/souls/{role}/body.md` | Soul body: `## Never` anti-patterns + optional `## Voice` section (per-turn only) |
| `co_cli/prompts/personalities/traits/*.md` | Trait wiring (1 per role, 4 `key: value` lines each) |
| `co_cli/prompts/personalities/behaviors/*.md` | Behavioral guidance (13 files, `{trait}-{value}.md` naming) |
| `co_cli/_profiles/*.md` | Role authoring reference (source asset, never runtime-loaded; 1 per role) |
| `co_cli/agent.py` | `get_agent(personality=…)` — extracts soul seed, assembles static prompt, registers 5 `@agent.system_prompt` functions |
| `co_cli/tools/personality.py` | `_load_personality_memories()` — loads personality-context tagged memories |
| `co_cli/_history.py` | `_PERSONALITY_COMPACTION_ADDENDUM` — summarizer guard for personality moments |
| `co_cli/_commands.py` | Slash command registry and dispatch |
| `co_cli/config.py` | `_validate_personality()` — validates role name against `VALID_PERSONALITIES` |
| `evals/eval_personality_behavior.py` | Consolidated personality eval runner (single + multi-turn), majority vote, gates, JSON/MD/trace outputs |
| `evals/personality_behavior.jsonl` | Golden personality behavior cases (`id`, `personality`, `turns`, `checks_per_turn`) |
| `evals/_common.py` | Shared eval infrastructure: deps factory, settings passthrough, check engine, telemetry/trace parsing |
| `evals/personality_behavior-data.json` | Detailed eval output (auto-generated) |
| `evals/personality_behavior-result.md` | Human-readable eval report (auto-generated) |
| `evals/personality_behavior-trace-*.md` | Per-turn trace reports with model/tool/check internals (auto-generated) |
