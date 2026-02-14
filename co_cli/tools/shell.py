from typing import Any

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.tools._errors import terminal_error


async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> str | dict[str, Any]:
    """Execute a shell command as a subprocess with approval.

    Use this tool for: listing files (ls), reading files (cat), running scripts,
    git commands, or any terminal/shell operation.

    Args:
        cmd: The shell command to execute (e.g., 'ls -la', 'cat file.txt', 'pwd').
        timeout: Max seconds to wait (default 120). Use higher values for
                 builds or long-running scripts. Capped by shell_max_timeout.
    """
    effective = min(timeout, ctx.deps.shell_max_timeout)
    try:
        return await ctx.deps.shell.run_command(cmd, timeout=effective)
    except RuntimeError as e:
        msg = str(e)
        if "timed out" in msg.lower():
            raise ModelRetry(
                f"Shell: command timed out after {effective}s. "
                f"Use a shorter command or increase timeout.\n{msg}"
            )
        if "permission denied" in msg.lower():
            return terminal_error(
                "Shell: permission denied. The current user may lack access. "
                "Try a different path or approach."
            )
        raise ModelRetry(f"Shell: command failed ({e}). Check command syntax or try a different approach.")
    except Exception as e:
        raise ModelRetry(f"Shell: unexpected error ({e}). Try a different approach.")
