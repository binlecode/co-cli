"""execute_code — thin shell tool for running code interpreter commands."""

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools._shell_policy import ShellDecisionEnum, evaluate_shell_command
from co_cli.tools.tool_io import tool_error, tool_output


async def execute_code(ctx: RunContext[CoDeps], cmd: str, timeout: int = 60) -> ToolReturn:
    """Run a code interpreter command and return combined stdout + stderr.

    Use to run a code file or one-liner via an interpreter. The agent
    constructs the command; the user approves before execution.

    Examples: "python main.py", "node index.js", "uv run script.py",
              "npx ts-node app.ts", "ruby script.rb"

    Do not use for git, builds, or system queries — use run_shell_command instead.

    Args:
        cmd: Interpreter command to run (e.g. "python main.py").
        timeout: Max seconds (default 60). Capped by shell_max_timeout.
    """
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell.safe_commands)
    if policy.decision == ShellDecisionEnum.DENY:
        return tool_error(policy.reason, ctx=ctx)
    # Always require approval — code execution always has side effects.
    if not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"cmd": cmd})
    effective = min(timeout, ctx.deps.config.shell.max_timeout)
    try:
        exit_code, output = await ctx.deps.shell.run_command(cmd, timeout=effective)
        if exit_code == 0:
            return tool_output(output, ctx=ctx)
        return tool_error(f"exit {exit_code}:\n{output}", ctx=ctx)
    except RuntimeError as e:
        raise ModelRetry(
            f"execute_code: timed out after {effective}s. "
            f"Use a shorter command or increase timeout."
        ) from e
    except Exception as e:
        raise ModelRetry(f"execute_code: unexpected error ({e}).") from e
