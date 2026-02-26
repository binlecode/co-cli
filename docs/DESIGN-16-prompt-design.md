---
title: "16 — Agentic Loop & Prompting"
nav_order: 16
---

# Design: Agentic Loop & Prompting

## 1. What & How

This component defines co-cli's execution primitive (`run_turn`) and prompt architecture (static assembly + per-turn layers). It is the runtime contract between REPL orchestration, pydantic-ai execution, history processors, and tool approval flow.

`DESIGN-core.md` remains the system skeleton. This doc is the canonical deep spec for loop behavior, safety policies, and prompt-layer composition.

```mermaid
graph TD
    U[User input] --> C[chat_loop main.py]
    C --> O[run_turn _orchestrate.py]
    O --> S[_stream_events]
    S --> A[agent.run_stream_events]

    A --> HP[history_processors]
    HP --> H1[inject_opening_context]
    HP --> H2[truncate_tool_returns]
    HP --> H3[detect_safety_issues]
    HP --> H4[truncate_history_window]

    A --> T[tool calls]
    T -->|requires_approval| D[DeferredToolRequests]
    D --> AP[_handle_approvals]
    AP --> A

    A --> R[final result]
    R --> O
    O --> C

    P1[assemble_prompt static] --> A
    P2[@agent.system_prompt per-turn] --> A
```

## 2. Core Logic

### Loop Topology and Ownership

Per user message, co runs one orchestration cycle:

```text
chat_loop receives input
  -> run_turn(agent, deps, message_history, ...)
  -> run_turn streams events and resolves approval re-entry
  -> returns TurnResult(messages, output, usage, interrupted, outcome)
  -> chat_loop updates message_history and continues
```

Boundaries:
- `main.py` owns session lifecycle, slash command dispatch, and post-turn hooks.
- `_orchestrate.py` owns streaming, approval chaining, provider retry/backoff, and interrupt recovery.
- `agent.py` owns model selection, tool registration, history processor registration, and per-turn prompt layers.
- `_history.py` owns context governance and safety message injection processors.

### Approval Re-Entry (Single Supported Re-Entry Pattern)

When a tool call requires approval, `agent.run_stream_events()` returns `DeferredToolRequests`.
`run_turn()` stays in an approval loop until the run returns non-deferred output.

Pseudocode:

```text
result = stream(...)
while result.output is DeferredToolRequests:
  decisions = collect y/n/a decisions per tool_call_id
  result = resume stream(user_input=None,
                         message_history=result.all_messages(),
                         deferred_tool_results=decisions,
                         usage_limits=same_turn_limits,
                         usage=accumulated_usage)
```

Design invariants:
- Approval UX lives in orchestration, not inside tools.
- Safe shell commands may auto-approve using `_is_safe_command`.
- Usage budget is shared across initial run and all approval resumes.

### Turn Outcome Contract

`TurnOutcome = Literal["continue", "stop", "error", "compact"]`

Current behavior:
- Normal text completion -> `continue`
- Budget exhaustion with grace summary -> `continue`
- Interrupted turn (with abort marker) -> `continue`
- Unrecoverable provider/network failure -> `error`
- `stop` and `compact` are reserved for explicit control paths

This typed boundary keeps REPL control flow explicit and testable.

### Safety and Resilience Policies

Per-turn safeguards:
- **Turn budget**: `UsageLimits(request_limit=max_request_limit)`.
- **Grace turn on exhaustion**: one extra request asks the model to summarize progress.
- **Doom loop detection**: `detect_safety_issues()` hashes consecutive `ToolCallPart` payloads and injects intervention at `doom_loop_threshold`.
- **Shell reflection cap**: same processor tracks consecutive shell errors and injects intervention at `max_reflections`.
- **Provider retry policy**: `classify_provider_error()` drives reflect/backoff/abort behavior.
- **Finish reason warning**: on `finish_reason == "length"`, emit truncation warning.
- **Interrupt recovery**: patch dangling tool calls, append abort marker to history.

Provider error handling:
- HTTP 400 tool-call rejection -> reflection prompt (reformulate tool JSON).
- HTTP 429/5xx and network errors -> exponential backoff retry (bounded).
- HTTP 401/403/404 or exhausted retries -> terminal error outcome.

### Prompt Architecture

Prompt composition uses two mechanisms:

1. **Static assembly** (`assemble_prompt`), once per agent creation:
- `prompts/instructions.md`
- ordered rules from `prompts/rules/*.md`
- optional model counter-steering from `prompts/quirks/{provider}/{model}.md`

2. **Per-turn conditional layers** (`@agent.system_prompt`):
- personality block (`compose_personality`) when role exists
- current date
- shell guidance
- project instructions from `.co-cli/instructions.md` when present
- personality-context memories when role exists

Design principles:
- System prompt defines identity and behavior policy.
- Tool docstrings define tool selection/chaining guidance.
- Knowledge remains tool-loaded; no bulk memory/articles embedded in base prompt.

### Context Governance Coupling

History processors run before model requests inside the same execution primitive, not as a separate mode:
- opening context memory recall on start/topic shift
- historical tool-return trimming
- safety issue injection
- sliding-window compaction (inline or precomputed background summary)

See `DESIGN-07-context-governance.md` for compaction internals.

### Future Extensions (Deferred)

These are intentionally not implemented yet:

1. **Sub-agent delegation**
- Focused research/analysis workers with structured outputs (`ResearchResult`, `AnalysisResult`).
- Parent remains orchestrator and validator.
- Shared usage budget via usage forwarding.
- Gate remains performance-driven: keep prompt-only approach when it meets quality targets.

2. **Confidence-scored advisory outputs**
- Add confidence metadata for advisory tools (e.g., search/recall) when ranking quality justifies it.

3. **Prompt budget optimization for personality payload**
- Further compress trait-derived behavior payload while preserving role fidelity.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `max_request_limit` | `CO_CLI_MAX_REQUEST_LIMIT` | `50` | Max model requests per user turn (`UsageLimits.request_limit`) |
| `model_http_retries` | `CO_CLI_MODEL_HTTP_RETRIES` | `2` | Provider/network retry budget per turn |
| `doom_loop_threshold` | `CO_CLI_DOOM_LOOP_THRESHOLD` | `3` | Consecutive identical tool calls before intervention |
| `max_reflections` | `CO_CLI_MAX_REFLECTIONS` | `3` | Consecutive shell error threshold before intervention |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Agent-level tool retry limit |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Message-count trigger for compaction |
| `summarization_model` | `CO_CLI_SUMMARIZATION_MODEL` | `""` | Optional compaction model override |
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Personality preset for per-turn personality layer |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/main.py` | Session loop, slash dispatch, run_turn integration, background compaction trigger |
| `co_cli/_orchestrate.py` | `run_turn`, `_stream_events`, `_handle_approvals`, interrupt patching |
| `co_cli/agent.py` | Agent factory, static prompt assembly call, per-turn `@agent.system_prompt` layers |
| `co_cli/prompts/__init__.py` | Static prompt assembly (`instructions` + ordered rules + quirks) |
| `co_cli/prompts/model_quirks.py` | Model-specific counter-steering and inference metadata |
| `co_cli/_history.py` | History processors: opening-context recall, safety checks, compaction |
| `co_cli/config.py` | Loop/prompt-relevant settings |
| `co_cli/deps.py` | Runtime fields consumed by loop processors and prompt layers |
| `docs/DESIGN-core.md` | System-level skeleton and integration map |
| `docs/DESIGN-07-context-governance.md` | Detailed compaction and summarization design |
| `docs/DESIGN-02-personality.md` | Personality composition and per-turn personality injection details |
| `docs/DESIGN-tools.md` | Tool-level conventions and docstring guidance ownership |
