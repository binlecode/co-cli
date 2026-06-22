# Co CLI — Prompt Assembly


Covers how `co-cli` shapes the prompt for each model request. Startup sequencing lives in [bootstrap.md](bootstrap.md); turn orchestration in [core-loop.md](core-loop.md); compaction mechanics in [compaction.md](compaction.md); memory (sessions, memory items, canon recall) in [memory.md](memory.md); tool registration in [tools.md](tools.md).

## 1. What & How

The agent has no persistent state in model weights. Each request is reconstructed from three layers with different lifecycles:

- **Static instructions** — assembled once at agent construction; never mutated during the session.
- **Dynamic instruction layers** — `@agent.instructions` callbacks evaluated fresh on every model request.
- **Message history** — transformed before every request by an ordered processor pipeline whose detailed behavior is owned by the relevant subsystem specs.

```mermaid
flowchart TD
    subgraph Build["agent construction (once)"]
        Static[build_base_instructions]
        MainAgent[build_orchestrator]
        Static --> MainAgent
    end

    subgraph PerRequest["per model request"]
        Dynamic["@agent.instructions callbacks"]
        Processors["history processors 1..4"]
        Model[model request]
        Dynamic --> Processors --> Model
    end

    subgraph ResumeRequest["approval resume (same turn)"]
        ResumeModel["deferred_tool_results path"]
        ResumeNote["SDK skips ModelRequestNode"]
        ResumeModel -. zero tokens .- ResumeNote
    end

    MainAgent --> PerRequest
    MainAgent --> ResumeRequest
```

## 2. Core Logic

### 2.1 Static Instruction Assembly

`build_orchestrator()` assembles `static_instructions` by calling each builder in `ORCHESTRATOR_SPEC.static_instruction_builders` in order — five thin closures, each taking `deps` and returning `str | None`. All evaluated once at agent construction:

1. **`_base_instructions_provider(deps)`** — wraps `build_base_instructions(deps.config)`: soul seed, mindsets, numbered rules (`co_cli/context/rules/NN_rule_id.md`), recency advisory. The numbered rules are the profile-agnostic **base** (shared intersection). Character memories and critique are NOT included here.
2. **`_model_profile_overlay_provider(deps)`** — wraps `build_profile_overlay(resolve_model_profile(deps.config.llm))`: the resolved model profile's **append-only overlay** (`co_cli/context/overlays/<profile>.md`), placed immediately after the base so the composed prompt is `base + overlay(profile)`. Append-only — the overlay only ADDS profile-specific prose; nothing in the base is filtered or removed. Returns `None` when the profile's overlay file is absent or empty. `overlays/weak_local.md` ships the weak-model scaffolding relocated out of the (now profile-agnostic) base — the intent taxonomy, act-this-turn Execution, sub-goal Completeness, over-planning calibration, error-recovery loop-prevention, and a conciseness reflex; `overlays/frontier.md` is absent today (a strong reasoner needs nothing beyond the neutral base), so the frontier composition reduces to base alone. `ModelProfile` is resolved from the configured provider (Ollama → `WEAK_LOCAL`, otherwise `FRONTIER`) in `co_cli/config/llm.py`.
3. **`_user_profile_provider(deps)`** — reads `deps.user_profile_path` (`~/.co-cli/USER.md`) once and wraps it in a `## USER PROFILE (who the user is)` block; gated on `deps.config.memory.user_profile_enabled`. Returns `None` when the flag is off or the file is empty, so an absent profile injects nothing. Snapshot-at-load, frozen for the session. See [memory.md](memory.md) §7.
4. **`_toolset_guidance_provider(deps)`** — wraps `build_toolset_guidance(deps.tool_catalog)`: tool-specific guidance blocks, each gated on the tool being present. Currently gated: `capabilities_check` → `CAPABILITIES_GUIDANCE`. Empty when no matching tools exist.
5. **`_personality_critique_provider(deps)`** — wraps `load_soul_critique(deps.config.personality)` and prefixes with `## Review lens` heading; appended last when a personality is configured and a critique file exists. Placed after operational guidance so the review frame wraps the complete prompt.

The parts are joined with `"\n\n"` and passed as the `instructions=` string to `Agent(...)`. The string is stable for the entire session — it never changes between turns. The skill manifest and deferred-tool awareness are NOT in this block — they live in per-turn instruction callbacks (§2.2) so that `skill_catalog` / `tool_catalog` mutations do not churn the cached prefix bytes.

Each personality role is fully self-contained under `souls/{role}/`. Adding a role requires only a new directory — no Python changes. Adding a tool-specific guidance block requires adding a constant to `co_cli/context/guidance.py` and a gate in `build_toolset_guidance`.

The numbered rule files (`co_cli/context/rules/NN_rule_id.md`) are authored as **low-inference reflexes** per `.agent_docs/rule-authoring-standard.md` — observable-cue triggers and single imperative actions rather than high-inference judgment calls, since the configured model under-executes metacognitive asks. That standard is the yardstick for every rule edit.

### 2.2 Dynamic Instruction Layers

Registered in `build_orchestrator()` (`co_cli/agent/build.py`) from `ORCHESTRATOR_SPEC.per_turn_instructions`, evaluated fresh per request:

| Layer | Condition | Content |
| --- | --- | --- |
| `safety_prompt` | doom loop or shell-error streak active | warning text injected into instructions context |
| `current_time_prompt` | always | current date and time string (`"Current time: Monday, April 28, 2026 08:13 AM"`) |
| `deferred_tool_awareness_prompt` | any `VisibilityPolicyEnum.DEFERRED` tools present | per-tool stub list (one `` - `name`: one-liner `` line per deferred tool) grouped by integration family — native primitives first with no sub-header, then each family under a `` `<label>` (load before use): `` sub-header (e.g. `Google Workspace`) — telling the model to load a tool via `tool_view` (by exact name) before calling it; wraps `build_deferred_tool_awareness_prompt(ctx.deps.tool_catalog)` |
| `skill_manifest_prompt` | `skill_catalog` non-empty | `<available_skills>` XML manifest of bundled + user-installed skills; wraps `render_skill_manifest(ctx.deps.skill_catalog, ctx.deps.skills_dir, ctx.deps.user_skills_dir)` |

These layers are **not** persisted into `message_history`. They are emitted as `InstructionPart(dynamic=True)` in registration order, joined by `\n\n` and appended after the static literal in the system prompt block — see §2.3 for how cache-aware providers separate them from the cached prefix.

**Signature-coherence invariant.** The instruction floor (rules, mindsets, toolset guidance) carries *behavioral triggers* — WHEN and WHY to use a capability — never a tool's *call signature* (its HOW). A signature lives in the tool's schema: on the cached prefix for `ALWAYS` tools, loaded on demand via `tool_view` for `DEFERRED` tools (whose floor presence is the one-line stub above, not a signature). Hard-coding a deferred tool's `name(args…)` syntax in rule or guidance prose is a defect on two counts — it re-encodes on the floor the schema cost deferral removed, and it instructs a direct call to a tool that is not callable until loaded (contradicting the deferred-load mechanic). `tests/test_instruction_floor_coupling.py` guards this: it derives the `DEFERRED` set live from `tool_catalog` and fails if any deferred tool's call signature appears in the assembled floor (`build_rules_block() + build_toolset_guidance(...)`).

### 2.3 Static vs Dynamic Split — Cache-Friendliness

pydantic-ai distinguishes static and dynamic instructions at the `InstructionPart` level: the literal passed to `Agent(instructions=...)` becomes one `InstructionPart(dynamic=False)`; each `@agent.instructions` callback becomes a separate `InstructionPart(dynamic=True)` in registration order. All parts are joined by `\n\n` and emitted as the system prompt block.

Cache-aware providers act on the static/dynamic flag:
- **Anthropic** (`pydantic_ai/models/anthropic.py`) places `cache_control` on the *last static* block when any dynamic part is present, leaving dynamic parts outside the cached prefix.
- **Ollama / llama.cpp** has no explicit `cache_control`, but the KV cache automatically reuses matching prefix bytes across consecutive requests. The static literal sits first; any per-turn variance lives in the dynamic suffix.

The cache-friendliness invariant therefore reduces to one rule: **content that can vary within a session MUST NOT be inside the literal `instructions=` string passed to `Agent(...)`**. It belongs in either:
- An `@agent.instructions` callback (becomes `dynamic=True`, kept outside the cached prefix), OR
- The message tail via a history processor (`[*messages, injection]`).

The skill manifest, deferred-tool awareness, safety warnings, and current time all use the `@agent.instructions` path. Audit every new static builder registration against this rule — anything reading `deps.skill_catalog`, `deps.tool_catalog`, or runtime state must live in the per-turn path.

### 2.4 History Processors And Dynamic Instructions

Pure-transformer processors run in this exact order (registered in `build_orchestrator()` from `ORCHESTRATOR_SPEC.history_processors`):

| Processor | Behavior |
| --- | --- |
| `dedup_tool_results` | collapses identical `(tool_name, content-hash)` returns in the pre-tail region into back-references pointing at the latest `tool_call_id` |
| `evict_old_tool_results` | content-clears tool returns older than the 5-most-recent per tool name; protects last user turn |
| `spill_largest_tool_results` | force-spills the largest unspilled `ToolReturnPart`s across the full message list when total tokens exceed `deps.spill_threshold_tokens`; cheap (non-LLM) per-request cap that runs before `proactive_window_processor`. See [compaction.md](compaction.md) §2.4. |
| `proactive_window_processor` | when history exceeds compaction threshold, replaces the middle with an LLM summary or static marker; full design in [compaction.md](compaction.md) |

Four dynamic instruction functions are registered via `agent.instructions()` and run before every model request:

| Dynamic instruction | Behavior |
| --- | --- |
| `safety_prompt` | detects identical-tool-call streaks and shell-error streaks; returns warning text injected into the instructions context |
| `current_time_prompt` | returns current date/time string at tail position — ephemeral grounding just before the model sees the user turn; keeps Block 0 cache-stable |
| `deferred_tool_awareness_prompt` | re-reads `ctx.deps.tool_catalog` each turn — newly registered deferred tools surface immediately without restart |
| `skill_manifest_prompt` | re-reads `ctx.deps.skill_catalog` each turn — newly created skills become visible to the model on the very next turn |

**Ordering rationale:**
- **#1–2 before #3–4**: dedup and eviction run before size enforcement and summarization. The summarizer sees a smaller, deduped history; size enforcement fires after cheap reductions but before the LLM call.
- **`safety_prompt` before `current_time_prompt`**: structural behavioral guidance sits above ephemeral grounding.
- **`deferred_tool_awareness_prompt` and `skill_manifest_prompt` last**: capability surfaces are the freshest layer — they reflect live `deps` state — and sit closest to the user turn so the model resolves "what can I call right now" against the most recent snapshot.
- **Dynamic instructions before model request**: these functions run via the SDK's `agent.instructions()` mechanism; their output is ephemeral — not stored back to `turn_state.current_history`.

### 2.5 Approval Resume

Approval resumes reuse the main agent with zero additional tokens. The pydantic-ai SDK skips `ModelRequestNode` entirely on the `deferred_tool_results` path, so the run continues from exactly where the deferred call paused. No separate resume agent is needed. Approval subject resolution and the resume loop live in [core-loop.md](core-loop.md) §2.3.

## 3. Config

Only the settings that directly shape prompt text are listed here. Compaction thresholds live in [compaction.md](compaction.md); recall parameters live in [memory.md](memory.md).

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `personality` | `CO_PERSONALITY` | `tars` | personality for static prompt assembly |
| `doom_loop_threshold` | `CO_DOOM_LOOP_THRESHOLD` | `3` | identical-tool-call streak for warning injection |
| `max_reflections` | `CO_MAX_REFLECTIONS` | `3` | shell-error streak for reflection-cap injection |

## 4. Public Interface

### Static instruction assembly

| Symbol | Source | Contract |
| --- | --- | --- |
| `build_base_instructions(config) -> str` | `co_cli/context/assembly.py` | Returns soul seed + mindsets + numbered rules, joined with `\n\n`; called once at agent construction |
| `build_toolset_guidance(tool_catalog) -> str` | `co_cli/context/guidance.py` | Returns tool-specific guidance blocks, gated on tool presence (`CAPABILITIES_GUIDANCE`) |
| `build_deferred_tool_awareness_prompt(tool_catalog) -> str` | `co_cli/tools/deferred_prompt.py` | Returns a per-tool stub list (one `` - `name`: one-line purpose `` per `DEFERRED` tool, name-only when description is empty) grouped by integration family: native primitives render first with no sub-header, then each family under a `` `<label>` (load before use): `` sub-header. Family key = segment before first `_` for native integrations (so all `google_*` cluster), whole string for MCP integrations; deterministic ordering. Empty when no deferred tools exist. Called per-turn via `deferred_tool_awareness_prompt` |
| `render_skill_manifest(skill_catalog, skills_dir, user_skills_dir) -> str` | `co_cli/context/manifests/skill_manifest.py` | Renders the `<available_skills>` XML block. Called per-turn via `skill_manifest_prompt` |

### Personality asset loaders

| Symbol | Source | Contract |
| --- | --- | --- |
| `load_soul_seed(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns the role's `seed.md` body |
| `load_soul_mindsets(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns the joined `## Mindsets` block from `mindsets/*.md` |
| `load_soul_critique(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns the optional `## Review lens` body |

### Dynamic per-request instructions

| Symbol | Source | Contract |
| --- | --- | --- |
| `safety_prompt(ctx) -> str` | `co_cli/agent/_instructions.py` | `@agent.instructions` — doom-loop / shell-error warning; output is ephemeral, not persisted to history |
| `current_time_prompt(ctx) -> str` | `co_cli/agent/_instructions.py` | `@agent.instructions` — current date/time string; ephemeral grounding |
| `deferred_tool_awareness_prompt(ctx) -> str` | `co_cli/agent/_instructions.py` | `@agent.instructions` — wraps `build_deferred_tool_awareness_prompt(ctx.deps.tool_catalog)`; live deferred-tool surface, not cached |
| `skill_manifest_prompt(ctx) -> str` | `co_cli/agent/_instructions.py` | `@agent.instructions` — wraps `render_skill_manifest(ctx.deps.skill_catalog, ...)`; live skill surface, not cached |

## 5. Files

| File | Purpose |
| --- | --- |
| `co_cli/agent/build.py` | `build_orchestrator()` — composes static instructions from `ORCHESTRATOR_SPEC.static_instruction_builders`, registers `per_turn_instructions` callbacks, attaches history processors |
| `co_cli/agent/orchestrator.py` | `ORCHESTRATOR_SPEC` — static builders (`_base_instructions_provider`, `_toolset_guidance_provider`, `_personality_critique_provider`) and per-turn instructions (`safety_prompt`, `current_time_prompt`, `deferred_tool_awareness_prompt`, `skill_manifest_prompt`) |
| `co_cli/agent/_instructions.py` | per-turn instruction callbacks: `safety_prompt`, `current_time_prompt`, `deferred_tool_awareness_prompt`, `skill_manifest_prompt` |
| `co_cli/context/assembly.py` | `build_base_instructions()` — soul + mindsets + rules; rule-file validation |
| `co_cli/context/guidance.py` | `CAPABILITIES_GUIDANCE` constant; `build_toolset_guidance()` — gated on tool presence |
| `co_cli/context/manifests/skill_manifest.py` | `render_skill_manifest()` — `<available_skills>` XML block; called per-turn from `skill_manifest_prompt` |
| `co_cli/personality/prompts/loader.py` | `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets` — personality asset loaders |
| `co_cli/personality/prompts/validator.py` | personality discovery and file validation |
| `co_cli/context/prompt_text.py` | `safety_prompt_text` — called via `agent.instructions()` wrapper in `co_cli/agent/_instructions.py` |
| `co_cli/tools/deferred_prompt.py` | `build_deferred_tool_awareness_prompt()` — per-tool stub list (name + one-liner) for deferred tools, grouped by integration family under sub-headers; called per-turn from `deferred_tool_awareness_prompt` |
