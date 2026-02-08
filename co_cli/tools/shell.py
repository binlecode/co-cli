from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> str:
    """Execute a shell command in a sandboxed Docker container.

    Use this tool for: listing files (ls), reading files (cat), running scripts,
    git commands, or any terminal/shell operation.

    Args:
        cmd: The shell command to execute (e.g., 'ls -la', 'cat file.txt', 'pwd').
        timeout: Max seconds to wait (default 120). Use higher values for
                 builds or long-running scripts. Capped by sandbox_max_timeout.
    """
    effective = min(timeout, ctx.deps.sandbox_max_timeout)
    try:
        return await ctx.deps.sandbox.run_command(cmd, timeout=effective)
    except RuntimeError as e:
        msg = str(e)
        if "timed out" in msg.lower():
            raise ModelRetry(
                f"Shell: command timed out after {effective}s. "
                "Use a shorter command or increase timeout."
            )
        if "permission denied" in msg.lower():
            raise ModelRetry(
                "Shell: permission denied. The sandbox user may lack access. "
                "Try a different path or approach."
            )
        raise ModelRetry(f"Shell: command failed ({e}). Check command syntax or try a different approach.")
    except Exception as e:
        raise ModelRetry(f"Shell: unexpected error ({e}). Try a different approach.")
