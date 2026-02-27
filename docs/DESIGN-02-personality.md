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
| **Static assembly** | `assemble_prompt()` in `prompts/__init__.py` | Once at agent creation | Soul seed (full static anchor: identity + Core + Never list) + behavioral policy: rules + model quirks |
| **Per-turn injection** | `@agent.system_prompt` functions in `agent.py` | Before every model call | Learned context + situational context |

Every co instance has a soul loaded — there is no soul-less mode. The soul seed is the first content the model sees in every context window. It is the complete static identity anchor — identity declaration, trait essence, and hard constraints — not a thin introduction. Task-specific behavioral guidance is loaded on demand via the `load_task_strategy` tool.

### Personality pipeline

```
souls/{role}/seed.md        → full static anchor:
                               identity declaration
                               Core: trait essence (4 one-line activations)
                               Never: hard constraints
                             placed first in static prompt via load_soul_seed()
                             loaded once at agent creation

load_task_strategy tool     → soul-specific strategy for active task type(s)
                               strategies/{role}/{task_type}.md
                               model calls this at the start of a new task
                               same task type, different guidance per soul
```

### Session state

One field on `CoDeps` controls personality composition at runtime:

| Field | Controls | Source | Default | Scope |
|-------|----------|--------|---------|-------|
| `personality` | Who co is (identity) | `CO_CLI_PERSONALITY` / config | `"finch"` | Immutable within session |

### Design invariants

These constraints govern every decision in the sections below:

1. **Soul is always loaded** — every co instance has a personality; there is no generic fallback identity
2. **Seed is the authority** — the expanded seed is the complete static anchor: identity, trait essence, and Never list. It is present in every context window. The model's first context is always the soul
3. **File structure is the schema** — roles and strategies are discovered by listing directories; no Python dicts, no hardcoded lists
4. **Never list in seed, not strategies** — negative constraints need system prompt authority; the seed is the one place guaranteed to be present in every context window
5. **Modulate, never override** — personality shapes HOW rules are expressed; it never weakens safety, approval gates, or factual accuracy

### Prompt layer map

```
┌─────────────────────────────────────────────────────────────────┐
│ Static system prompt  (assembled once at agent creation)        │
│                                                                 │
│   soul seed  (identity + Core + Never list — full anchor)       │
│   rules/01..05_*.md                                             │
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
│ On-demand tool context  (model-triggered)                       │
│                                                                 │
│   load_task_strategy       → strategies/{role}/{task_type}.md   │
│                              called at start of new tasks       │
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

Four `@agent.system_prompt` functions registered in `get_agent()` in `co_cli/agent.py`. pydantic-ai appends their return values to the static system prompt before every model call. Functions returning empty string contribute nothing.

| Function | Registration order | Condition | Content |
|----------|--------------------|-----------|---------|
| `add_current_date` | 1 | Always | `"Today is {date}."` |
| `add_shell_guidance` | 2 | Always | Shell approval hint |
| `add_project_instructions` | 3 | `.co-cli/instructions.md` exists | Project-specific instructions |
| `add_personality_memories` | 4 | `ctx.deps.personality` is set | `## Learned Context` section (top 5 personality-context memories by recency) |

The static prompt is assembled once and never re-read between turns; the per-turn functions read from `ctx.deps` on every call. Personality identity is carried entirely by the static seed — no per-turn personality injection.

### 2c. Personality: seed + strategy

`load_soul_seed(role)` in `co_cli/prompts/personalities/_composer.py` is called once in `get_agent()`. It reads `souls/{role}/seed.md` and places it at the top of the static system prompt.

`VALID_PERSONALITIES` is derived from `souls/` folder listing via `_discover_valid_personalities()` — lists directories that contain `seed.md`, no hardcoded list.

**Expanded seed structure** — each soul seed is the complete static identity anchor:

```
souls/{role}/seed.md
  identity declaration     "You are X — …"
  Core:                    4 one-line trait essence activations
  Never:                   hard constraint list (negative space)
```

The `Never:` list belongs in the seed, not in strategies: negative constraints degrade faster than positive ones over long context. The seed is the one place guaranteed to be present in every context window — Never constraints must be there.

**Task strategy tool** — `load_task_strategy` in `co_cli/tools/personality.py` is called by the model at the start of a new task. It reads `strategies/{role}/{task_type}.md` and returns the content as tool-result context.

```
load_task_strategy(task_types=["technical", "debugging"])
  →  reads strategies/{role}/technical.md
  →  reads strategies/{role}/debugging.md
  →  returns merged content as display string
```

6 task types per role:

| Token | When to call | Soul differentiation |
|-------|-------------|---------------------|
| `technical` | implementation, commands, file ops, tool chains | Medium — communication style differs |
| `exploration` | research, tradeoffs, open investigation | High — finch structures, jeff discovers |
| `debugging` | isolate fault, hypothesize, verify | Medium — both methodical, different voice |
| `teaching` | explain concepts, guide toward understanding | High — finch prepares, jeff explores together |
| `emotional` | user frustrated, stuck, or celebrating | Low — both empathetic, different warmth level |
| `quick` | direct answer, ≤2–3 sentences | Low — both concise, different tone |

Multiple types can be active simultaneously — `["technical", "debugging"]` for "why is this failing?".

**File layout:**

```
co_cli/prompts/personalities/
├── souls/
│   ├── finch/   seed.md  (identity + Core + Never)
│   └── jeff/    seed.md  (identity + Core + Never)
└── strategies/
    ├── finch/   technical.md  exploration.md  debugging.md
    │            teaching.md   emotional.md    quick.md
    └── jeff/    technical.md  exploration.md  debugging.md
                 teaching.md   emotional.md    quick.md
```

The folder structure is the schema — roles are discovered by listing `souls/` for directories with `seed.md`. Adding a role requires only files, no Python changes.

**Adding a new role** requires only files — no Python changes:
1. Write `souls/{name}/seed.md` — identity declaration + Core + Never list
2. Write `strategies/{name}/*.md` — 6 strategy files for the 6 task types
3. `VALID_PERSONALITIES` updates automatically from `souls/` folder listing

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
| Static: soul seed | ~400–600 | identity + Core + Never, assembled once |
| Static: 5 rules | ~4,800 | behavioral policy, assembled once |
| Static: counter-steering (quirk file body) | 0–500 | model-specific, when file exists |
| Per-turn: personality injection | 0 | removed — seed carries identity |
| Per-turn: personality memories | 0–500 | top-5 personality-context memories |
| Per-turn: date + shell hint + project instructions | ~100–500 | always present |
| **System prompt total** | **~5,300–6,900** | |

**Strategy context** (delivered as tool result — separate from system prompt):

| Component | Chars | Notes |
|-----------|-------|-------|
| Strategy files when called | ~200–600 | 1–3 files × ~200 chars; called ~3–5× per 20-turn session |

**Tool schemas** (JSON schema field in API call — separate from system prompt):

| Component | Chars |
|-----------|-------|
| 17 registered tool docstrings | ~8,400 |
| **Grand total per API call** | **~13,700–15,900** |

**Session overhead comparison (20-turn conversation):**

| Component | Before | After |
|-----------|--------|-------|
| Soul seed (static, once) | ~200–370 chars | ~400–600 chars |
| Per-turn personality injection | ~2,300–3,500 chars × 20 = ~46,000–70,000 | 0 chars |
| Strategy context | n/a | ~200–600 chars × 3–5 calls = ~600–3,000 total |

**Peer comparison** (system prompt only; tool schemas are separate in all systems):

| System | System prompt | Has personality |
|--------|--------------|-----------------|
| co | ~5,300–6,900 | Yes — expanded seed anchor + on-demand strategy |
| Gemini-CLI | ~18,000 | No — heavier operational/workflow guidance |
| aider (editblock mode) | ~4,500 | No — pure edit-format guidance |

The reduced per-turn budget comes from moving stable behavioral guidance (soul body + behavior files) out of system prompt injection and into the seed (stable identity) and on-demand strategy tool (task-specific context).

### 2g. Design decisions

**Expanded seed as static anchor.** The soul seed is the complete static identity anchor: identity declaration, distilled trait essence, and hard constraints. Placed first in the static system prompt, it is present in every context window. The model's first context is always the soul — not a generic label. The Never list lives in the seed because negative constraints degrade faster than positive ones in long context, and the seed is the one place guaranteed to be present.

**Identity in seed, strategy on demand.** Stable identity content (who the model is, hard constraints) lives in the static seed. Dynamic, task-shaped behavioral guidance is loaded via `load_task_strategy` at the start of a new task. The seed is authoritative configuration; strategy files are retrieved context the soul chose to load. This split prevents the fox-henhouse problem for identity (Never list is structural) while allowing task-relevant guidance to be delivered only when needed.

**Structural delivery for identity, not for all content.** The Never list and Core trait essence belong structurally in the seed — they need system prompt authority, not retrieval authority. Task-specific behavioral guidance (exploration approach, teaching style, debugging process) does not — it is relevant to specific tasks only and carries adequate authority as tool-result context when explicitly loaded.

**Role immutability within a session.** `CoDeps.personality` is set once at session start, read-only thereafter. This prevents personality drift within a conversation.

**Personality modulates, never overrides.** Personality shapes HOW rules are expressed — never weakens safety, approval gates, or factual accuracy. There is no adoption mandate or override framing: the soul IS the identity, not a layer on top of a generic baseline.

**No self-modification.** Peers openclaw (agent writes to SOUL.md) and letta (agent edits its own persona via `core_memory_replace()`) allow the agent to mutate its own personality. Co does not. `## Learned Context` memories already provide session-to-session adaptation without mutating structural files.

**No fragment composition.** The soul+strategy combination is hardwired per role in `souls/{role}/seed.md` and `strategies/{role}/`. A new role requires only creating those files — no Python changes.

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
| `co_cli/prompts/personalities/_composer.py` | `load_soul_seed(role)`, `VALID_PERSONALITIES` — sole entry points for personality composition |
| `co_cli/prompts/personalities/souls/{role}/seed.md` | Full static anchor: identity + Core trait essence + Never list (2 roles: finch, jeff) |
| `co_cli/prompts/personalities/strategies/{role}/{task_type}.md` | Soul-specific behavioral guidance per task type (12 files: 6 types × 2 roles) |
| `co_cli/agent.py` | `get_agent(personality=…)` — extracts soul seed, assembles static prompt, registers 4 `@agent.system_prompt` functions, registers `load_task_strategy` |
| `co_cli/tools/personality.py` | `load_task_strategy` tool + `_load_personality_memories()` helper |
| `co_cli/_history.py` | `_PERSONALITY_COMPACTION_ADDENDUM` — summarizer guard for personality moments |
| `co_cli/_commands.py` | Slash command registry and dispatch |
| `co_cli/config.py` | `_validate_personality()` — validates role name against `VALID_PERSONALITIES` |
| `evals/eval_personality_behavior.py` | Consolidated personality eval runner (single + multi-turn), majority vote, gates, JSON/MD/trace outputs |
| `evals/personality_behavior.jsonl` | Golden personality behavior cases (`id`, `personality`, `turns`, `checks_per_turn`) |
| `evals/_common.py` | Shared eval infrastructure: deps factory, settings passthrough, check engine, telemetry/trace parsing |
| `evals/personality_behavior-data.json` | Detailed eval output (auto-generated) |
| `evals/personality_behavior-result.md` | Human-readable eval report (auto-generated) |
| `evals/personality_behavior-trace-*.md` | Per-turn trace reports with model/tool/check internals (auto-generated) |
