# Research: TUI Architecture Comparison

This note compares the interactive terminal UIs in `hermes-agent` and `co-cli` based on source code, not product summaries.

## 1. Rendering Architecture and Layout Strategy

### Hermes Agent

Hermes does use a fixed-bottom `prompt_toolkit` application, but it is not a true fullscreen alternate-screen app. The CLI imports `Application`, `Layout`, `HSplit`, `Window`, `ConditionalContainer`, `TextArea`, and `patch_stdout()` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py). The interactive layout is assembled as a prompt-toolkit `Layout(HSplit(...))` with dedicated widgets for clarify prompts, sudo prompts, approval prompts, spinner text, image badges, voice status, and a persistent status bar, then passed to `Application(..., full_screen=False, mouse_support=False)` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py).

Hermes also has real stdout-bridging workarounds because it mixes Rich-style ANSI output with prompt-toolkit rendering:

- `_cprint()` routes ANSI through `print_formatted_text(ANSI(...))` so colors survive `patch_stdout` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py).
- `ChatConsole` captures Rich output into a buffer and re-emits each rendered line through `_cprint()` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py).
- The app is actually run inside `with patch_stdout(): app.run()` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py).

Hermes also maintains substantial thread-and-queue machinery around that UI:

- shared modal/UI state and queues such as `_pending_input`, `_interrupt_queue`, `_clarify_state`, `_approval_state`, `_sudo_state`, and voice locks/events are initialized in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py)
- chat execution starts the agent in a background thread in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py)
- the interactive mode also runs a background `spinner_loop` thread and a background `process_loop` thread in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py)

So the accurate description is: Hermes is a prompt-toolkit bottom-docked TUI with explicit modal widgets, stdout patching, and background-thread coordination.

### Co-CLI

`co-cli` uses the higher-level `PromptSession` API rather than a custom prompt-toolkit `Application`. The REPL creates `PromptSession(...)` and calls `await session.prompt_async(...)` inside the chat loop in [`co_cli/main.py`](../../co_cli/main.py). There is no prompt-toolkit layout tree, no status bar widget, and no `patch_stdout()` usage.

Instead, `co-cli` treats prompt input and output as separate phases:

- prompt ownership is tracked with `TerminalFrontend._input_active` in [`co_cli/display/_core.py`](../../co_cli/display/_core.py)
- while input is active, status renderables are buffered and flushed after the prompt returns in [`co_cli/display/_core.py`](../../co_cli/display/_core.py)
- streamed text, thinking, tool activity, and status each use Rich `Live` surfaces managed by `TerminalFrontend` in [`co_cli/display/_core.py`](../../co_cli/display/_core.py)

So the accurate description is: `co-cli` is a linear prompt loop with transient Rich surfaces, not a docked prompt-toolkit TUI.

## 2. Streaming and Thinking Display

### Hermes Agent

Hermes has two distinct mechanisms here.

First, it has a real `KawaiiSpinner` implementation in [`hermes-agent/agent/display.py`](../../../hermes-agent/agent/display.py). That class defines:

- spinner frame sets
- `KAWAII_WAITING` face lists
- `KAWAII_THINKING` face lists
- `THINKING_VERBS`

It is also explicitly aware of prompt-toolkit's `StdoutProxy` and suppresses its own carriage-return animation when `patch_stdout()` is active, because the TUI already renders spinner state through `_spinner_text` in [`hermes-agent/agent/display.py`](../../../hermes-agent/agent/display.py).

Second, Hermes does manual reasoning-tag suppression during streamed output. `_stream_delta()` line-buffers output and filters `<REASONING_SCRATCHPAD>`, `<think>`, `<reasoning>`, and related closing tags in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py). If `show_reasoning` is enabled, Hermes can route reasoning content into a dedicated reasoning box via `_stream_reasoning_delta()` instead of discarding it in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py).

So the accurate claim is not just "Hermes uses a spinner"; it uses both a spinner system and a manual streamed-tag filter because raw reasoning tags can appear in token output.

### Co-CLI

`co-cli` does not parse raw `<think>` tags itself. Its orchestration layer consumes structured `ThinkingPart` and `ThinkingPartDelta` events from `pydantic_ai` in [`co_cli/context/orchestrate.py`](../../co_cli/context/orchestrate.py). `StreamRenderer` then handles three explicit reasoning modes in [`co_cli/display/_stream_renderer.py`](../../co_cli/display/_stream_renderer.py):

- `off`: drop thinking
- `summary`: reduce buffered thinking to short status/progress lines
- `full`: stream and then commit the raw thinking text

In other words, the earlier high-level conclusion was directionally right but too vague. The precise distinction is:

- Hermes filters model-emitted reasoning tags in its own stream parser.
- `co-cli` renders provider-parsed reasoning parts from `pydantic_ai` events.

## 3. Skinning and Customization

### Hermes Agent

Hermes really does have a data-driven skin engine. [`hermes-agent/hermes_cli/skin_engine.py`](../../../hermes-agent/hermes_cli/skin_engine.py) documents and implements YAML-defined skins loaded from `~/.hermes/skins/` plus built-in presets.

The schema supports all of the following:

- arbitrary color keys
- spinner `waiting_faces`
- spinner `thinking_faces`
- spinner `thinking_verbs`
- spinner `wings`
- branding strings such as agent name, welcome text, prompt symbol, and help header
- `tool_prefix`
- per-tool emoji overrides
- banner art fields such as `banner_logo` and `banner_hero`

That means the old draft understated some areas and overstated none here: Hermes skinning is broader than just borders and faces.

### Co-CLI

`co-cli` does have lightweight visual configuration, but not a skin engine. It exposes:

- `theme` with a `dark`/`light` palette in [`co_cli/display/_core.py`](../../co_cli/display/_core.py)
- `--theme` in [`co_cli/main.py`](../../co_cli/main.py)
- `reasoning_display` config and `--reasoning-display` in [`co_cli/config/_core.py`](../../co_cli/config/_core.py) and [`co_cli/main.py`](../../co_cli/main.py)

There is no YAML skin loader, no user-defined spinner personas, no banner art system, and no per-tool emoji or branding layer in the current `co-cli` codebase.

## 4. Interactive Callbacks and Approval UX

### Hermes Agent

Hermes supports true in-TUI modal callbacks from agent-side execution.

The clarify tool is explicitly designed around a platform callback in [`hermes-agent/tools/clarify_tool.py`](../../../hermes-agent/tools/clarify_tool.py). In the CLI, `_clarify_callback()` is invoked from the agent thread, sets `_clarify_state`, invalidates the UI, and waits on a response queue in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py). The prompt-toolkit keybindings then drive:

- arrow-key choice selection
- an appended "Other" branch that switches to free-text input
- timeout handling that falls back to "agent decides"

Hermes uses the same modal pattern for:

- sudo password capture in `_sudo_password_callback()` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py)
- dangerous-command approval in `_approval_callback()` and `_get_approval_display_fragments()` in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py)

So the accurate claim is: Hermes has integrated modal interaction surfaces, not just a single clarify callback.

### Co-CLI

`co-cli` currently handles approvals through deferred-tool approval collection rather than prompt-toolkit modal widgets. `_collect_deferred_tool_approvals()` calls `frontend.prompt_approval(subject.display)` and records `y`, `n`, or `a` decisions in [`co_cli/context/orchestrate.py`](../../co_cli/context/orchestrate.py). `TerminalFrontend.prompt_approval()` then uses Rich `Prompt.ask(...)` with choices `["y", "n", "a"]` in [`co_cli/display/_core.py`](../../co_cli/display/_core.py).

That means the earlier draft was wrong on two details:

- it is not `typer.prompt`
- the interaction is not a pause/resume "live context is paused and resumed" widget inside prompt-toolkit

It is a blocking Rich prompt in the foreground terminal frontend.

## Conclusion

The source-accurate comparison is:

- Hermes uses a prompt-toolkit fixed-bottom layout with modal widgets, `patch_stdout()`, ANSI bridging helpers, manual reasoning-tag filtering, and substantial background-thread coordination.
- `co-cli` uses `PromptSession.prompt_async()` plus Rich `Live` surfaces, with structured `ThinkingPart` handling from `pydantic_ai` and a simpler blocking approval prompt.

## ROI Adoptions for Co

The highest-ROI ideas `co-cli` could adopt from this comparison are:

- Structured deferred user input, not just approvals. `co-cli` already has a pause/resume seam for deferred approvals in [`co_cli/context/orchestrate.py`](../../co_cli/context/orchestrate.py), and Hermes shows that the same pattern can support multiple-choice plus free-text clarification flows through a dedicated UI callback and widget layer in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py) and [`hermes-agent/tools/clarify_tool.py`](../../../hermes-agent/tools/clarify_tool.py). This would reduce wasted turns on ambiguous requirements and planning gaps.
- Richer approval UX for mutating actions. `co-cli` currently uses a compact blocking `Prompt.ask(...)` path in [`co_cli/display/_core.py`](../../co_cli/display/_core.py). Hermes adds more operator context by rendering approval description plus command preview and offering `once`, `session`, `always`, `deny`, and `view` choices in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py). `co-cli` already has approval subjects and remembered rules, so the main missing piece is UI depth rather than policy machinery.
- A persistent status/footer surface. Hermes continuously surfaces model, context usage, and elapsed session time through its status bar helpers in [`hermes-agent/cli.py`](../../../hermes-agent/cli.py). `co-cli` currently has transient status surfaces in [`co_cli/display/_core.py`](../../co_cli/display/_core.py) but no always-visible runtime bar. Adding one would improve long-turn visibility and context-budget awareness.

Low-ROI adoptions for `co-cli` are Hermes's `patch_stdout()` plumbing, ANSI rerouting helpers, and manual reasoning-tag filtering. `co-cli` already has the cleaner path here because it consumes structured `ThinkingPart` / `ThinkingPartDelta` events from `pydantic_ai` in [`co_cli/context/orchestrate.py`](../../co_cli/context/orchestrate.py) and renders them through [`co_cli/display/_stream_renderer.py`](../../co_cli/display/_stream_renderer.py).
