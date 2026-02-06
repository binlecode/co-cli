# TODO: Migrate to pydantic-ai Approval Flow

**Goal:** Replace `rich.prompt.Confirm` inside tools with pydantic-ai's native `requires_approval=True` + `DeferredToolRequests` pattern.

**Why:** Current pattern blocks inside the tool — the LLM never sees that approval happened, can't react to rejections, and tools own UI concerns they shouldn't. The pydantic-ai pattern gives the chat loop control over approval UX and lets the LLM see denial reasons.

**Reference:** https://ai.pydantic.dev/deferred-tools/

---

## Affected Tools

| Tool | File | Current approval |
|------|------|-----------------|
| `run_shell_command` | `shell.py` | `Confirm.ask(f"Execute command: {cmd}?")` |
| `draft_email` | `google_gmail.py` | `Confirm.ask(f"Draft email to {to}?")` |
| `post_slack_message` | `slack.py` | `Confirm.ask(f"Send Slack message to {channel}?")` |

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

### 2. Tools — remove Confirm.ask blocks

Each tool drops `rich.prompt.Confirm`, `_console`, and the `auto_confirm` check. The tool body assumes approval already granted.

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

**google_gmail.py** `draft_email` — remove the `Confirm.ask` block (lines 130-136), keep the rest.

**slack.py** `post_slack_message` — remove the `Confirm.ask` block (lines 25-31), keep the rest.

### 3. `main.py` — handle `DeferredToolRequests` in chat loop

```python
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from rich.prompt import Confirm

async def chat_loop():
    agent = get_agent()
    deps = create_deps()
    # ...
    message_history = []
    try:
        while True:
            # ... get user_input ...
            result = await agent.run(
                user_input, deps=deps, message_history=message_history
            )

            if isinstance(result.output, DeferredToolRequests):
                result = await _handle_approvals(agent, deps, result)

            message_history = result.all_messages()
            console.print(Markdown(result.output))
    finally:
        deps.sandbox.cleanup()


async def _handle_approvals(agent, deps, result):
    """Prompt user for each pending tool call, then resume."""
    approvals = DeferredToolResults()
    for call in result.output.approvals:
        desc = f"{call.tool_name}({call.args})"
        approved = Confirm.ask(f"Approve [bold]{desc}[/bold]?", default=False, console=console)
        approvals.approvals[call.tool_call_id] = approved
    return await agent.run(
        None, deps=deps,
        message_history=result.all_messages(),
        deferred_tool_results=approvals,
    )
```

### 4. `deps.py` — remove `auto_confirm`

Drop the `auto_confirm: bool = False` field from `CoDeps`. Remove from `create_deps()` in `main.py` and `auto_confirm` from `config.py`.

## Checklist

- [ ] Add `DeferredToolRequests` to agent `output_type`
- [ ] Register 3 tools with `requires_approval=True`
- [ ] Remove `Confirm.ask` + `auto_confirm` from shell.py, google_gmail.py, slack.py
- [ ] Add `_handle_approvals()` to chat loop in main.py
- [ ] Remove `auto_confirm` from CoDeps, create_deps(), config.py
- [ ] Functional test: `uv run co chat` → trigger each tool, verify approval prompt
