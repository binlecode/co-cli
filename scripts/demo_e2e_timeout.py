"""E2E test: LLM-controlled command timeout in the Docker sandbox.

Demonstrates timeout functionality by having the agent:
1. Create a simple script with sleep/loops
2. Edit the script to add timeouts
3. Run the script with timeout parameter
4. Clean up temporary files

This exercises every layer of the timeout design:
  - Tool parameter: LLM sets timeout=15 in the tool call
  - Hard ceiling: sandbox_max_timeout (600s default) caps the value
  - In-container: coreutils `timeout 15 sh -c '...'` (would kill at 15s)
  - Python-side: asyncio.wait_for(..., timeout=20) (safety net at 15+5s)
  - Partial output: PYTHONUNBUFFERED=1 on exec_run environment

Prerequisites:
  - Docker running with `co-cli-sandbox` image built
  - LLM provider configured (gemini_api_key or ollama running)

Usage:
    uv run python scripts/demo_e2e_timeout.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.main import create_deps


# Initial greeting bot with infinite loop (simulates hang bug)
INITIAL_GREETING_BOT = '''#!/usr/bin/env python3
"""Simple greeting bot that runs indefinitely."""
import time

print("Starting greeting bot...")
while True:
    print("Hello!")
    time.sleep(2)
'''


async def main():
    # Create temporary file for the demo
    temp_file = Path(tempfile.gettempdir()) / "demo_greeting_bot.py"

    try:
        # Write initial version with infinite loop
        temp_file.write_text(INITIAL_GREETING_BOT)
        print(f">>> Created temp file: {temp_file}")

        # Prompt instructs the LLM to edit and run the script
        # "Use timeout=15" tests that the LLM passes the timeout param through
        prompt = (
            f"Update {temp_file} so it prints a greeting every 2 seconds and "
            "stops after 3 rounds (about 6 seconds total). Remove the infinite loop. "
            "Then run the updated script and show me the output. "
            "Use timeout=15 for the shell command."
        )

        # Build the agent and deps exactly as the chat loop does
        agent, model_settings = get_agent()
        deps = create_deps()

        # Auto-approve all shell commands so the script runs non-interactively
        deps.auto_confirm = True

        print(f"\n>>> Sending prompt:\n{prompt}\n")

        # First agent.run() — the LLM will plan tool calls (read file,
        # rewrite file, run script). Side-effectful tools like
        # run_shell_command return DeferredToolRequests instead of executing
        # immediately, so we must approve them in the loop below.
        result = await agent.run(
            prompt,
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
        # Always clean up temp file and sandbox container
        if temp_file.exists():
            temp_file.unlink()
            print(f"\n>>> Cleaned up temp file: {temp_file}")
        deps.sandbox.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
