"""Shared approval prompt with session-level yolo mode."""

from rich.console import Console
from rich.prompt import Prompt
from pydantic_ai import RunContext

from co_cli.deps import CoDeps

_console = Console()


def confirm_or_yolo(ctx: RunContext[CoDeps], prompt: str) -> bool:
    """Prompt user with [y/n/a]. Returns True if approved.

    Choices:
        y — approve this one
        n — deny this one
        a — approve all for the rest of the session (yolo mode)
    """
    if ctx.deps.auto_confirm:
        return True
    choice = Prompt.ask(prompt, choices=["y", "n", "a"], default="n", console=_console)
    if choice == "a":
        ctx.deps.auto_confirm = True
        return True
    return choice == "y"
