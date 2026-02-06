from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.tools._confirm import confirm_or_yolo


def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    """Execute a shell command in a sandboxed Docker container.

    Use this tool for: listing files (ls), reading files (cat), running scripts,
    git commands, or any terminal/shell operation.

    Args:
        cmd: The shell command to execute (e.g., 'ls -la', 'cat file.txt', 'pwd').
    """
    if not confirm_or_yolo(ctx, f"Execute command: [bold]{cmd}[/bold]?"):
        return "Command cancelled by user."

    try:
        return ctx.deps.sandbox.run_command(cmd)
    except Exception as e:
        return f"Error executing command: {e}"
