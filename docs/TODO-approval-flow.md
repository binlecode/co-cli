# TODO: Migrate to pydantic-ai Approval Flow

**Goal:** Replace `confirm_or_yolo()` inside tools with pydantic-ai's native `requires_approval=True` + `DeferredToolRequests` pattern.

**Why:** Current pattern blocks inside the tool — the LLM never sees that approval happened, can't react to rejections, and tools own UI concerns they shouldn't. The pydantic-ai pattern gives the chat loop control over approval UX and lets the LLM see denial reasons.

**Reference:** https://ai.pydantic.dev/deferred-tools/

**Prerequisite:** Session-yolo (`y/n/a`) is already implemented via `tools/_confirm.py` — see `docs/TODO-session-yolo.md`. This migration replaces that helper entirely.

---

## Current State (post session-yolo)

All 3 tools use `confirm_or_yolo(ctx, prompt)` from `co_cli/tools/_confirm.py`:

| Tool | File | Current approval |
|------|------|-----------------|
| `run_shell_command` | `shell.py` | `confirm_or_yolo(ctx, f"Execute command: {cmd}?")` |
| `draft_email` | `google_gmail.py` | `confirm_or_yolo(ctx, f"Draft email to {to}?")` |
| `post_slack_message` | `slack.py` | `confirm_or_yolo(ctx, f"Send Slack message to {channel}?")` |

## Changes

### 1. `agent.py` — add `DeferredToolRequests` as output type

```python
from pydantic_ai import Agent, DeferredToolRequests

agent: Agent[CoDeps, str] = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    output_type=[str, DeferredToolRequests],  # add
)

# Register with requires_approval
agent.tool(run_shell_command, requires_approval=True)
agent.tool(draft_email, requires_approval=True)
agent.tool(post_slack_message, requires_approval=True)
```

### 2. Tools — remove `confirm_or_yolo` calls

Each tool drops the `confirm_or_yolo` import and call. The tool body assumes approval already granted.

**shell.py:**
```python
from pydantic_ai import RunContext
from co_cli.deps import CoDeps

def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    """Execute a shell command in a sandboxed Docker container."""
    try:
        return ctx.deps.sandbox.run_command(cmd)
    except Exception as e:
        return f"Error executing command: {e}"
```

**google_gmail.py** `draft_email` — remove the `confirm_or_yolo` call, keep the rest.

**slack.py** `post_slack_message` — remove the `confirm_or_yolo` call, keep the rest.

### 3. `main.py` — handle `DeferredToolRequests` in chat loop

The `_handle_approvals` function should preserve the session-yolo `[y/n/a(yolo)]` UX:

```python
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from rich.prompt import Prompt

async def chat_loop():
    agent, model_settings = get_agent()
    deps = create_deps()
    # ...
    message_history = []
    try:
        while True:
            # ... get user_input ...
            result = await agent.run(
                user_input, deps=deps, message_history=message_history,
                model_settings=model_settings,
            )

            if isinstance(result.output, DeferredToolRequests):
                result = await _handle_approvals(agent, deps, result)

            message_history = result.all_messages()
            console.print(Markdown(result.output))
    finally:
        deps.sandbox.cleanup()


async def _handle_approvals(agent, deps, result):
    """Prompt user [y/n/a(yolo)] for each pending tool call, then resume."""
    approvals = DeferredToolResults()
    for call in result.output.approvals:
        if deps.auto_confirm:
            approvals.approvals[call.tool_call_id] = True
            continue
        desc = f"{call.tool_name}({call.args})"
        choice = Prompt.ask(
            f"Approve [bold]{desc}[/bold]?",
            choices=["y", "n", "a"], default="n", console=console,
        )
        if choice == "a":
            deps.auto_confirm = True
            approvals.approvals[call.tool_call_id] = True
        else:
            approvals.approvals[call.tool_call_id] = (choice == "y")
    return await agent.run(
        None, deps=deps,
        message_history=result.all_messages(),
        deferred_tool_results=approvals,
        model_settings=model_settings,
    )
```

### 4. Cleanup — remove `_confirm.py`

After migration, delete `co_cli/tools/_confirm.py` — approval UX moves to the chat loop.

### 5. `deps.py` — keep `auto_confirm`

`auto_confirm` stays on `CoDeps` for session-yolo support (set by `_handle_approvals` when user picks `a`). Remove from `config.py` only if we no longer want a config-level default.

## Checklist

- [ ] Add `DeferredToolRequests` to agent `output_type`
- [ ] Register 3 tools with `requires_approval=True`
- [ ] Remove `confirm_or_yolo` calls from shell.py, google_gmail.py, slack.py
- [ ] Add `_handle_approvals()` with `[y/n/a(yolo)]` to chat loop in main.py
- [ ] Delete `co_cli/tools/_confirm.py`
- [ ] Functional test: `uv run co chat` → trigger each tool, verify `[y/n/a(yolo)]` prompt + yolo mode
