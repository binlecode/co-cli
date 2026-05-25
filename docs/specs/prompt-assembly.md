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
        Static[build_static_instructions]
        MainAgent[build_orchestrator]
        Static --> MainAgent
    end

    subgraph PerRequest["per model request"]
        Dynamic["@agent.instructions callbacks"]
        Processors["history processors 1..5"]
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

1. **`_static_instructions_provider(deps)`** — wraps `build_static_instructions(deps.config)`: soul seed, mindsets, numbered rules (`co_cli/context/rules/NN_rule_id.md`), recency advisory. Character memories and critique are NOT included here.
2. **`_toolset_guidance_provider(deps)`** — wraps `build_toolset_guidance(deps.tool_index)`: tool-specific guidance blocks, each gated on the tool being present. Currently gated: `memory_search` / `session_search` → `MEMORY_GUIDANCE`; `capabilities_check` → `CAPABILITIES_GUIDANCE`. Empty when no matching tools exist.
3. **`_category_awareness_provider(deps)`** — wraps `build_category_awareness_prompt(deps.tool_index)`: single-sentence category-level hint listing deferred tool categories reachable via `search_tools`. Derived from `VisibilityPolicyEnum.DEFERRED` entries. Empty when no deferred tools exist.
4. **`_skill_manifest_provider(deps)`** — wraps `render_skill_manifest(deps.skill_index, deps.skills_dir, deps.user_skills_dir)`: bundled-skill manifest. Empty when no skills are loaded.
5. **`_personality_critique_provider(deps)`** — wraps `load_soul_critique(deps.config.personality)` and prefixes with `## Review lens` heading; appended last when a personality is configured and a critique file exists. Placed after operational guidance so the review frame wraps the complete prompt.

The parts are joined with `"\n\n"` and passed as the `instructions=` string to `Agent(...)`. The string is stable for the entire session — it never changes between turns.

Each personality role is fully self-contained under `souls/{role}/`. Adding a role requires only a new directory — no Python changes. Adding a tool-specific guidance block requires adding a constant to `co_cli/context/guidance.py` and a gate in `build_toolset_guidance`.

### 2.2 Dynamic Instruction Layers

Registered in `build_orchestrator()` (`co_cli/agent/build.py`) from `ORCHESTRATOR_SPEC.per_turn_instructions`, evaluated fresh per request:

| Layer | Condition | Content |
| --- | --- | --- |
| `safety_prompt` | doom loop or shell-error streak active | warning text injected into instructions context |
| `current_time_prompt` | always | current date and time string (`"Current time: Monday, April 28, 2026 08:13 AM"`) |

These layers are **not** persisted into `message_history`.

### 2.3 Append-only Invariant for Dynamic Content

Any content that can vary within a single session MUST be appended to the tail of the message list via a history processor that returns `[*messages, injection]`. It MUST NOT be placed in `@agent.instructions`.

**Rationale:** `@agent.instructions` output is concatenated into the static system-prompt block pydantic-ai sends to the provider. Providers cache the system-prompt block as the prefix of every request. Any per-request variance in that block invalidates the cache for the entire prefix, including fixed tool schemas and soul assets.

New dynamic surfaces go in the tail. Audit every new `@agent.instructions` registration against this rule. The current date/time is injected via `current_time_prompt` — a per-turn callback that lands in Block 1 (non-cached, tiny), keeping Block 0 cache-stable.

### 2.4 History Processors And Dynamic Instructions

Pure-transformer processors run in this exact order (registered in `build_orchestrator()` from `ORCHESTRATOR_SPEC.history_processors`):

| Processor | Behavior |
| --- | --- |
| `dedup_tool_results` | collapses identical `(tool_name, content-hash)` returns in the pre-tail region into back-references pointing at the latest `tool_call_id` |
| `evict_old_tool_results` | content-clears tool returns older than the 5-most-recent per tool name; protects last user turn |
| `enforce_request_size` | force-spills the largest unspilled `ToolReturnPart`s across the full message list when total tokens exceed `deps.spill_threshold_tokens`; cheap (non-LLM) per-request cap that runs before `proactive_window_processor`. See [compaction.md](compaction.md) §2.4. |
| `proactive_window_processor` | when history exceeds compaction threshold, replaces the middle with an LLM summary or static marker; full design in [compaction.md](compaction.md) |
| `sanitize_surrogate_codepoints` | replaces lone Unicode surrogates (U+D800–U+DFFF) with U+FFFD across all parts; guards against `UnicodeEncodeError` |

Two dynamic instruction functions are registered via `agent.instructions()` and run before every model request:

| Dynamic instruction | Behavior |
| --- | --- |
| `safety_prompt` | detects identical-tool-call streaks and shell-error streaks; returns warning text injected into the instructions context |
| `current_time_prompt` | returns current date/time string at tail position — ephemeral grounding just before the model sees the user turn; keeps Block 0 cache-stable |

**Ordering rationale:**
- **#1–2 before #3–4**: dedup and eviction run before size enforcement and summarization. The summarizer sees a smaller, deduped history; size enforcement fires after cheap reductions but before the LLM call.
- **#5 last**: surrogate sanitization runs after `proactive_window_processor` so the summary text it produces is also swept.
- **`safety_prompt` before `current_time_prompt`**: structural behavioral guidance sits above ephemeral grounding. `current_time_prompt` is at the tail — the last thing the model sees before the user turn — because ephemeral grounding is most effective close to the user message.
- **Dynamic instructions before model request**: these functions run via the SDK's `agent.instructions()` mechanism; their output is ephemeral — not stored back to `turn_state.current_history`.

### 2.5 Approval Resume

Approval resumes reuse the main agent with zero additional tokens. The pydantic-ai SDK skips `ModelRequestNode` entirely on the `deferred_tool_results` path, so the segment continues from exactly where the deferred call paused. No separate resume agent is needed. Approval subject resolution and the resume loop live in [core-loop.md](core-loop.md) §2.3.

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
| `build_static_instructions(config) -> str` | `co_cli/context/assembly.py` | Returns soul seed + mindsets + numbered rules + `RECENCY_CLEARING_ADVISORY`, joined with `\n\n`; called once at agent construction |
| `RECENCY_CLEARING_ADVISORY` | `co_cli/context/assembly.py` | Module-level constant — "## Tool result recency" paragraph appended last to the static prompt |
| `build_toolset_guidance(tool_index) -> str` | `co_cli/context/guidance.py` | Returns tool-specific guidance blocks, gated on tool presence (`MEMORY_GUIDANCE`, `CAPABILITIES_GUIDANCE`) |
| `build_category_awareness_prompt(tool_index) -> str` | `co_cli/tools/deferred_prompt.py` | Returns a single-sentence category-level hint for `DEFERRED` tools; empty when no deferred tools exist |
| `render_skill_manifest(skill_index, skills_dir, user_skills_dir) -> str` | `co_cli/context/manifests/skill_manifest.py` | Renders the `<available_skills>` XML block injected after tool guidance |

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
| `current_time_prompt(ctx) -> str` | `co_cli/agent/_instructions.py` | `@agent.instructions` — current date/time string at tail position; ephemeral grounding |

## 5. Files

| File | Purpose |
| --- | --- |
| `co_cli/agent/core.py` | main-agent and delegation-agent construction; history-processor and instruction registration |
| `co_cli/agent/_instructions.py` | per-turn instruction callbacks: `current_time_prompt`, `safety_prompt` |
| `co_cli/context/assembly.py` | `build_static_instructions()` — soul + mindsets + rules + recency advisory; rule-file validation |
| `co_cli/context/guidance.py` | `MEMORY_GUIDANCE`, `CAPABILITIES_GUIDANCE` constants; `build_toolset_guidance()` — gated on tool presence |
| `co_cli/personality/prompts/loader.py` | `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets` — personality asset loaders |
| `co_cli/personality/prompts/validator.py` | personality discovery and file validation |
| `co_cli/context/prompt_text.py` | `safety_prompt_text` — called via `agent.instructions()` wrapper in `agents/_instructions.py` |
| `co_cli/tools/deferred_prompt.py` | `build_category_awareness_prompt()` — category-level hint for deferred tool categories; called at build time |
