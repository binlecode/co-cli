# TODO: Approval Flow Extraction

**Origin:** RESEARCH-PYDANTIC-AI-CLI-BEST-PRACTICES.md gap analysis (runtime decomposition)

---

## Problem

`co_cli/main.py` centralizes six distinct concerns in a single `chat_loop()`:

1. **Input handling** — prompt-toolkit session, slash-command dispatch, `!` passthrough (lines 319–356)
2. **Agent execution** — `_stream_agent_run()` call + model settings (lines 362–366)
3. **Approval orchestration** — `_handle_approvals()` loop with y/n/a prompt, safe-command auto-approval, YOLO escalation, SIGINT handler swap (lines 240–297)
4. **Tool output display** — inline in `_stream_agent_run()` via Live + Markdown rendering with throttled refresh (lines 155–237)
5. **Interrupt patching** — `_patch_dangling_tool_calls()` repairs history when Ctrl+C fires mid-run (lines 122–149)
6. **Sandbox lifecycle** — creation + cleanup in `finally` (lines 300, 399)

### Why extract

- **Testability** — `_handle_approvals` and `_patch_dangling_tool_calls` are untested because they're entangled with the console. See `TODO-approval-interrupt-tests.md`.
- **Separation of concerns** — `_stream_agent_run` interleaves orchestration (event loop) with Rich display (`Live`, `Markdown`, `Panel`). Extracting into `_orchestrate.py` keeps streaming logic co-located with approval orchestration, separate from UI.
- **CI/headless mode** — extracting the approval callback enables non-interactive approval (auto-deny, policy-based) without forking the entire chat loop.

### Coupling points

| Function | Location | Coupled to console? |
|---|---|---|
| `_handle_approvals` | main.py:243 | Yes — `Prompt.ask()`, `console.print()` |
| `_stream_agent_run` | main.py:155 | Yes — `console.print()`, `Panel`, `Live`, `Markdown` |
| `_patch_dangling_tool_calls` | main.py:122 | No (pure function) |
| `_is_safe_command` | _approval.py:4 | No (pure function) |

---

## Key Design Issue: `_stream_agent_run` couples orchestration with display

`_stream_agent_run()` (main.py:155–237) interleaves orchestration logic with Rich display calls in the same `async for` loop:

```
async for event in agent.run_stream_events(...):     # orchestration
    if PartDeltaEvent:
        live = Live(Markdown(text_buffer), console=console)  # display — Rich Live
        live.start()                                         # display
    elif FunctionToolCallEvent:
        console.print(f"[dim]  {tool}({cmd})[/dim]")        # display
    elif FunctionToolResultEvent:
        console.print(Panel(content, ...))                   # display — Rich Panel
    elif AgentRunResultEvent:
        result = event.result                                # orchestration
```

Six Rich imports would follow `_stream_agent_run` into `_orchestrate.py`: `console`, `Live`, `Markdown`, `Panel`, `Prompt`, `_CHOICES_HINT`. This defeats the extraction's testability goal.

**Resolution: Split the event loop with a `DisplayCallback` protocol.** The event loop stays in `_orchestrate.py` but delegates all rendering to an injected callback. The orchestrator never imports Rich.

---

## Design

### New module: `co_cli/_orchestrate.py`

Single responsibility: run the agent, handle deferred approvals via injected callbacks, patch interrupts. No Rich imports — all display via `DisplayCallback` protocol.

### Callback protocols

```python
# co_cli/_orchestrate.py

from typing import Protocol

from pydantic_ai import DeferredToolRequests, ToolDenied


class ApprovalDecision:
    """Result of a single tool-call approval prompt."""
    approved: bool
    yolo: bool  # escalate to auto-approve all remaining


class ApprovalCallback(Protocol):
    """Injected by the caller (CLI, test, CI) to decide on tool approvals."""

    async def __call__(
        self,
        tool_name: str,
        args: dict,
        *,
        auto_approved: bool,
    ) -> ApprovalDecision: ...


class DisplayCallback(Protocol):
    """Injected by the caller to render streaming events."""

    def on_text_delta(self, delta: str) -> None: ...
    def on_text_commit(self) -> None: ...
    def on_tool_call(self, tool_name: str, args: dict) -> None: ...
    def on_tool_result(self, tool_name: str, content: str | dict) -> None: ...
```

### Orchestration function

```python
async def run_with_approvals(
    agent: Agent,
    user_input: str | None,
    *,
    deps: CoDeps,
    message_history: list,
    model_settings: ModelSettings | None,
    usage_limits: UsageLimits,
    approval_callback: ApprovalCallback,
    display: DisplayCallback,
) -> tuple[Any, list, bool]:
    """Run agent with streaming, loop through deferred approvals.

    Returns (result, updated_history, streamed_text).

    Handles:
    - _stream_agent_run() invocation with display callback
    - DeferredToolRequests loop with approval callback
    - Safe-command auto-approval (delegates to _is_safe_command)
    - KeyboardInterrupt → _patch_dangling_tool_calls
    """
    ...
```

### Extracted `_stream_agent_run` (display-free)

```python
async def _stream_agent_run(agent, *, user_input, deps, message_history,
                            model_settings, usage_limits,
                            display: DisplayCallback,
                            deferred_tool_results=None):
    result = None
    streamed_text = False

    async for event in agent.run_stream_events(
        user_input, deps=deps, message_history=message_history,
        model_settings=model_settings, usage_limits=usage_limits,
        deferred_tool_results=deferred_tool_results,
    ):
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            display.on_text_delta(event.delta.content_delta)
            streamed_text = True
            continue

        display.on_text_commit()  # flush text before tool output

        if isinstance(event, FunctionToolCallEvent):
            display.on_tool_call(event.part.tool_name, event.part.args_as_dict())
        elif isinstance(event, FunctionToolResultEvent):
            if isinstance(event.result, ToolReturnPart):
                display.on_tool_result(event.result.tool_name, event.result.content)
        elif isinstance(event, AgentRunResultEvent):
            result = event.result

    display.on_text_commit()  # final flush
    return result, streamed_text
```

### CLI callbacks (in `main.py`)

```python
class RichDisplay:
    """DisplayCallback implementation using Rich Live + Markdown + Panel."""

    def __init__(self, console: Console):
        self._console = console
        self._live: Live | None = None
        self._text_buffer = ""
        self._last_render = 0.0
        self._pending_cmds: dict[str, str] = {}

    def on_text_delta(self, delta: str) -> None:
        self._text_buffer += delta
        now = time.monotonic()
        if now - self._last_render >= _RENDER_INTERVAL:
            if self._live is None:
                self._live = Live(Markdown(self._text_buffer),
                                  console=self._console, auto_refresh=False)
                self._live.start()
            else:
                self._live.update(Markdown(self._text_buffer))
                self._live.refresh()
            self._last_render = now

    def on_text_commit(self) -> None:
        if self._live:
            self._live.update(Markdown(self._text_buffer))
            self._live.refresh()
            self._live.stop()
            self._live = None
            self._text_buffer = ""
            self._last_render = 0.0

    def on_tool_call(self, tool_name: str, args: dict) -> None:
        if tool_name == "run_shell_command":
            cmd = args.get("cmd", "")
            self._console.print(f"[dim]  {tool_name}({cmd})[/dim]")
        else:
            self._console.print(f"[dim]  {tool_name}()[/dim]")

    def on_tool_result(self, tool_name: str, content: str | dict) -> None:
        if isinstance(content, str) and content.strip():
            self._console.print(Panel(content.rstrip(), title=f"$ {tool_name}",
                                      border_style="shell"))
        elif isinstance(content, dict) and "display" in content:
            self._console.print(content["display"])


class CliApprovalCallback:
    """Interactive y/n/a approval using Rich Prompt. Owns SIGINT handler swap."""

    def __init__(self, console: Console):
        self._console = console

    async def __call__(self, tool_name, args, *, auto_approved):
        if auto_approved:
            return ApprovalDecision(approved=True, yolo=False)

        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        desc = f"{tool_name}({args_str})"
        self._console.print(f"Approve [bold]{desc}[/bold]?" + _CHOICES_HINT, end=" ")

        # Swap SIGINT handler so Ctrl-C works in synchronous Prompt.ask()
        prev = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            choice = Prompt.ask("", choices=["y", "n", "a"], default="n",
                                show_choices=False, show_default=False,
                                console=self._console)
        finally:
            signal.signal(signal.SIGINT, prev)

        if choice == "a":
            return ApprovalDecision(approved=True, yolo=True)
        elif choice == "y":
            return ApprovalDecision(approved=True, yolo=False)
        else:
            return ApprovalDecision(approved=False, yolo=False)
```

### What moves where

| Current location | Destination | Notes |
|---|---|---|
| `_stream_agent_run()` main.py:155 | `_orchestrate.py` | Event loop only — display calls replaced with `DisplayCallback` |
| `_handle_approvals()` main.py:243 | `_orchestrate.run_with_approvals()` | Approval UX replaced with `ApprovalCallback` |
| `_patch_dangling_tool_calls()` main.py:122 | `_orchestrate.py` | Pure function, no dependencies |
| `_CHOICES_HINT` main.py:240 | `main.py` (stays) | Display-only, used by `CliApprovalCallback` |
| SIGINT handler swap main.py:251 | `CliApprovalCallback` | Signal handling is a UI concern |
| `Live`/`Markdown`/`Panel` rendering | `RichDisplay` | All Rich imports stay in `main.py`, never enter `_orchestrate.py` |
| `_RENDER_INTERVAL`, throttle logic | `RichDisplay` | Display concern, not orchestration |
| `_is_safe_command()` _approval.py:4 | `_approval.py` (stays) | Already extracted |

### chat_loop after extraction

```python
async def chat_loop():
    agent, model_settings, tool_names = get_agent()
    deps = create_deps()
    display = RichDisplay(console)
    approval_cb = CliApprovalCallback(console)
    # ...
    while True:
        # ... input handling ...
        console.print("[dim]Co is thinking...[/dim]")
        try:
            result, message_history, streamed_text = await run_with_approvals(
                agent, user_input,
                deps=deps,
                message_history=message_history,
                model_settings=model_settings,
                usage_limits=UsageLimits(request_limit=settings.max_request_limit),
                approval_callback=approval_cb,
                display=display,
            )
            if not streamed_text and isinstance(result.output, str):
                console.print(Markdown(result.output))
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[dim]Interrupted.[/dim]")
```

---

## Implementation Plan

### Items

- [ ] Create `co_cli/_orchestrate.py` with `DisplayCallback`, `ApprovalCallback` protocols and `run_with_approvals()`
- [ ] Move `_patch_dangling_tool_calls()` from `main.py` to `_orchestrate.py`
- [ ] Move `_stream_agent_run()` to `_orchestrate.py` — replace inline display code with `DisplayCallback` calls
- [ ] Move safe-command auto-approval logic into `run_with_approvals()` (calls `_is_safe_command`)
- [ ] Create `RichDisplay` class in `main.py` implementing `DisplayCallback` (owns `Live`, `Markdown`, `Panel`, throttle)
- [ ] Create `CliApprovalCallback` class in `main.py` implementing `ApprovalCallback` (owns `Prompt.ask`, SIGINT swap)
- [ ] Refactor `chat_loop()` to instantiate callbacks and call `run_with_approvals()`
- [ ] Add functional test for `_patch_dangling_tool_calls` (see `TODO-approval-interrupt-tests.md`)
- [ ] Add functional test for `run_with_approvals` using `BufferDisplay` + auto-approve callback (no Rich, no terminal)

### File changes

| File | Change |
|---|---|
| `co_cli/_orchestrate.py` | New — `DisplayCallback`, `ApprovalCallback`, `ApprovalDecision`, `run_with_approvals`, `_stream_agent_run`, `_patch_dangling_tool_calls` |
| `co_cli/main.py` | Remove extracted functions; add `RichDisplay`, `CliApprovalCallback`; call `run_with_approvals()` |
| `co_cli/_approval.py` | No change — `_is_safe_command` stays |
| `tests/test_orchestrate.py` | New — `BufferDisplay`, auto-approve callback; tests for patch + orchestration without terminal |
