from typing import Any

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from co_cli._exec_approvals import find_approved, load_approvals, update_last_used
from co_cli.deps import CoDeps
from co_cli._shell_policy import ShellDecision, evaluate_shell_command
from co_cli.tools._errors import terminal_error


async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120) -> str | dict[str, Any]:
    """Execute a shell command and return combined stdout + stderr as text.

    Use for any terminal operation: file listing (ls, find), file reading
    (cat, head), git commands, package managers (pip, npm), builds, scripts,
    or system info (whoami, df, uname).

    Commands run in the project working directory. DENY-pattern commands are
    blocked immediately inside the tool. Safe-prefix commands execute directly.
    All other commands require user approval before execution.

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
    # Policy check: DENY → error, ALLOW → execute, REQUIRE_APPROVAL → check persistent or defer
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell_safe_commands)
    if policy.decision == ShellDecision.DENY:
        return terminal_error(policy.reason)
    if policy.decision == ShellDecision.REQUIRE_APPROVAL:
        entries = load_approvals(ctx.deps.config.exec_approvals_path)
        found = find_approved(cmd, entries)
        if found:
            update_last_used(ctx.deps.config.exec_approvals_path, found["id"])
        elif not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"cmd": cmd})
    # ALLOW, persistent approval, or tool_call_approved: fall through to execution

    effective = min(timeout, ctx.deps.config.shell_max_timeout)
    try:
        return await ctx.deps.services.shell.run_command(cmd, timeout=effective)
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
