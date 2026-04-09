from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools._shell_policy import ShellDecisionEnum, evaluate_shell_command
from co_cli.tools.tool_errors import tool_error
from co_cli.tools.tool_output import tool_output


async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> ToolReturn:
    """Execute a shell command and return combined stdout + stderr as text.

    Use for git commands, package managers, builds, scripts, and system info.

    Do not use shell for file reads or content search — use the dedicated tools:
    - read_file instead of cat/head/tail
    - find_in_files instead of grep/rg
    - list_directory instead of ls/find
    - web_fetch instead of curl for web pages
    - search_notes / read_note instead of grep/cat on the Obsidian vault
    - search_drive_files / read_drive_file instead of manual API calls

    Commands run in the project working directory. DENY-pattern commands are
    blocked. Safe-prefix commands execute directly. All others require user
    approval.

    No interactive input — commands that prompt for stdin will hang and timeout.
    Long-running commands are killed after timeout seconds (capped by
    shell_max_timeout).

    Args:
        cmd: Shell command string (e.g. "git log --oneline -10",
             "python scripts/run.py", "uv run pytest").
        timeout: Max seconds to wait (default 120). Increase for builds or
                 long scripts (e.g. 300). Capped by shell_max_timeout.
    """
    # Policy check: DENY → error, ALLOW → execute, REQUIRE_APPROVAL → defer for user approval
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell.safe_commands)
    if policy.decision == ShellDecisionEnum.DENY:
        return tool_error(policy.reason, ctx=ctx)
    if policy.decision == ShellDecisionEnum.REQUIRE_APPROVAL and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"cmd": cmd})
    # ALLOW or tool_call_approved: fall through to execution

    effective = min(timeout, ctx.deps.config.shell.max_timeout)
    try:
        output = await ctx.deps.shell.run_command(cmd, timeout=effective)
        return tool_output(output, ctx=ctx)
    except RuntimeError as e:
        msg = str(e)
        if "timed out" in msg.lower():
            raise ModelRetry(
                f"Shell: command timed out after {effective}s. "
                f"Use a shorter command or increase timeout.\n{msg}"
            ) from e
        if "permission denied" in msg.lower():
            return tool_error(
                "Shell: permission denied. The current user may lack access. "
                "Try a different path or approach.",
                ctx=ctx,
            )
        raise ModelRetry(
            f"Shell: command failed ({e}). Check command syntax or try a different approach."
        ) from e
    except Exception as e:
        raise ModelRetry(f"Shell: unexpected error ({e}). Try a different approach.") from e
