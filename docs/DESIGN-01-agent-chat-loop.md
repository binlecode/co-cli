---
title: "01 — Agent Loop"
parent: Core
nav_order: 1
---

# Design: Agent Loop

## 1. What & How

The agent loop is the core orchestration layer. It connects the REPL (user input), the pydantic-ai Agent (LLM + tools), and the terminal display (Rich). Three modules collaborate:

- **`agent.py`** — `get_agent()` factory: model selection, tool registration, system prompt
- **`_orchestrate.py`** — `run_turn()` state machine: streaming, approval chaining, error handling, interrupt patching
- **`main.py`** — REPL: input dispatch, session lifecycle, context history

`CoDeps` (in `deps.py`) is the runtime dependency dataclass injected into every tool via `RunContext[CoDeps]`. `FrontendProtocol` (in `_orchestrate.py`) abstracts all display — `TerminalFrontend` (Rich/prompt-toolkit) and `RecordingFrontend` (tests) implement it.

```mermaid
graph LR
    subgraph REPL ["REPL (main.py)"]
        Input[User Input]
        Dispatch{Dispatch}
        History[message_history]
    end

    subgraph Orchestration ["Orchestration (_orchestrate.py)"]
        RunTurn["run_turn()"]
        Stream["_stream_events()"]
        Approve["_handle_approvals()"]
    end

    subgraph Agent ["Agent (agent.py)"]
        Factory["get_agent()"]
        PydanticAgent[Pydantic AI Agent]
    end

    subgraph Display ["Display (display.py)"]
        Frontend[TerminalFrontend]
    end

    subgraph Tools ["Tool Layer"]
        ReadOnly[Read-only tools]
        SideEffect[Side-effectful tools]
        MCP[MCP toolsets]
    end

    Input --> Dispatch
    Dispatch -->|text| RunTurn
    Dispatch -->|!cmd| Shell[Shell]
    Dispatch -->|/cmd| SlashCmd[_commands.py]
    RunTurn --> Stream
    Stream --> PydanticAgent
    PydanticAgent --> ReadOnly
    PydanticAgent --> SideEffect
    PydanticAgent --> MCP
    SideEffect -->|DeferredToolRequests| Approve
    Approve -->|approved| Stream
    Stream -->|events| Frontend
    RunTurn -->|TurnResult| History
```

## 2. Core Logic

### Agent Factory (`get_agent`)

Returns `(agent, model_settings, tool_names)`. Selects LLM model based on provider, registers tools with approval policies, assembles the system prompt.

```
get_agent(all_approval, web_policy, mcp_servers) → (agent, model_settings, tool_names):
    resolve model from settings.llm_provider (gemini or ollama)
    build system_prompt via get_system_prompt(provider, personality, model_name)

    create Agent with:
        model, deps_type=CoDeps, system_prompt, retries=tool_retries
        output_type = [str, DeferredToolRequests]
        history_processors = [truncate_tool_returns, truncate_history_window]

    register side-effectful tools with requires_approval=True
    register read-only tools with requires_approval=all_approval
    register web tools with requires_approval=(policy == "ask")
    register MCP toolsets with per-server approval config
```

| Category | Approval | Notes |
|----------|----------|-------|
| Side-effectful | Always deferred | `run_shell_command`, `create_email_draft`, `send_slack_message`, `save_memory` |
| Read-only | Auto-execute | `all_approval=True` forces deferred (for eval harness) |
| Web tools | Policy-driven | `web_policy.search` / `web_policy.fetch`: `"allow"` or `"ask"` |
| MCP tools | Per-server config | `"auto"` → deferred; `"never"` → trusted |

See [DESIGN-03-llm-models.md](DESIGN-03-llm-models.md) for model configuration details.

### CoDeps (Runtime Dependencies)

Flat dataclass injected into every tool via `RunContext[CoDeps]`. Contains runtime resources and scalar config — no `Settings` objects. `main.py:create_deps()` reads `Settings` once and injects values.

| Group | Fields |
|-------|--------|
| **Runtime resources** | `shell` (ShellBackend), `auto_confirm` (session yolo), `session_id` (uuid4), `slack_client`, `google_creds` (lazy-resolved) |
| **Tool config** | `obsidian_vault_path`, `google_credentials_path`, `shell_safe_commands`, `shell_max_timeout` (600), `brave_search_api_key`, `web_fetch_allowed_domains`, `web_fetch_blocked_domains`, `web_policy` |
| **Memory config** | `memory_max_count` (200), `memory_dedup_window_days` (7), `memory_dedup_threshold` (85), `memory_decay_strategy` ("summarize"), `memory_decay_percentage` (0.2) |
| **History governance** | `max_history_messages` (40), `tool_output_trim_chars` (2000), `summarization_model` (empty = primary model) |
| **Mutable state** | `drive_page_tokens` (pagination state per query) |

### Multi-Session State Design

| Tier | Scope | Lifetime | Example |
|------|-------|----------|---------|
| **Agent config** | Process | Entire process | Model, system prompt, tool registrations |
| **Session deps** | Session | One REPL loop | `CoDeps`: shell, creds, page tokens |
| **Run state** | Single run | One `run_turn()` | Per-turn counter (if needed) |

**Invariant:** Mutable per-session state belongs in `CoDeps`, never in module globals. One `CoDeps` per chat session — tools accumulate state across turns within a session.

### Chat Session Lifecycle

```
chat_loop():
    agent, model_settings, tool_names = get_agent(web_policy, mcp_servers)
    deps = create_deps()
    frontend = TerminalFrontend()
    message_history = []

    async with agent:                          ← connects MCP servers
        loop:
            user_input = prompt_async()        ← prompt-toolkit + tab completion

            dispatch:
                "exit"/"quit"  → break
                empty/blank    → continue
                "!cmd"         → shell.run_command(cmd), no LLM
                "/command"     → dispatch_command(), no LLM
                anything else  → run_turn()

            message_history = turn_result.messages

    finally: deps.shell.cleanup()
```

**MCP fallback:** If the agent context fails (MCP server unavailable), the chat loop recreates the agent without MCP and continues with native tools only.

### Orchestration State Machine (`run_turn`)

Single user turn: streaming → approval chaining → error retry → interrupt recovery. Returns `TurnResult(messages, output, usage, interrupted, streamed_text)`.

```
run_turn(agent, user_input, deps, message_history, ...) → TurnResult:
    turn_limits = UsageLimits(request_limit=max_request_limit)
    turn_usage = None
    current_input = user_input
    backoff_base = 1.0

    retry_loop (up to http_retries):
        try:
            result, streamed = _stream_events(agent, current_input, deps,
                message_history, turn_limits, usage=turn_usage, frontend)
            turn_usage = result.usage()

            while result.output is DeferredToolRequests:
                result, streamed = _handle_approvals(agent, deps, result,
                    model_settings, turn_limits, usage=turn_usage, frontend)
                turn_usage = result.usage()

            if not streamed and output is str:
                frontend.on_final_output(result.output)
            return TurnResult(messages=result.all_messages(), ...)

        except ModelHTTPError:
            action, msg, delay = classify_provider_error(e)
            REFLECT  → append error body as ModelRequest to history,
                       set current_input = None, continue
            BACKOFF  → sleep(delay * backoff^attempt), backoff *= 1.5, continue
            ABORT    → return TurnResult(output=None)

        except ModelAPIError:
            backoff retry or ABORT if exhausted

        except (KeyboardInterrupt, CancelledError):
            msgs = result.all_messages() if result else message_history
            return TurnResult(_patch_dangling_tool_calls(msgs), interrupted=True)
```

**Design notes:**

- **Approval `while` loop:** A resumed run may produce another `DeferredToolRequests` when the LLM chains multiple side-effectful calls. Each round needs its own approval cycle
- **Budget sharing:** One `UsageLimits` + accumulating `turn_usage` across streaming, approvals, and retries. Prevents N approval hops from getting N × budget
- **Reflection (400):** Error body injected into history as `ModelRequest`; `current_input` set to `None` so the next `_stream_events` resumes from history, letting the model self-correct
- **Progressive backoff:** Escalates by `backoff_base *= 1.5` per retry, capped at 30s. Applies to both `ModelHTTPError` (429/5xx) and `ModelAPIError` (network/timeout)
- **Safe message extraction:** `result` may be `None` if the exception fired before any result was captured — `result.all_messages() if result else message_history` preserves history

### Streaming (`_stream_events`)

Wraps `agent.run_stream_events()`, dispatches events to frontend. Transient state in `_StreamState` (text/thinking buffers, render timestamps) — fresh per call, no globals.

```
_stream_events(agent, input, deps, history, limits, frontend,
               deferred_tool_results=None) → (result, streamed_text):
    state = _StreamState()
    pending_cmds = {}                          ← shell cmd titles by tool_call_id

    try:
        for each event from agent.run_stream_events(...):
            PartStartEvent(TextPart)           → flush thinking, append text
            PartStartEvent(ThinkingPart)       → if verbose: append, else discard
            TextPartDelta                      → flush thinking, accumulate text,
                                                 throttled render at 50ms (20 FPS)
            ThinkingPartDelta                  → if verbose: accumulate + throttle
            FunctionToolCallEvent              → flush all buffers,
                                                 if shell: store cmd in pending_cmds,
                                                 frontend.on_tool_call(name, args)
            FunctionToolResultEvent            → flush all buffers,
                                                 if ToolReturnPart with str content:
                                                   show with cmd from pending_cmds as title
                                                 elif dict with "display" key:
                                                   show structured result
                                                 else: skip
            FinalResultEvent, PartEndEvent     → no-op (rendering continues after)
            AgentRunResultEvent                → capture result

        commit remaining text buffer
    finally: frontend.cleanup()
```

**Key transitions:** Thinking → text is a one-way flush: first text/tool event commits the thinking panel, then thinking buffer resets. `_flush_for_tool_output()` commits both buffers before any tool annotation or result panel, preventing interleaved output.

**API choice:** `run_stream_events()` over `run_stream()` (incompatible with `DeferredToolRequests` output type), `iter()` (3-4x more code), or `run()` + callback (splits display state).

### Deferred Approval (`_handle_approvals`)

Collects approval decisions for all pending tool calls, then resumes the agent.

```
_handle_approvals(agent, deps, result, model_settings, limits, frontend):
    for each call in result.output.approvals:
        parse args (json.loads if string)
        format description as "tool_name(k=v, ...)"

        if deps.auto_confirm → approve
        elif run_shell_command AND is_safe_command(cmd, shell_safe_commands) → approve
        else:
            choice = frontend.prompt_approval(desc)
            "y" → approve
            "a" → set deps.auto_confirm = True, approve
            "n" → ToolDenied("User denied this action")

    return _stream_events(agent, user_input=None,
        message_history=result.all_messages(),
        deferred_tool_results=approvals, ...)
```

**Safe-command gate:** Commands matching the safe-prefix list are auto-approved. **Denial:** LLM sees `ToolDenied` and can suggest alternatives. **Session yolo:** `"a"` sets `auto_confirm = True` for all subsequent calls in the session.

### FrontendProtocol

`@runtime_checkable` protocol decoupling orchestration from terminal rendering.

| Method | Purpose |
|--------|---------|
| `on_text_delta(accumulated)` | Incremental Markdown render |
| `on_text_commit(final)` | Final render + tear down Live |
| `on_thinking_delta(accumulated)` | Thinking panel (verbose) |
| `on_thinking_commit(final)` | Final thinking panel |
| `on_tool_call(name, args_display)` | Dim annotation |
| `on_tool_result(title, content)` | Panel for result |
| `on_status(message)` | Status messages |
| `on_final_output(text)` | Fallback Markdown render |
| `prompt_approval(description) → str` | y/n/a prompt |
| `cleanup()` | Exception teardown |

Implementations: `TerminalFrontend` (Rich/prompt-toolkit, in `display.py`), `RecordingFrontend` (tests).

### Slash Commands (`_commands.py`)

Local REPL commands — bypass the LLM, execute instantly. Explicit `dict` registry, no decorators. Handler returns `None` (display-only) or `list` (new history to rebind).

| Command | Effect |
|---------|--------|
| `/help` | Print table of all commands |
| `/clear` | Empty conversation history |
| `/status` | System health check |
| `/tools` | List registered tool names |
| `/history` | Show turn/message totals |
| `/compact` | LLM-summarise history (see [DESIGN-07](DESIGN-07-context-governance.md)) |
| `/yolo` | Toggle `deps.auto_confirm` |
| `/model` | Show/switch current model (Ollama only) |
| `/forget <id>` | Delete memory by ID |

### Error Handling

**Provider errors** — two exception types in `run_turn()`:

| Exception | Status | Action | Behavior |
|-----------|--------|--------|----------|
| `ModelHTTPError` | 400 | `REFLECT` | Inject error body into history, `current_input=None`, re-run |
| `ModelHTTPError` | 401, 403, 404 | `ABORT` | Display error, end turn |
| `ModelHTTPError` | 429 | `BACKOFF_RETRY` | Parse `Retry-After` (default 3s), progressive backoff |
| `ModelHTTPError` | 5xx | `BACKOFF_RETRY` | 2s base, progressive backoff |
| `ModelAPIError` | Network/timeout | `BACKOFF_RETRY` | 2s base, progressive backoff |

All retries capped at `model_http_retries` (default 2). Backoff capped at 30s, escalation factor 1.5× per retry. Classified via `classify_provider_error()` in `_provider_errors.py`.

**Tool errors** — classified inside tool functions via `handle_tool_error()` (`tools/_errors.py`):

| Kind | Behavior | Example |
|------|----------|---------|
| `TERMINAL` | Return error dict — model sees it, picks alternative | Auth failure, API not enabled |
| `TRANSIENT` | `ModelRetry` — model retries the call | Rate limit (429), server error (5xx) |
| `MISUSE` | `ModelRetry` with hint — model corrects parameters | Bad resource ID (404) |

### Interrupt Handling

**Dangling tool call patching:** On `KeyboardInterrupt` / `CancelledError`, `_patch_dangling_tool_calls()` scans *all* `ModelResponse` messages for `ToolCallPart` entries without a matching `ToolReturnPart`, then appends a single synthetic `ModelRequest` with `ToolReturnPart`(s) carrying "Interrupted by user." Full scan handles interrupts during multi-tool approval loops where earlier responses may have unmatched calls.

**Signal handling:** `run_turn()` catches both `KeyboardInterrupt` and `CancelledError` (Python 3.11+ asyncio delivers the latter in async code). For the synchronous approval prompt, `TerminalFrontend.prompt_approval()` temporarily restores Python's default SIGINT handler during `Prompt.ask()`, then restores asyncio's handler in `finally`.

| Context | Ctrl+C Result |
|---------|---------------|
| During `run_turn()` | Patches dangling tool calls, returns to prompt |
| During approval prompt | Cancels approval, returns to prompt |
| At prompt (1st) | "Press Ctrl+C again to exit" |
| At prompt (2nd within 2s) | Exits session |
| At prompt (2nd after 2s) | Treated as new 1st press |
| Anywhere (Ctrl+D) | Exits immediately |

### System Prompt Assembly

Composed by `get_system_prompt(provider, personality, model_name)` in `prompts/__init__.py`:

1. **Instructions** — bootstrap identity from `prompts/instructions.md`
2. **Soul seed** — personality fingerprint from `prompts/personalities/seed/`
3. **Behavioral rules** — 5 rules from `prompts/rules/01-05`
4. **Model counter-steering** — quirk corrections from `prompts/model_quirks.py`

See [DESIGN-14-memory-lifecycle-system.md](DESIGN-14-memory-lifecycle-system.md) for knowledge loading details.

### Eval Framework

`scripts/eval_tool_calling.py` uses `get_agent(all_approval=True)` so every tool call returns `DeferredToolRequests` without executing. Scores: `tool_selection`, `arg_extraction`, `refusal`, `error_recovery`. Golden cases in `evals/tool_calling.jsonl` (~26 lines). Auto-discovers `evals/baseline-*.json` for model comparison; degradation beyond `--max-degradation` (default 10pp) fails the run.

### CLI Commands & REPL

| Command | Description |
|---------|-------------|
| `co chat` | Interactive REPL (`--verbose` streams thinking tokens) |
| `co status` | System health check |
| `co tail` | Real-time span viewer |
| `co logs` | Telemetry dashboard (Datasette) |
| `co traces` | Visual span tree (HTML) |

| REPL Feature | Detail |
|--------------|--------|
| History | `~/.local/share/co-cli/history.txt` |
| Spinner | "Co is thinking..." before streaming starts |
| Streaming | Rich `Live` + `Markdown` at ~20 FPS |
| Fallback | Final result rendered as Markdown if streaming produced no text |
| Tab completion | `WordCompleter` for `/command` names |

## 3. Config

Settings relevant to the agent loop. Full settings inventory in `co_cli/config.py`.

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `llm_provider` | `LLM_PROVIDER` | `"gemini"` | Provider selection (`gemini` or `ollama`) |
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Personality preset for system prompt |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Agent-level retry budget for all tools |
| `max_request_limit` | `CO_CLI_MAX_REQUEST_LIMIT` | `25` | Caps LLM round-trips per user turn |
| `model_http_retries` | `CO_CLI_MODEL_HTTP_RETRIES` | `2` | Max provider error retries per turn |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Sliding window threshold |
| `tool_output_trim_chars` | `CO_CLI_TOOL_OUTPUT_TRIM_CHARS` | `2000` | Truncate old tool outputs |
| `summarization_model` | `CO_CLI_SUMMARIZATION_MODEL` | `""` | LLM for summarization (or use agent model) |
| `memory_max_count` | `CO_CLI_MEMORY_MAX_COUNT` | `200` | Max stored memories |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | 3 defaults | MCP server configurations (JSON) |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agent.py` | `get_agent()` factory — model selection, tool registration, MCP wiring |
| `co_cli/deps.py` | `CoDeps` dataclass — runtime dependencies injected via `RunContext` |
| `co_cli/config.py` | `Settings` + `MCPServerConfig` — Pydantic config from `settings.json` + env vars |
| `co_cli/main.py` | CLI entry point, `chat_loop()`, `create_deps()`, OTel setup |
| `co_cli/_orchestrate.py` | `FrontendProtocol`, `TurnResult`, `run_turn()`, `_stream_events()`, `_handle_approvals()` |
| `co_cli/_provider_errors.py` | `ProviderErrorAction`, `classify_provider_error()`, `_parse_retry_after()` |
| `co_cli/display.py` | Themed Rich Console, semantic styles, `TerminalFrontend` |
| `co_cli/_commands.py` | Slash command registry, handlers, `dispatch()` |
| `co_cli/_approval.py` | Shell safe-command classification (`_is_safe_command`) |
| `co_cli/_history.py` | `truncate_tool_returns`, `truncate_history_window`, `summarize_messages` |
| `co_cli/prompts/__init__.py` | `assemble_prompt()` — instructions, soul seed, rules, counter-steering |
| `co_cli/tools/_errors.py` | `ToolErrorKind`, `classify_google_error()`, `handle_tool_error()`, `terminal_error()` |
| `scripts/eval_tool_calling.py` | Eval runner — golden case scoring, model tagging, baseline comparison |
