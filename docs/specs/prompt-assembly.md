# Co CLI — Prompt Assembly

## Product Intent

**Goal:** Own how instruction layers, dynamic instruction callbacks, and history processors combine to produce the outbound model request on every turn.
**Functional areas:**
- Static instruction assembly (soul + rules + examples + critique)
- Dynamic instruction callbacks (`@agent.instructions`)
- Three registered history processors (truncate, compact, summarize) plus two preflight callables (safety injection, recall injection)
- Append-only invariant for dynamic content (cache hygiene)
- Approval resume reusing the main agent

**Non-goals:**
- Compaction internals (owned by [compaction.md](compaction.md))
- Session transcript storage (owned by [session.md](session.md))
- Cognitive artifact schema and retrieval (owned by [cognition.md](cognition.md))
- Provider wire format past the pydantic-ai SDK boundary

**Success criteria:** Prompt-prefix cache hit rate preserved across turns; dynamic content appended to the tail, never woven into `@agent.instructions`; approval resumes add zero new tokens.
**Status:** Stable
**Known gaps:** None.

---

Covers how `co-cli` shapes the prompt for each model request. Startup sequencing lives in [bootstrap.md](bootstrap.md); turn orchestration in [core-loop.md](core-loop.md); compaction mechanics in [compaction.md](compaction.md); transcript persistence in [session.md](session.md); knowledge retrieval internals in [cognition.md](cognition.md); tool registration in [tools.md](tools.md).

## 1. What & How

The agent has no persistent state in model weights. Each request is reconstructed from three layers with different lifecycles:

- **Static instructions** — assembled once at agent construction; never mutated during the session.
- **Dynamic instruction layers** — `@agent.instructions` callbacks evaluated fresh on every model request.
- **Message history** — transformed before every request by five ordered history processors.

```mermaid
flowchart TD
    subgraph Build["agent construction (once)"]
        Static[build_static_instructions]
        MainAgent[build_agent]
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

`build_static_instructions()` (in `co_cli/prompts/_assembly.py`) assembles one literal string in fixed order:

1. Soul seed from `souls/{role}/seed.md`
2. Character memories from `co_cli/prompts/personalities/souls/{role}/memories/*.md` (read-only system assets)
3. Mindsets from `co_cli/prompts/personalities/souls/{role}/mindsets/{task_type}.md`
4. Numbered rules from `co_cli/prompts/rules/NN_rule_id.md` (contiguous from 01, unique prefixes, fail-fast validation)
5. Examples from `souls/{role}/examples.md` (optional)
6. Critique appended as `## Review lens` (optional)

Each personality role is fully self-contained under `souls/{role}/`. Adding a role requires only a new directory — no Python changes.

### 2.2 Dynamic Instruction Layers

Registered in `build_agent()` (`co_cli/agent/_core.py`), evaluated fresh per request:

| Layer | Condition | Content |
| --- | --- | --- |
| `add_shell_guidance` | always | shell approval/reminder text |
| `add_category_awareness_prompt` | deferred tools registered in tool_index | category-level prompt listing available capabilities via `search_tools` (~100 tokens) |

These layers are **not** persisted into `message_history`.

### 2.3 Append-only Invariant for Dynamic Content

Any content that can vary within a single session MUST be appended to the tail of the message list via a history processor that returns `[*messages, injection]`. It MUST NOT be placed in `@agent.instructions`.

**Rationale:** `@agent.instructions` output is concatenated into the static system-prompt block pydantic-ai sends to the provider. Providers cache the system-prompt block as the prefix of every request. Any per-request variance in that block invalidates the cache for the entire prefix, including fixed tool schemas and soul assets.

New dynamic surfaces go in the tail. Audit every new `@agent.instructions` registration against this rule. The current date and `personality-context` memories both live in `build_recall_injection` (preflight) for this reason — the date can change at midnight, and personality-context memories can change mid-session.

### 2.4 History Processors And Preflight

Three pure-transformer processors run in this exact order (registered in `build_agent()`):

| Processor | Behavior |
| --- | --- |
| `truncate_tool_results` | clears older `ToolReturnPart` content per tool type; keeps 5 most recent per type; always protects last user turn |
| `compact_assistant_responses` | caps older `TextPart`/`ThinkingPart` to 2,500 chars with 20/80 head/tail retention; uses `_find_last_turn_start()` boundary, not turn grouping |
| `summarize_history_window` | when history exceeds compaction threshold, replaces the middle with an LLM summary or static marker; full three-mechanism design in [compaction.md](compaction.md) |

Two preflight callables run before each model-bound segment (called explicitly by `run_turn()` — not registered as processors):

| Preflight | Behavior |
| --- | --- |
| `build_safety_injection` | detects identical-tool-call streaks and shell-error streaks; returns injection messages and flags (caller writes flags to `deps.runtime.safety_state`) |
| `build_recall_injection` | on every model-bound segment: appends current date and `personality-context` memories; once per new user turn: also appends top-3 recalled knowledge artifacts (caller writes counters to `deps.session.memory_recall_state`) |

**Ordering rationale:**
- **#1–2 before #3**: truncation and response capping run before summarization. The summarizer sees partially cleared content but receives rich side-channel context (file working set, todos) to compensate.
- **Preflight before segment**: safety and recall run in `_run_model_preflight()` before `_execute_stream_segment()`; the extended history is passed directly to the segment without storing back to `turn_state.current_history`, so retry iterations start clean.

### 2.5 Approval Resume

Approval resumes reuse the main agent with zero additional tokens. The pydantic-ai SDK skips `ModelRequestNode` entirely on the `deferred_tool_results` path, so the segment continues from exactly where the deferred call paused. No separate resume agent is needed. Approval subject resolution and the resume loop live in [core-loop.md](core-loop.md) §2.3.

## 3. Config

Only the settings that directly shape prompt text are listed here. Compaction thresholds live in [compaction.md](compaction.md); recall parameters in [cognition.md](cognition.md).

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `personality` | `CO_CLI_PERSONALITY` | `tars` | personality for static prompt assembly and memory injection |
| `doom_loop_threshold` | `CO_CLI_DOOM_LOOP_THRESHOLD` | `3` | identical-tool-call streak for warning injection |
| `max_reflections` | `CO_CLI_MAX_REFLECTIONS` | `3` | shell-error streak for reflection-cap injection |
| `memory.injection_max_chars` | `CO_CLI_MEMORY_INJECTION_MAX_CHARS` | `2000` | cap for recalled artifact injection |
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | age decay in turn-time recall scoring |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/agent/_core.py` | main-agent and delegation-agent construction; history-processor and instruction registration |
| `co_cli/agent/_instructions.py` | dynamic instruction callbacks (`add_shell_guidance`, `add_category_awareness_prompt`) |
| `co_cli/prompts/_assembly.py` | `build_static_instructions()`; rule-file validation |
| `co_cli/prompts/personalities/_loader.py` | soul seed, mindset, character memory, examples, critique loading |
| `co_cli/prompts/personalities/_injector.py` | per-turn `personality-context` memory injection |
| `co_cli/prompts/personalities/_validator.py` | personality discovery and file validation |
| `co_cli/context/_history.py` | three registered history processors; `build_recall_injection` and `build_safety_injection` preflight callables; compaction trigger |
| `co_cli/context/_deferred_tool_prompt.py` | `build_category_awareness_prompt()` — category-level prompt for deferred tool discovery |
| `co_cli/context/types.py` | `MemoryRecallState` and `SafetyState` |
