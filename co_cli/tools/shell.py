from typing import Any

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.tools._errors import terminal_error


async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> str | dict[str, Any]:
    """Execute a shell command and return combined stdout + stderr as text.

    Use for any terminal operation: file listing (ls, find), file reading
    (cat, head), git commands, package managers (pip, npm), builds, scripts,
    or system info (whoami, df, uname).

    Commands run in the project working directory. The user must approve each
    command before execution (unless it matches a safe-prefix auto-approval rule).

    Returns the combined stdout and stderr output as a string.

    Caveats:
    - Long-running commands are killed after timeout seconds
    - timeout is capped by the configured shell_max_timeout (cannot exceed it)
    - No interactive input — commands that prompt for stdin will hang and timeout

    Prefer dedicated tools over shell equivalents when available:
    - Use web_fetch instead of curl for web pages
    - Use search_notes / read_note instead of grep / cat on the Obsidian vault
    - Use search_drive_files / read_drive_file instead of manual API calls

    Args:
        cmd: Shell command string (e.g. "ls -la", "git log --oneline -10",
             "python scripts/run.py").
        timeout: Max seconds to wait (default 120). Increase for builds or
                 long scripts (e.g. 300). Capped by shell_max_timeout.
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
