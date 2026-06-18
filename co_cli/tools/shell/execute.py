import logging
from pathlib import Path

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.background import adopt_running_process
from co_cli.tools.files.fs_guards import enforce_write_boundary
from co_cli.tools.shell._exit_codes import benign_exit_note, shell_exit_meaning
from co_cli.tools.shell_backend import YieldedProcess
from co_cli.tools.shell_policy import ShellDecisionEnum, evaluate_shell_command
from co_cli.tools.tool_io import tool_error, tool_output

logger = logging.getLogger(__name__)


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
async def shell_exec(
    ctx: RunContext[CoDeps],
    cmd: str,
    timeout_seconds: int = 120,
    work_dir: str | None = None,
    pty: bool = False,
) -> ToolReturn:
    """Execute a shell command and return combined stdout + stderr as text.

    Use for git commands, package managers, builds, scripts, and system info.

    Do not use shell for file reads, content search, or tasks with dedicated tools:
    - file_read instead of cat/head/tail
    - file_search instead of grep/rg/find/ls
    - web_fetch instead of curl for web pages
    - google_drive_search / google_drive_read instead of manual API calls
    - file_write / file_patch instead of shell redirection for workspace file creation or editing
    - skill_edit / skill_patch instead of shell for creating or editing skill files
      (~/.co-cli/skills/<name>/SKILL.md) — a direct shell write bypasses the
      security scan, atomic write, catalog reload, and usage tracking
    - task_start instead of shell for detached long-running work

    Commands run in the project working directory. DENY-pattern commands are
    blocked. Safe-prefix commands execute directly. All others require user
    approval.

    No interactive stdin — a prompting command hangs until timeout; for prompts,
    launch via task_start and use the task_write loop. A command still running
    after a short window auto-promotes to a background task and returns a task
    handle (track with task_status, stop with task_cancel); otherwise it is
    killed at timeout (capped by shell.max_timeout_seconds).

    On failure, the tool returns the exit code and combined output as a tool
    result — read it to diagnose the failure (wrong flags, missing binary,
    permission issue) and retry with a corrected command. Benign non-zero exits
    (grep finding no matches, diff finding differences) come back as normal
    output, not failures — do not retry them. Consider platform differences:
    macOS uses BSD utilities (stat -f, not -c; sed -i '' not -i).

    Args:
        cmd: Shell command string (e.g. "git log --oneline -10",
             "python scripts/run.py", "uv run pytest").
        timeout_seconds: Max seconds to wait (default 120). Increase for builds or
                 long scripts (e.g. 300). Capped by shell.max_timeout_seconds.
        work_dir: Optional subdirectory (relative to workspace root) to run the
                 command in. Default None = the workspace root. Prevents
                 directory traversal.
        pty: Run under a pseudo-terminal for output fidelity only (isatty,
             ANSI colors) — not interactive drive; raw ANSI preserved.
    """
    # Policy check: DENY → error, ALLOW → execute, REQUIRE_APPROVAL → defer for user approval
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell.safe_commands)
    if policy.decision == ShellDecisionEnum.DENY:
        logger.debug(
            "tool_denied tool_name=%s subject_kind=%s subject_value=%s",
            "shell_exec",
            "shell_exec",
            cmd.split()[0] if cmd.strip() else "",
        )
        return tool_error(policy.reason, ctx=ctx)
    if policy.decision == ShellDecisionEnum.REQUIRE_APPROVAL and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"cmd": cmd})
    # ALLOW or tool_call_approved: fall through to execution

    # Shell cwd is anchored to the workspace dir — the same write/cwd anchor as
    # file_write/file_patch (BC-1). An explicit cwd is always passed (the backend
    # holds no default), so a configured workspace_path takes effect even when no
    # work_dir is given.
    workspace_dir = ctx.deps.workspace_dir
    if work_dir is not None:
        try:
            resolved_cwd = str(enforce_write_boundary(Path(work_dir), workspace_dir))
        except ValueError as e:
            return tool_error(str(e), ctx=ctx)
    else:
        resolved_cwd = str(workspace_dir)

    effective = min(timeout_seconds, ctx.deps.config.shell.max_timeout_seconds)
    skill_env = ctx.deps.runtime.active_skill_env or None
    # Auto-yield is non-pty only — the pty path has no proc.stdout to hand off.
    yield_window_seconds = 0 if pty else ctx.deps.config.shell.yield_window_seconds
    try:
        result = await ctx.deps.shell.run_command(
            cmd,
            timeout_seconds=effective,
            cwd=resolved_cwd,
            extra_env=skill_env,
            pty=pty,
            yield_window_seconds=yield_window_seconds,
        )
        if isinstance(result, YieldedProcess):
            # The command outlived the yield window. Adopt the live process into
            # a background task and hand the model a task handle. The command
            # already cleared the DENY/approval gate above before it spawned —
            # adoption is a hand-off of an authorized process, not a new
            # execution, so it does not re-gate.
            state = await adopt_running_process(
                result.process,
                command=cmd,
                cwd=resolved_cwd,
                session=ctx.deps.session,
                prefix_bytes=result.prefix_bytes,
                skill_env=ctx.deps.runtime.active_skill_env or {},
            )
            partial = result.prefix_bytes.decode("utf-8", errors="replace").strip()
            display = (
                f"Command still running after {yield_window_seconds}s — promoted to "
                f"background task [{state.task_id}]. Use task_status({state.task_id}) "
                f"to check on it or task_cancel({state.task_id}) to stop it."
            )
            if partial:
                display += f"\nPartial output so far:\n{partial}"
            return tool_output(display, ctx=ctx, task_id=state.task_id, status="running")
        exit_code, output = result
        if exit_code == 0:
            return tool_output(output, ctx=ctx)
        # Benign non-zero exits (grep with no matches, diff with differences)
        # ran correctly — return them as normal output so the model does not
        # mistake a successful "found nothing" for a failure and loop.
        note = benign_exit_note(cmd, exit_code)
        if note is not None:
            return tool_output(f"[ran OK — {note}]\n{output}", ctx=ctx)
        meaning = shell_exit_meaning(exit_code)
        header = f"exit {exit_code} ({meaning})" if meaning else f"exit {exit_code}"
        return tool_error(f"{header}:\n{output}", ctx=ctx)
    except RuntimeError as e:
        raise ModelRetry(
            f"Shell: command timed out after {effective}s. "
            f"Use a shorter command or increase timeout."
        ) from e
    except Exception as e:
        raise ModelRetry(f"Shell: unexpected error ({e}). Try a different approach.") from e
