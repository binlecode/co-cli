from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from co_cli.deps import CoDeps
from co_cli.tools.tool_errors import tool_error
from co_cli.tools.tool_output import ToolResult, tool_output
from co_cli.tools._shell_policy import ShellDecisionEnum, evaluate_shell_command


async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> ToolResult:
    """Execute a shell command and return combined stdout + stderr as text.

    Use for any terminal operation: file listing (ls, find), file reading
    (cat, head), git commands, package managers (pip, npm), builds, scripts,
    or system info (whoami, df, uname).

    Commands run in the project working directory. DENY-pattern commands are
    blocked immediately inside the tool. Safe-prefix commands execute directly.
    All other commands require user approval before execution.

    Returns the combined stdout and stderr output as a ToolResult.

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
    # Policy check: DENY → error, ALLOW → execute, REQUIRE_APPROVAL → defer for user approval
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell_safe_commands)
    if policy.decision == ShellDecisionEnum.DENY:
        return tool_error(policy.reason, ctx=ctx)
    if policy.decision == ShellDecisionEnum.REQUIRE_APPROVAL:
        if not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"cmd": cmd})
    # ALLOW or tool_call_approved: fall through to execution

    effective = min(timeout, ctx.deps.config.shell_max_timeout)
    try:
        output = await ctx.deps.shell.run_command(cmd, timeout=effective)
        return tool_output(output, ctx=ctx)
    except RuntimeError as e:
        msg = str(e)
        if "timed out" in msg.lower():
            raise ModelRetry(
                f"Shell: command timed out after {effective}s. "
                f"Use a shorter command or increase timeout.\n{msg}"
            )
        if "permission denied" in msg.lower():
            return tool_error(
                "Shell: permission denied. The current user may lack access. "
                "Try a different path or approach.",
                ctx=ctx,
            )
        raise ModelRetry(f"Shell: command failed ({e}). Check command syntax or try a different approach.")
    except Exception as e:
        raise ModelRetry(f"Shell: unexpected error ({e}). Try a different approach.")
