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
Application (prompt_toolkit, full_screen=False)  ← single terminal owner
  layout: in-flight streaming window + input TextArea (FileHistory, completer) + toolbar window
    ↓ accept_handler: arm a turn task (idle) / enqueue (mid-turn, FIFO)
_chat_loop()                    ← session lifecycle; Esc interrupts active turn, c-c double-press exit
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

A single `prompt_toolkit.Application` owns the inline terminal. Rich is demoted to a
stateless renderable→ANSI bridge (`render_to_ansi`): committed output is printed to
scrollback via `print_formatted_text(ANSI(...))`, and in-flight streaming renders into a
`FormattedTextControl` window updated by throttled `app.invalidate()`. `app.run_async()` is
wrapped in `patch_stdout()` so the incidental `console.print` sites reflow above the input.

## 2. Core Logic

### REPL Loop (`_chat_loop`)

`_chat_loop` is the async entry point for an interactive session. It initialises:
- `CoDeps` via `create_deps()`, which resolves all service handles, workspace paths, and config
- `deps.session.reasoning_display` set from the effective startup mode (CLI flag or config)
- a `FileHistory` persisted to `~/.co-cli/history.txt` and a completer seeded from
  `BUILTIN_COMMANDS` keys and user-invocable skill names
- the REPL `Application` via `build_repl_app(...)` (`co_cli/display/_app.py`), bound to the
  frontend via `frontend.bind_app(app)`; the loop then drives it with `await app.run_async()`
  inside `patch_stdout()`

The Application is event-driven, not a read-one-line loop. The input `TextArea`'s
`accept_handler` arms a turn task (`asyncio.ensure_future`) for an idle submission; a submission
arriving while a turn is active **enqueues** (FIFO, non-blank only) instead of being dropped.
The mid-turn append routes through a single `_enqueue(runtime, text, deps, on_status)` helper
(`co_cli/main.py`): blank-drop first (a blank never counts against the cap), then a bound check —
when `repl.queue_cap > 0` and the append would exceed it, drop per `repl.drop_policy`
(`"oldest"` pops the head then appends; `"newest"` rejects the incoming item, one notice either
way); `queue_cap == 0` is unbounded (the default). Each armed turn carries an `add_done_callback`
that drains the next queued item at the turn boundary — normal completion *and* `Esc`-cancel both
fire it — so the queue advances one item per turn. Turn state — the current turn-task reference,
the iteration state, and the input `queue` (`collections.deque[str]`) — has one owner,
`_ReplRuntime`, shared by the `accept_handler` and the key bindings. Queue depth + a truncated head-item preview surface
in the bottom toolbar (`{n} queued: "…"`, omitted at depth 0). `/queue [list|clear|pop [n]]`
inspects or prunes pending items; mid-turn it bypasses the queue and runs via
`runtime.schedule_control(...)` (it is a buffer op, not a turn). `exit`/`quit` and empty input
are handled inside `_handle_one_input`.

Interrupt handling (via key bindings):
- `Esc` while a turn is running: cancels the active turn task; its done-callback drains the next
  queued item, so `Esc` interrupts and advances the queue. The interrupted query is abandoned
  (not re-run). Idle: no-op.
- First Ctrl+C while idle: prints "Press Ctrl+C again to exit" and arms a 2 s window
- Second Ctrl+C within 2 seconds: exits (`app.exit()`)
- Ctrl+C is exit-only — it does **not** cancel the active turn (interrupt moved to `Esc`); a
  double-press exit tears the app down, which cancels any in-flight turn
- Ctrl+D (`eof`): exits

### Tab Completion

`_build_completer_words(skill_index)` is the single source of truth for completer content.
It returns `["/cmd" for cmd in BUILTIN_COMMANDS] + ["/name" for user_invocable skills]`.
The completer is rebuilt once after `create_deps()` resolves the skill registry. Subsequent
skill changes within the session (e.g. `/skills reload`) call `set_skill_index()` and the
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

elif name in skill_index:
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
- `background_tasks`, `google.drive_page_tokens` — managed by tool layer

`CoRuntimeState` fields are owned by the orchestration layer. Slash commands must not write
to `CoRuntimeState` — use `CoSessionState` for user-preference and cross-turn session state.

## 3. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Initial reasoning display mode; overridden by `--reasoning-display` CLI flag or `/reasoning` mid-session |
| `repl.queue_cap` | `CO_REPL_QUEUE_CAP` | `0` | Max pending mid-turn input-queue items (≥ 0); `0` = unbounded |
| `repl.drop_policy` | `CO_REPL_DROP_POLICY` | `"oldest"` | Drop policy when an enqueue exceeds `queue_cap`: `"oldest"` drops the head, `"newest"` rejects the incoming item. Inert at cap `0` |

The `--verbose` / `-v` CLI flag is an alias for `--reasoning-display full`.

## 4. Public Interface

### Dispatch API

| Symbol | Source | Contract |
|---|---|---|
| `dispatch(raw_input, ctx) -> SlashOutcome` | `co_cli/commands/core.py` | Async — parses `/<name> <args>`, routes to `BUILTIN_COMMANDS` or skill; falls back to unknown-command hint |
| `BUILTIN_COMMANDS: dict[str, SlashCommand]` | `co_cli/commands/registry.py` | Module-level registry of built-in slash commands |
| `SlashCommand` | `co_cli/commands/registry.py` | Dataclass — `name`, `handler`, `description`, `argument_hint`, `category` |
| `CommandContext` | `co_cli/commands/types.py` | Input bag passed to every handler — `message_history`, `deps`, `agent`, `completer`, `frontend`, `input_queue` (live REPL queue by reference, `None` outside REPL) |
| `SlashOutcome`, `LocalOnly`, `ReplaceTranscript(history)`, `DelegateToAgent(delegated_input, skill_env, skill_name)` | `co_cli/commands/types.py` | Handler return types signalling REPL action |
| `filter_namespace_conflicts(skill_index) -> dict` | `co_cli/commands/registry.py` | Drops skills whose names collide with `BUILTIN_COMMANDS` |
| `_build_completer_words(skill_index) -> list[str]` | `co_cli/commands/registry.py` | Returns `["/cmd" for cmd in BUILTIN_COMMANDS] + ["/name" for user-invocable skills]` |

### Frontend surface

| Symbol | Source | Contract |
|---|---|---|
| `Frontend` (Protocol) | `co_cli/display/core.py` | Display contract — `on_status`, `clear_status`, `update_status`, `cleanup`, and the async interactive prompts `prompt_approval`, `prompt_question`, `prompt_confirm` (coroutines) |
| `TerminalFrontend` | `co_cli/display/core.py` | Single-owner terminal implementation: drives one `prompt_toolkit.Application` via `bind_app(app)`; streaming surfaces share one in-flight ANSI buffer; committed output prints to scrollback. Rich is used only as the `render_to_ansi` bridge |
| `HeadlessFrontend` | `co_cli/display/headless.py` | No-op frontend for evals and tests; stores `last_status_snapshot` for inspection; mirrors the async prompt signatures |
| `render_to_ansi(renderable, *, width) -> str` | `co_cli/display/core.py` | The sole Rich renderable→ANSI-string primitive; stateless, width supplied by the caller |
| `console`, `set_theme(name)`, `PROMPT_CHAR` | `co_cli/display/core.py` | Shared console instance, theme switcher, prompt glyph |
| `build_repl_app(...)`, `build_key_bindings(...)`, `_ReplRuntime` | `co_cli/display/_app.py` | Inline-REPL Application factory, Esc/Ctrl+C/Ctrl+D key bindings, and the single turn-state holder (F7) — holds the turn-task reference and the input `queue` |
| `StreamRenderer(frontend, reasoning_display)` | `co_cli/display/stream_renderer.py` | Per-segment text/thinking buffering and flush policy |
| `QuestionPrompt(question, options, multiple)` | `co_cli/display/core.py` | Clarify-path approval prompt for tool-issued questions |
| `StatusSnapshot(session_label, mode, context_pct, background_task_count, approval_count, queue_depth=0, queue_head_preview=None)` | `co_cli/display/core.py` | Typed contract for bottom-toolbar footer content; pushed via `update_status` (which repaints via `_invalidate`); when `queue_depth > 0`, renders `{n} queued: "<preview>"` between `mode` and `ctx`; omitted at 0 |
| `TerminalFrontend.render_footer_toolbar()` | `co_cli/display/core.py` | Plain-text footer string consumed by the toolbar `Window` in the Application layout |
| `_build_status_snapshot(deps, mode, queue)` | `co_cli/main.py` | Assembles a `StatusSnapshot` from `CoDeps` at lifecycle push points; callers pass `runtime.queue` (or an empty `deque()` at startup) — both depth and head-item preview are derived inside |

### Slash command reference

All built-in commands registered in `BUILTIN_COMMANDS`:

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
| `/filescope` | — | Show file search roots (read scope) and the workspace write anchor; flags missing roots | `None` |
| `/skills` | `[name]` | List loaded skills; show detail for named skill | `None` |
| `/memory` | `list\|count\|forget\|dream\|restore\|decay-review\|stats [args] [flags]` | Manage memory items; dream lifecycle details live in [dream.md](dream.md) | `None` |
| `/approvals` | `list\|clear\|...` | View and manage session approval rules | `None` |
| `/background` | `<command>` | Run a shell command in the background | `None` |
| `/tasks` | `[status-filter \| task-id]` | List background tasks; pass a 12-hex-char task ID to show detail | `None` |
| `/cancel` | `<task-id>` | Cancel a running background task | `None` |
| `/queue` | `[list\|clear\|pop [n]]` | Inspect or prune pending REPL input-queue items; mid-turn bypass runs immediately without enqueueing | `None` |
| `/reasoning` | `[off\|summary\|full\|next]` | Show or set reasoning display mode | `None` |
| `/usage` | `[week\|month\|total]` | Show token usage: no arg = current session totals (daemon excluded); a window shows a Session / Daemon / Total split with a distinct-session count | `None` |

#### `/reasoning` detail

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

## 5. Files

| File | Purpose |
|---|---|
| `co_cli/main.py` | REPL loop (`_chat_loop`), foreground turn entry, CLI command (`chat`) |
| `co_cli/commands/core.py` | Slash-command registry and `dispatch()` |
| `co_cli/commands/registry.py` | `BUILTIN_COMMANDS` dict, `SlashCommand` dataclass, `filter_namespace_conflicts`, completer helpers |
| `co_cli/commands/types.py` | `CommandContext`, `SlashOutcome`, `LocalOnly`, `ReplaceTranscript`, `DelegateToAgent`, `_confirm` |
| `co_cli/display/core.py` | `Frontend` protocol, `TerminalFrontend`, `render_to_ansi`, `StatusSnapshot`, `console`, `set_theme`, `PROMPT_CHAR` |
| `co_cli/display/_app.py` | `build_repl_app`, `build_key_bindings`, `_ReplRuntime` — the single-owner inline-REPL Application factory |
| `co_cli/display/headless.py` | `HeadlessFrontend` — full `Frontend` protocol implementation for evals and tests |
| `co_cli/display/stream_renderer.py` | `StreamRenderer` — text/thinking buffering and flush policy per segment |
| `co_cli/deps.py` | `CoSessionState` (user-preference + tool-visible state), `CoRuntimeState` (orchestration state) |
| `co_cli/config/core.py` | `VALID_REASONING_DISPLAY_MODES`, `DEFAULT_REASONING_DISPLAY`, mode constants |
| `co_cli/skills/skill_types.py` | `SkillInfo` — skill metadata including body, env vars, invocability flags |
