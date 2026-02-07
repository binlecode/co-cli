from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    """Execute a shell command in a sandboxed Docker container.

    Use this tool for: listing files (ls), reading files (cat), running scripts,
    git commands, or any terminal/shell operation.

    Args:
        cmd: The shell command to execute (e.g., 'ls -la', 'cat file.txt', 'pwd').
    """
    try:
        return ctx.deps.sandbox.run_command(cmd)
    except Exception as e:
        raise ModelRetry(f"Command failed ({e})")
