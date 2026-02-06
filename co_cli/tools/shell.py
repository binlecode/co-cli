from rich.console import Console
from rich.prompt import Confirm
from pydantic_ai import RunContext

from co_cli.deps import CoDeps

_console = Console()


def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    """Execute a shell command in a sandboxed Docker container.

    Use this tool for: listing files (ls), reading files (cat), running scripts,
    git commands, or any terminal/shell operation.

    Args:
        cmd: The shell command to execute (e.g., 'ls -la', 'cat file.txt', 'pwd').
    """
    # Human-in-the-loop confirmation (temporary until DeferredToolRequests migration)
    if not ctx.deps.auto_confirm:
        if not Confirm.ask(f"Execute command: [bold]{cmd}[/bold]?", default=False, console=_console):
            return "Command cancelled by user."

    try:
        return ctx.deps.sandbox.run_command(cmd)
    except Exception as e:
        return f"Error executing command: {e}"
