# TUI Layer — REPL, Completer, and Slash Commands


## 1. What & How

The TUI layer is the user-facing shell of the chat session. It owns the REPL loop, input
completion, slash command dispatch, and the boundary between user input and agent turns. The
layer is implemented across three modules: `co_cli/main.py` (loop and lifecycle),
`co_cli/commands/core.py` (command registry and handlers), and
`co_cli/display/core.py` (terminal output surface).

```
User input
    ↓
PromptSession (prompt_toolkit)  ← FileHistory, WordCompleter
    ↓
_chat_loop()                    ← session lifecycle, Ctrl+C handling
    ↓ starts with "/"
dispatch(raw_input, ctx)        ← routes to BUILTIN_COMMANDS or skill
    ↓ starts without "/"
_run_foreground_turn(deps, ...) → run_turn() → LLM turn
    ↓
SlashOutcome
  LocalOnly        → continue to next prompt
  ReplaceTranscript → adopt new history, continue
  DelegateToAgent  → enter LLM turn with delegated_input
```

## 2. Core Logic

### REPL Loop (`_chat_loop`)

`_chat_loop` is the async entry point for an interactive session. It initialises:
- `PromptSession` with `FileHistory` persisted to `~/.co-cli/history.txt`
- `WordCompleter` seeded from `BUILTIN_COMMANDS` keys and user-invocable skill names
  via `_build_completer_words()`
- `CoDeps` via `create_deps()`, which resolves all service handles, workspace paths, and config
- `deps.session.reasoning_display` set from the effective startup mode (CLI flag or config)

The loop reads one line of input at a time. `exit`/`quit` break cleanly. Empty input is skipped.

Interrupt handling:
- First Ctrl+C: prints "Press Ctrl+C again to exit" and continues
- Second Ctrl+C within 2 seconds: breaks the loop
- `EOFError` (Ctrl+D): breaks the loop

### Tab Completion

`_build_completer_words(skill_commands)` is the single source of truth for completer content.
It returns `["/cmd" for cmd in BUILTIN_COMMANDS] + ["/name" for user_invocable skills]`.
The completer is rebuilt once after `create_deps()` resolves the skill registry. Subsequent
skill changes within the session (e.g. `/skills reload`) call `set_skill_commands()` and the
completer is updated in-place on `ctx.completer`.

### Slash Command Dispatch (`dispatch`)

Input starting with `/` is routed through `dispatch(raw_input, ctx)`:

```
parse name = first token after "/"
parse args = remainder (empty string if none)

if name in BUILTIN_COMMANDS:
    result = await handler(ctx, args)
    if ReplaceTranscript  → return it
    if result is not None → return ReplaceTranscript(history=result)  # list[Any] path
    else                  → return LocalOnly()

elif name in skill_commands:
    resolve body, inject $ARGUMENTS / $N / $0
    return DelegateToAgent(delegated_input=body, skill_env=..., skill_name=...)

else:
    print "unknown command" hint
    return LocalOnly()
```

Unknown commands print a hint and return `LocalOnly` — they do not reach the LLM.

Security: skill env vars blocked from overriding system paths (`PATH`, `PYTHONPATH`, `HOME`,
etc.) via `_SKILL_ENV_BLOCKED`. Skill content is scanned for `credential_exfil`,
`pipe_to_shell`, `destructive_shell`, and `prompt_injection` patterns before loading.

### Return Type Contract

| Return type | Handler returns | `dispatch` produces | Chat loop action |
|---|---|---|---|
| `LocalOnly` | `None` | `LocalOnly()` | Return to prompt |
| `ReplaceTranscript` | `ReplaceTranscript(history=...)` | same | Adopt new history |
| History list (legacy) | `list[Any]` | `ReplaceTranscript(history=list)` | Adopt new history |
| `DelegateToAgent` | N/A (skill path only) | `DelegateToAgent(...)` | Enter LLM turn |

All built-in command handlers return `None` or `ReplaceTranscript`. Returning `list[Any]` is
supported for backwards compatibility with a small number of older handlers (e.g. `_cmd_clear`).

### `CommandContext`

Every handler receives a `CommandContext` input bag:

```
CommandContext:
  message_history: list[Any]    — current REPL history
  deps: CoDeps                  — full runtime dependencies
  agent: Agent[CoDeps, ...]     — live agent (needed by LLM-backed commands)
  completer: Any | None         — live WordCompleter (for completer updates)
  frontend: Frontend | None     — terminal frontend for confirmation prompts
```

`deps.session` and `deps.runtime` are mutable throughout the session. Commands that update
session state (e.g. `/reasoning`, `/approvals`) write directly to `deps.session.*`.

### Interaction with `CoSessionState` and `CoRuntimeState`

`CoSessionState` fields are readable and writable by slash command handlers:
- `session_approval_rules` — managed by `/approvals`
- `session_todos` — managed by task-related commands
- `reasoning_display` — managed by `/reasoning`
- `background_tasks`, `drive_page_tokens` — managed by tool layer

`CoRuntimeState` fields are owned by the orchestration layer. Slash commands must not write
to `CoRuntimeState` — use `CoSessionState` for user-preference and cross-turn session state.

## 3. Slash Command Reference

All built-in commands are registered in `BUILTIN_COMMANDS: dict[str, SlashCommand]`.

| Command | Args | What it does | Returns |
|---|---|---|---|
| `/help` | — | List all slash commands with descriptions | `None` → `LocalOnly` |
| `/clear` | — | Clear conversation history | `list[]` → `ReplaceTranscript` |
| `/new` | — | Rotate session ID, start fresh | `list[]` → `ReplaceTranscript` |
| `/compact` | — | Summarise conversation via LLM to reduce context | `ReplaceTranscript` or `None` |
| `/resume` | `[session-id]` | Resume a past session by ID or via picker | `ReplaceTranscript` or `None` |
| `/sessions` | — | List past sessions with timestamps | `None` |
| `/history` | — | Show delegation history (sub-agents + background) | `None` |
| `/tools` | — | List registered agent tools with descriptions | `None` |
| `/skills` | `[name]` | List loaded skills; show detail for named skill | `None` |
| `/memory` | `list\|count\|forget\|dream\|restore\|decay-review\|stats [args] [flags]` | Manage knowledge artifacts; dream lifecycle details live in [dream.md](dream.md) | `None` |
| `/approvals` | `list\|clear\|...` | View and manage session approval rules | `None` |
| `/background` | `<command>` | Run a shell command in the background | `None` |
| `/tasks` | `[status-filter \| task-id]` | List background tasks; pass a 12-hex-char task ID to show detail | `None` |
| `/cancel` | `<task-id>` | Cancel a running background task | `None` |
| `/reasoning` | `[off\|summary\|full\|next]` | Show or set reasoning display mode | `None` |

### `/reasoning` detail

`/reasoning` controls how model thinking/reasoning is surfaced in the terminal:

| Mode | Display behaviour |
|---|---|
| `off` | Thinking stream is silently dropped |
| `summary` | Thinking is reduced to short operator-style progress lines (default) |
| `full` | Raw thinking is streamed and committed to the terminal |

Usage:
- `/reasoning` — print current mode, no state change
- `/reasoning next` (or `cycle`) — advance through `off → summary → full → off`
- `/reasoning off|summary|full` — set directly

The mode is stored on `deps.session.reasoning_display` (a `CoSessionState` field, default
`"summary"`). It is read by `_execute_stream_segment()` at stream start via
`StreamRenderer(frontend, reasoning_display=deps.session.reasoning_display)`. Changes take
effect on the next turn; any in-flight stream uses the mode it started with.

Delegation agent turns inherit the mode via `fork_deps()`, which copies
`base.session.reasoning_display` into the child agent's `CoSessionState`.

## 4. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Initial reasoning display mode; overridden by `--reasoning-display` CLI flag or `/reasoning` mid-session |

The `--verbose` / `-v` CLI flag is an alias for `--reasoning-display full`.

## 5. Files

| File | Purpose |
|---|---|
| `co_cli/main.py` | REPL loop (`_chat_loop`), foreground turn entry, CLI command (`chat`) |
| `co_cli/commands/core.py` | Slash-command registry and `dispatch()` |
| `co_cli/commands/registry.py` | `BUILTIN_COMMANDS` dict, `SlashCommand` dataclass, `filter_namespace_conflicts`, completer helpers |
| `co_cli/commands/types.py` | `CommandContext`, `SlashOutcome`, `LocalOnly`, `ReplaceTranscript`, `DelegateToAgent`, `_confirm` |
| `co_cli/display/core.py` | `Frontend` protocol, `TerminalFrontend`, `console`, `set_theme`, `PROMPT_CHAR` |
| `co_cli/display/stream_renderer.py` | `StreamRenderer` — text/thinking buffering and flush policy per segment |
| `co_cli/deps.py` | `CoSessionState` (user-preference + tool-visible state), `CoRuntimeState` (orchestration state) |
| `co_cli/config/core.py` | `VALID_REASONING_DISPLAY_MODES`, `DEFAULT_REASONING_DISPLAY`, mode constants |
| `co_cli/skills/skill_types.py` | `SkillConfig` — skill metadata including body, env vars, invocability flags |
