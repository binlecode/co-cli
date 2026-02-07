"""E2E test: LLM-controlled command timeout in the Docker sandbox.

Reproduces the hang bug (greeting_bot.py with infinite loop) and verifies
that the full timeout pipeline works end-to-end:

  1. Agent receives a prompt to edit greeting_bot.py (remove infinite loop,
     add 3-round finite loop with 2s sleep) and then run it.
  2. Shell tool calls go through DeferredToolRequests → auto-approved here.
  3. sandbox.run_command() wraps the command with coreutils `timeout` inside
     the container AND asyncio.wait_for() on the Python side.
  4. PYTHONUNBUFFERED=1 ensures partial output is captured even if the
     process is killed mid-run.
  5. The script finishes normally (~6s), well within the 15s timeout.

This exercises every layer of the timeout design:
  - Tool parameter: LLM sets timeout=15 in the tool call
  - Hard ceiling: sandbox_max_timeout (600s default) caps the value
  - In-container: coreutils `timeout 15 sh -c '...'` (would kill at 15s)
  - Python-side: asyncio.wait_for(..., timeout=20) (safety net at 15+5s)
  - Partial output: PYTHONUNBUFFERED=1 on exec_run environment

Prerequisites:
  - Docker running with `co-cli-sandbox` image built
  - LLM provider configured (gemini_api_key or ollama running)
  - greeting_bot.py present in the project root

Usage:
    uv run python scripts/e2e_timeout.py
"""
import asyncio
import json

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.main import create_deps


# Prompt instructs the LLM to both edit and run the script.
# "Use timeout=15" tests that the LLM passes the timeout param through
# to the tool schema (visible in the auto-approve log below).
PROMPT = (
    "Update greeting_bot.py so it prints a greeting every 2 seconds and "
    "stops after 3 rounds (about 6 seconds total). Remove the infinite loop. "
    "Then run the updated script and show me the output. "
    "Use timeout=15 for the shell command."
)


async def main():
    # Build the agent and deps exactly as the chat loop does.
    agent, model_settings = get_agent()
    deps = create_deps()

    # Auto-approve all shell commands so the script runs non-interactively.
    # In normal chat, the user sees [y/n/a] prompts for each tool call.
    deps.auto_confirm = True

    try:
        print(f">>> Sending prompt:\n{PROMPT}\n")

        # First agent.run() — the LLM will plan tool calls (read file,
        # rewrite file, run script). Side-effectful tools like
        # run_shell_command return DeferredToolRequests instead of executing
        # immediately, so we must approve them in the loop below.
        result = await agent.run(
            PROMPT,
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=25),
        )

        # Approval loop: mirrors _handle_approvals() in main.py.
        # Each iteration approves all pending tool calls, then resumes
        # the agent. The agent may produce more deferred calls (e.g.,
        # first it reads the file, then rewrites it, then runs it),
        # so we loop until the output is a plain string.
        while isinstance(result.output, DeferredToolRequests):
            approvals = DeferredToolResults()
            for call in result.output.approvals:
                # Parse args for display (they arrive as JSON string or dict)
                args = call.args
                if isinstance(args, str):
                    args = json.loads(args)
                args = args or {}
                args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
                print(f"  [auto-approve] {call.tool_name}({args_str})")
                approvals.approvals[call.tool_call_id] = True

            # Resume with approvals. Pass message_history so the agent
            # retains context from prior turns. The first arg is None
            # because we're continuing, not sending a new user message.
            result = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model_settings=model_settings,
                usage_limits=UsageLimits(request_limit=25),
            )

        print(f"\n{'='*60}")
        print("AGENT RESPONSE:")
        print(f"{'='*60}")
        print(result.output)
    finally:
        # Always clean up the sandbox container, even on error.
        deps.sandbox.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
