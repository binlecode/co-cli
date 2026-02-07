# TODO: Approval Flow Extraction

**Origin:** RESEARCH-PYDANTIC-AI-CLI-BEST-PRACTICES.md gap analysis (runtime decomposition)

---

## Gap Analysis

`co_cli/main.py` centralizes six distinct concerns in a single `chat_loop()`:

1. **Input handling** — prompt-toolkit session, slash-command dispatch, `!` passthrough (lines 257–293)
2. **Agent execution** — `agent.run()` call + model settings (lines 297–301)
3. **Approval orchestration** — `_handle_approvals()` loop with y/n/a prompt, safe-command auto-approval, YOLO escalation, SIGINT handler swap (lines 151–208)
4. **Tool output display** — `_display_tool_outputs()` post-processes message history to print raw tool results (lines 211–233)
5. **Interrupt patching** — `_patch_dangling_tool_calls()` repairs history when Ctrl+C fires mid-run (lines 118–145)
6. **Sandbox lifecycle** — creation + cleanup in `finally` (lines 238, 334)

The research doc flags this as an anti-pattern (§6: "Mixing all concerns in one loop function") and proposes extraction (§5.2). The streaming TODO (`TODO-streaming-tool-output.md`) will replace item 4 and change item 2, but does not address splitting orchestration from UI.

### Why this matters

- **Testability** — `_handle_approvals` and `_patch_dangling_tool_calls` are untested because they're entangled with the console. See `TODO-approval-interrupt-tests.md`.
- **Streaming migration** — `TODO-streaming-tool-output.md` needs to replace the inner run block. A clean boundary makes that refactor smaller.
- **CI/headless mode** — extracting the approval callback enables non-interactive approval (auto-deny, policy-based) without forking the entire chat loop.

### Current coupling points

| Function | Location | Coupled to console? | Coupled to `settings`? |
|---|---|---|---|
| `_handle_approvals` | main.py:151 | Yes — `Prompt.ask()`, `console.print()` | Yes — `settings.max_request_limit` |
| `_display_tool_outputs` | main.py:211 | Yes — `console.print()`, `Panel` | No |
| `_patch_dangling_tool_calls` | main.py:118 | No (pure function) | No |
| `_is_safe_command` | _approval.py:4 | No (pure function) | No |

---

## Design

### New module: `co_cli/_orchestrate.py`

Single responsibility: run the agent, handle deferred approvals via an injected callback, patch interrupts. No console imports.

### Approval callback protocol

```python
# co_cli/_orchestrate.py

from collections.abc import Awaitable, Callable
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
) -> tuple[Any, list]:
    """Run agent, loop through deferred approvals, return (result, updated_history).

    Handles:
    - agent.run() invocation
    - DeferredToolRequests loop with callback-driven approval
    - Safe-command auto-approval (delegates to _is_safe_command)
    - KeyboardInterrupt → _patch_dangling_tool_calls
    """
    ...
```

### CLI callback (replaces inline Prompt.ask)

```python
# co_cli/main.py — or co_cli/_cli_approval.py

class CliApprovalCallback:
    """Interactive y/n/a approval using rich Prompt."""

    def __init__(self, console: Console):
        self.console = console

    async def __call__(self, tool_name, args, *, auto_approved):
        if auto_approved:
            return ApprovalDecision(approved=True, yolo=False)
        # ... Prompt.ask logic moved here ...
```

### What moves where

| Current location | Destination | Notes |
|---|---|---|
| `_handle_approvals()` main.py:151 | `_orchestrate.run_with_approvals()` | Core loop logic; approval UX injected via callback |
| `_CHOICES_HINT` main.py:148 | `main.py` (stays) | Display-only, used by CLI callback |
| `_patch_dangling_tool_calls()` main.py:118 | `_orchestrate.py` | Pure function, no dependencies |
| `_is_safe_command()` _approval.py:4 | `_approval.py` (stays) | Already extracted |
| `_display_tool_outputs()` main.py:211 | `_orchestrate.py` or removed | Streaming TODO replaces this; if pre-streaming, move it |
| SIGINT handler swap main.py:159 | CLI callback | Signal handling is a UI concern |

### chat_loop after extraction

```python
async def chat_loop():
    agent, model_settings, tool_names = get_agent()
    deps = create_deps()
    approval_cb = CliApprovalCallback(console)
    # ...
    while True:
        # ... input handling ...
        console.print("[dim]Co is thinking...[/dim]")
        try:
            result, message_history = await run_with_approvals(
                agent, user_input,
                deps=deps,
                message_history=message_history,
                model_settings=model_settings,
                usage_limits=UsageLimits(request_limit=settings.max_request_limit),
                approval_callback=approval_cb,
            )
            console.print(Markdown(result.output))
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[dim]Interrupted.[/dim]")
```

---

## Implementation Plan

### Items

- [ ] Create `co_cli/_orchestrate.py` with `ApprovalCallback` protocol and `run_with_approvals()`
- [ ] Move `_patch_dangling_tool_calls()` from `main.py` to `_orchestrate.py`
- [ ] Move safe-command auto-approval logic into `run_with_approvals()` (calls `_is_safe_command`)
- [ ] Create `CliApprovalCallback` class in `main.py` (or `_cli_approval.py`) wrapping `Prompt.ask`
- [ ] Move SIGINT handler swap into `CliApprovalCallback`
- [ ] Refactor `chat_loop()` inner run block to call `run_with_approvals()`
- [ ] Move `_display_tool_outputs()` into `_orchestrate.py` (pre-streaming) or remove if streaming lands first
- [ ] Add functional test for `_patch_dangling_tool_calls` in new test file (see `TODO-approval-interrupt-tests.md`)
- [ ] Add functional test for `run_with_approvals` using a test callback (auto-approve all)
- [ ] Verify streaming TODO (`TODO-streaming-tool-output.md`) design still applies after extraction

### File changes

| File | Change |
|---|---|
| `co_cli/_orchestrate.py` | New — `ApprovalCallback`, `run_with_approvals`, `_patch_dangling_tool_calls` |
| `co_cli/main.py` | Remove extracted functions; add `CliApprovalCallback`; call `run_with_approvals()` |
| `co_cli/_approval.py` | No change — `_is_safe_command` stays |
| `tests/test_orchestrate.py` | New — tests for patch + orchestration with auto-approve callback |

### Ordering

This should land **before** the streaming migration (`TODO-streaming-tool-output.md`). The streaming TODO replaces the `agent.run()` call inside `run_with_approvals()` rather than inside the entangled `chat_loop()`, making both changes smaller.
