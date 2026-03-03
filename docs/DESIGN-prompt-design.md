# Design: Agentic Loop & Prompting

## 1. What & How

This component defines co-cli's execution primitive (`run_turn`) and prompt architecture (static assembly + per-turn layers). It is the runtime contract between REPL orchestration, pydantic-ai execution, history processors, and tool approval flow.

`DESIGN-core.md` remains the system skeleton. This doc is the canonical deep spec for loop behavior, safety policies, and context governance coupling.

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
  decisions = collect y/n/a decisions per tool_call_id  (via _handle_approvals)
  result = resume stream(user_input=None,
                         message_history=result.all_messages(),
                         deferred_tool_results=decisions,
                         usage_limits=same_turn_limits,
                         usage=accumulated_usage)
```

`_handle_approvals()` runs a four-tier decision chain per pending call:

1. **Safe-command allowlist** — `_approval._is_safe_command(cmd)` — shell-only, silent auto-approve. Chaining operators force tier 4.
2. **Persistent cross-session approvals** — `_exec_approvals.find_approved(cmd, entries)` — shell-only. Matches stored fnmatch patterns from `.co-cli/exec-approvals.json`. Updates `last_used_at` on match. Bare `"*"` patterns are blocked.
3. **Per-session auto-approve** — `deps.auto_approved_tools` — non-shell tools. Set when user chose `"a"` earlier in session.
4. **User prompt** — `frontend.prompt_approval(desc)` → `[y/n/a]`.

**`"a"` persistence semantics differ by tool:**
- `run_shell_command`: `"a"` derives an fnmatch pattern (e.g. `"git commit *"`) and appends to `.co-cli/exec-approvals.json` — **cross-session persistent**.
- All other tools: `"a"` adds tool name to `deps.auto_approved_tools` — **session-only**.

Design invariants:
- Approval UX lives in orchestration, not inside tools.
- Usage budget is shared across initial run and all approval resumes.
- See `DESIGN-core.md` Approval Flow for the approval table.

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
- soul seed + character base memories (`souls/{role}/seed.md` + planted memories, placed first)
- ordered rules from `prompts/rules/*.md`
- soul examples from `souls/{role}/examples.md` (when file exists, trailing rules)
- optional model counter-steering from `prompts/quirks/{provider}/{model}.md`

2. **Per-turn conditional layers** (`@agent.system_prompt`):
- current date
- shell guidance
- project instructions from `.co-cli/instructions.md` when present
- personality-context memories when role exists
- active mindset (`## Active mindset: {types}`) after Turn 1 classification, when non-empty
- review lens (`## Review lens`) from `souls/{role}/critique.md` when role exists
- available skills (`## Available Skills`) listing `/name — description` entries from `skill_registry` when non-empty; capped at 2 KB

Design principles:
- System prompt defines identity and behavior policy.
- Tool docstrings define tool selection/chaining guidance.
- Knowledge remains tool-loaded; no bulk memory/articles embedded in base prompt.

For the deep spec on prompt composition (assembly order, per-turn layers, budget), see `DESIGN-personality.md`.

### Tool Preamble Injection

When the model emits no text delta before its first tool call, `_stream_events()` auto-injects a short dim status message via `frontend.on_status()` — the "tool preamble" — to prevent silent UX gaps.

```
FunctionToolCallEvent received:
  if not state.streamed_text and not state.tool_preamble_emitted:
      frontend.on_status(_tool_preamble_message(tool_name))
      state.tool_preamble_emitted = True
```

`_tool_preamble_message()` maps tool names to context-appropriate messages: `recall_memory` → "Checking saved context before answering.", `web_search` → "Looking up current sources.", etc. Unknown tools fall back to `"Running a quick check before answering."` The preamble fires at most once per `_stream_events()` call — `tool_preamble_emitted` is a flag on `_StreamState`.

This covers the common "model goes straight to tools" pattern without requiring any prompt changes.

### Context Governance Coupling

History processors run before model requests inside the same execution primitive, not as a separate mode:
- opening context memory recall on start/topic shift
- historical tool-return trimming
- safety issue injection
- sliding-window compaction (inline or precomputed background summary)

See `DESIGN-context-governance.md` for compaction internals.

### Future Extensions (Deferred)

These are intentionally not implemented yet. See `TODO-subagent-delegation.md` for implementation plan.

1. **Sub-agent delegation**
- Focused research/analysis workers with structured outputs (`ResearchResult`, `AnalysisResult`).
- Delegation via tool call on parent — explicit, traceable in OTel spans.
- Sub-agent tools restricted to read-only (no approval bypass).
- Shared usage budget via `turn_usage` forwarding on `CoDeps` (Phase C).

2. **Confidence-scored advisory outputs**
- Add confidence metadata for advisory tools (e.g., search/recall) when ranking quality justifies it.
- Blocked on FTS5 ranked retrieval baseline (meaningful signal precondition).

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
| `session_ttl_minutes` | `CO_SESSION_TTL_MINUTES` | `60` | Session persistence TTL (minutes) |
| `llm_fallback_models` | `CO_LLM_FALLBACK_MODELS` | `[]` | Comma-separated same-provider fallback models for error recovery |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/main.py` | Session loop, slash dispatch, run_turn integration, background compaction trigger |
| `co_cli/_orchestrate.py` | `run_turn`, `_stream_events`, `_handle_approvals`, tool preamble injection, interrupt patching |
| `co_cli/_exec_approvals.py` | Persistent exec approvals: `derive_pattern()`, `find_approved()`, `add_approval()`, `update_last_used()`, `prune_stale()` |
| `co_cli/agent.py` | Agent factory, static prompt assembly call, per-turn `@agent.system_prompt` layers |
| `co_cli/prompts/__init__.py` | Static prompt assembly (`instructions` + ordered rules + quirks) |
| `co_cli/prompts/model_quirks.py` | Model-specific counter-steering and inference metadata |
| `co_cli/_history.py` | History processors: opening-context recall, safety checks, compaction |
| `co_cli/config.py` | Loop/prompt-relevant settings |
| `co_cli/deps.py` | Runtime fields consumed by loop processors and prompt layers |
| `co_cli/tools/capabilities.py` | `check_capabilities` — capability introspection tool (registered in `agent.py`) |
| `docs/DESIGN-core.md` | System-level skeleton and integration map |
| `docs/DESIGN-context-governance.md` | Detailed compaction and summarization design |
| `docs/DESIGN-personality.md` | Personality composition and per-turn personality injection details |
| `docs/DESIGN-tools.md` | Tool-level conventions and docstring guidance ownership |
