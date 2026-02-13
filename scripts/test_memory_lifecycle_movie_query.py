#!/usr/bin/env python3
"""Test: memory lifecycle via movie query — natural prompt, observe behavior.

Runs a live agent conversation with a natural user prompt. The prompt does
NOT explicitly ask Co to save — proactive memory detection should trigger
save_memory autonomously. Exercises:
  1. Co researches the movie online (web_search, web_fetch)
  2. Co synthesizes and presents what it learned
  3. Co proactively detects memory-worthy content and calls save_memory
  4. Optionally: approve the save and show the memory file on disk

Environment defaults (override via env vars):
  LLM_PROVIDER=ollama
  OLLAMA_MODEL=qwen3:30b-a3b-thinking-2507-q8_0-agentic
  OLLAMA_NUM_CTX=262144

Usage:
    uv run python scripts/test_memory_lifecycle_movie_query.py
    uv run python scripts/test_memory_lifecycle_movie_query.py --approve
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.config import get_settings
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend

# ---------------------------------------------------------------------------
# Env defaults — merged from run_ollama_web_research_e2e.sh
# ---------------------------------------------------------------------------

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}


def _apply_env_defaults():
    for key, value in _ENV_DEFAULTS.items():
        if key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# Trace printer
# ---------------------------------------------------------------------------


def print_trace(messages: list) -> None:
    """Print a human-readable conversation trace."""
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content = str(part.content)
                    preview = content[:500] + "..." if len(content) > 500 else content
                    print(f"\n  [{i}] TOOL_RETURN({part.tool_name}):")
                    for line in preview.split("\n"):
                        print(f"       {line}")
                elif isinstance(part, RetryPromptPart):
                    print(f"\n  [{i}] RETRY({part.tool_name}): {part.content}")
                else:
                    kind = type(part).__name__
                    text = str(part)[:300]
                    print(f"\n  [{i}] {kind}: {text}")

        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    args = part.args
                    if hasattr(args, "args_dict"):
                        args_str = json.dumps(args.args_dict, indent=2)
                    else:
                        args_str = str(args)
                    preview = args_str[:500] + "..." if len(args_str) > 500 else args_str
                    print(f"\n  [{i}] TOOL_CALL: {part.tool_name}")
                    for line in preview.split("\n"):
                        print(f"       {line}")
                elif isinstance(part, TextPart):
                    print(f"\n  [{i}] TEXT: {part.content[:500]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    approve = "--approve" in sys.argv

    _apply_env_defaults()
    settings = get_settings()
    agent, model_settings, _ = get_agent()

    deps = CoDeps(
        sandbox=SubprocessBackend(),
        session_id="demo-movie-lifecycle",
        personality="finch",
        brave_search_api_key=settings.brave_search_api_key,
        web_policy=settings.web_policy,
    )

    prompt = (
        "Go online and learn about the movie Finch, "
        "then tell me about it — make it interesting."
    )

    print("=" * 70)
    print("  Memory Lifecycle Test — Co Learns About a Movie")
    print("=" * 70)
    print(f"\n  Model:  {os.environ.get('OLLAMA_MODEL', '?')}")
    print(f"  Prompt: {prompt}")
    print(f"  Approve save: {approve}")
    print()

    # --- Run agent ---
    print("--- Conversation Trace ---")
    result = await agent.run(
        prompt,
        deps=deps,
        model_settings=model_settings,
        usage_limits=UsageLimits(request_limit=25),
    )

    print_trace(result.all_messages())

    # --- Tool call summary ---
    tool_calls = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls.append(part.tool_name)

    print("\n--- Tool Call Sequence ---")
    for i, name in enumerate(tool_calls):
        print(f"  {i + 1}. {name}")

    # --- Result ---
    print("\n--- Result ---")
    if isinstance(result.output, DeferredToolRequests):
        for call in result.output.approvals:
            print(f"  Deferred: {call.tool_name} (id={call.tool_call_id})")

        if approve:
            print("\n--- Approving save_memory ---")
            approvals = DeferredToolResults()
            for call in result.output.approvals:
                approvals.approvals[call.tool_call_id] = True

            result2 = await agent.run(
                deferred_tool_results=approvals,
                message_history=result.all_messages(),
                deps=deps,
                model_settings=model_settings,
                usage_limits=UsageLimits(request_limit=5),
            )

            # Show final response
            output = result2.output if isinstance(result2.output, str) else str(result2.output)
            print(f"  Agent: {output[:500]}")

            # Show memory file on disk
            memory_dir = Path.cwd() / ".co-cli" / "knowledge" / "memories"
            if memory_dir.exists():
                files = sorted(memory_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                if files:
                    newest = files[0]
                    print(f"\n--- Memory File: {newest.name} ---")
                    print(newest.read_text(encoding="utf-8"))
        else:
            print("\n  (pass --approve to execute the save and show the memory file)")
    else:
        print(f"  Output: {result.output[:500] if isinstance(result.output, str) else result.output}")

    print()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
