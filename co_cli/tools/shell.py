import logging

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools._shell_policy import ShellDecisionEnum, evaluate_shell_command
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output

logger = logging.getLogger(__name__)


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True, max_result_size=30_000
)
async def shell(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> ToolReturn:
    """Execute a shell command and return combined stdout + stderr as text.

    Use for git commands, package managers, builds, scripts, and system info.

    Do not use shell for file reads, content search, or tasks with dedicated tools:
    - file_read instead of cat/head/tail
    - file_grep instead of grep/rg
    - file_glob instead of ls/find
    - web_fetch instead of curl for web pages
    - obsidian_search / obsidian_read instead of grep/cat on the Obsidian vault
    - drive_search / drive_read instead of manual API calls
    - file_write / file_patch instead of shell redirection for workspace file creation or editing
    - task_start instead of shell for detached long-running work

    Commands run in the project working directory. DENY-pattern commands are
    blocked. Safe-prefix commands execute directly. All others require user
    approval.

    No interactive input — commands that prompt for stdin will hang and timeout.
    Long-running commands are killed after timeout seconds (capped by
    shell_max_timeout).

    On failure, the tool returns the exit code and combined output as a tool
    result — read it to diagnose the failure (wrong flags, missing binary,
    permission issue) and retry with a corrected command. Consider platform
    differences: macOS uses BSD utilities (stat -f, not -c; sed -i '' not -i).

    Args:
        cmd: Shell command string (e.g. "git log --oneline -10",
             "python scripts/run.py", "uv run pytest").
        timeout: Max seconds to wait (default 120). Increase for builds or
                 long scripts (e.g. 300). Capped by shell_max_timeout.
    """
    # Policy check: DENY → error, ALLOW → execute, REQUIRE_APPROVAL → defer for user approval
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell.safe_commands)
    if policy.decision == ShellDecisionEnum.DENY:
        logger.debug(
            "tool_denied tool_name=%s subject_kind=%s subject_value=%s",
            "shell",
            "shell",
            cmd.split()[0] if cmd.strip() else "",
        )
        return tool_error(policy.reason, ctx=ctx)
    if policy.decision == ShellDecisionEnum.REQUIRE_APPROVAL and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"cmd": cmd})
    # ALLOW or tool_call_approved: fall through to execution

    effective = min(timeout, ctx.deps.config.shell.max_timeout)
    try:
        exit_code, output = await ctx.deps.shell.run_command(cmd, timeout=effective)
        if exit_code == 0:
            return tool_output(output, ctx=ctx)
        return tool_error(f"exit {exit_code}:\n{output}", ctx=ctx)
    except RuntimeError as e:
        raise ModelRetry(
            f"Shell: command timed out after {effective}s. "
            f"Use a shorter command or increase timeout."
        ) from e
    except Exception as e:
        raise ModelRetry(f"Shell: unexpected error ({e}). Try a different approach.") from e
