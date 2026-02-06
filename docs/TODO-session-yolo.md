# TODO: Session-level "yolo" mode (approve-all)

**Goal:** Add `a` (all) option to approval prompts so users can approve all commands for the rest of the session — common CLI "yolo" pattern.

**Problem:** Every shell command, email draft, and Slack message requires individual `y/n` confirmation. No way to say "trust me for this session."

**UX:**
```
Execute command: ls -la? [y/n/a] (n): a
# → approves this command AND all future commands for the session
```

- `y` — approve this one
- `n` — deny this one
- `a` — approve all (yolo mode for rest of session)

---

## Design

When user picks `a`, flip `ctx.deps.auto_confirm = True` on the shared `CoDeps` dataclass. Since `deps` is a single mutable instance created once in `main.py` and passed to every `agent.run()`, this persists for the rest of the session. New sessions start fresh (`auto_confirm=False` by default).

## Changes

### 1. New `co_cli/tools/_confirm.py` — shared helper

```python
from rich.console import Console
from rich.prompt import Prompt
from pydantic_ai import RunContext
from co_cli.deps import CoDeps

_console = Console()

def confirm_or_yolo(ctx: RunContext[CoDeps], prompt: str) -> bool:
    """Prompt [y/n/a]. Returns True if approved. 'a' enables auto_confirm for session."""
    if ctx.deps.auto_confirm:
        return True
    choice = Prompt.ask(prompt, choices=["y", "n", "a"], default="n", console=_console)
    if choice == "a":
        ctx.deps.auto_confirm = True
        return True
    return choice == "y"
```

### 2. Update 3 tool files

Replace `Confirm.ask` + `auto_confirm` check with `confirm_or_yolo` in:
- `co_cli/tools/shell.py` — `run_shell_command`
- `co_cli/tools/google_gmail.py` — `draft_email`
- `co_cli/tools/slack.py` — `post_slack_message`

## Checklist

- [ ] Create `co_cli/tools/_confirm.py` with `confirm_or_yolo`
- [ ] Update `shell.py` — use `confirm_or_yolo`
- [ ] Update `google_gmail.py` — use `confirm_or_yolo`
- [ ] Update `slack.py` — use `confirm_or_yolo`
- [ ] Manual test: `uv run co chat` → shell command → press `a` → next command auto-approves
