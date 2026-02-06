"""Shared approval prompt with session-level yolo mode."""

from rich.prompt import Prompt
from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.display import console

_CHOICES_HINT = " [[green]y[/green]/[red]n[/red]/[bold orange3]a[/bold orange3](yolo)]"


def confirm_or_yolo(ctx: RunContext[CoDeps], prompt: str) -> bool:
    """Prompt user with y/n/a(yolo). Returns True if approved.

    Choices:
        y — approve this one
        n — deny this one
        a — approve all for the rest of the session (yolo)
    """
    if ctx.deps.auto_confirm:
        return True
    console.print(prompt + _CHOICES_HINT, end=" ")
    choice = Prompt.ask(
        "", choices=["y", "n", "a"], default="n",
        show_choices=False, show_default=False, console=console,
    )
    if choice == "a":
        ctx.deps.auto_confirm = True
        console.print("[bold orange3]YOLO mode enabled — auto-approving for this session[/bold orange3]")
        return True
    return choice == "y"
