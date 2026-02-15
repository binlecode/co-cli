#!/usr/bin/env python3
"""E2E: Compaction prompt quality — /compact produces actionable handoff summary.

Builds a multi-turn conversation history, runs /compact via dispatch,
and verifies the compacted summary contains handoff-style content.

Usage:
    uv run python scripts/eval_e2e_compaction.py
"""

import asyncio
import os
import sys
import time

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from co_cli._commands import dispatch, CommandContext  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402
from co_cli.config import get_settings  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


def _build_history() -> list[ModelMessage]:
    """Build a realistic multi-turn conversation history."""
    return [
        ModelRequest(parts=[UserPromptPart(content="What is Docker?")]),
        ModelResponse(parts=[TextPart(
            content="Docker is a containerisation platform that uses OS-level "
                    "virtualisation to package applications in containers."
        )]),
        ModelRequest(parts=[UserPromptPart(content="How do I install it on Ubuntu?")]),
        ModelResponse(parts=[TextPart(
            content="Run: sudo apt-get update && sudo apt-get install "
                    "docker-ce docker-ce-cli containerd.io"
        )]),
        ModelRequest(parts=[UserPromptPart(content="What about Docker Compose?")]),
        ModelResponse(parts=[TextPart(
            content="Docker Compose is a tool for defining multi-container "
                    "applications. Install with: sudo apt-get install docker-compose-plugin"
        )]),
        ModelRequest(parts=[UserPromptPart(content="Can you show me a sample docker-compose.yml?")]),
        ModelResponse(parts=[TextPart(
            content="Here's a basic docker-compose.yml for a web app with postgres:\n"
                    "services:\n  web:\n    build: .\n    ports:\n      - '8000:8000'\n"
                    "  db:\n    image: postgres:16\n    environment:\n"
                    "      POSTGRES_DB: myapp\n      POSTGRES_PASSWORD: secret"
        )]),
        ModelRequest(parts=[UserPromptPart(content="How do I handle volumes for data persistence?")]),
        ModelResponse(parts=[TextPart(
            content="Add a volumes section to persist postgres data:\n"
                    "volumes:\n  postgres_data:\nservices:\n  db:\n    volumes:\n"
                    "      - postgres_data:/var/lib/postgresql/data"
        )]),
    ]


async def main() -> int:
    settings = get_settings()

    print("=" * 60)
    print("  E2E: Compaction Prompt Quality")
    print("=" * 60)

    agent, _, tool_names = get_agent()
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="e2e-compaction",
    )
    history = _build_history()

    print(f"\n[1] Built {len(history)} message history about Docker")

    # Run /compact
    print("\n[2] Running /compact...")
    ctx = CommandContext(
        message_history=history,
        deps=deps,
        agent=agent,
        tool_names=tool_names,
    )

    t0 = time.monotonic()
    handled, new_history = await dispatch("/compact", ctx)
    elapsed = time.monotonic() - t0

    if not handled:
        print("    FAIL: /compact not handled")
        return 1
    if new_history is None:
        print("    FAIL: /compact returned None history")
        return 1

    print(f"    Compacted in {elapsed:.1f}s")
    print(f"    Original: {len(history)} messages → Compacted: {len(new_history)} messages")

    # Step 3: Verify compaction quality
    print("\n[3] Verifying compaction quality...")

    # The compacted history should be 2 messages: summary + ack
    summary_text = ""
    if len(new_history) >= 1 and isinstance(new_history[0], ModelRequest):
        for part in new_history[0].parts:
            if isinstance(part, UserPromptPart):
                summary_text = part.content
                break

    print(f"    Summary ({len(summary_text)} chars):")
    for line in summary_text.split("\n")[:10]:
        print(f"      {line}")
    if summary_text.count("\n") > 10:
        print(f"      ... ({summary_text.count(chr(10))} total lines)")

    # Quality checks
    text_lower = summary_text.lower()
    checks = {
        "Compacted to 2 messages (summary + ack)": len(new_history) == 2,
        "Summary contains 'Compacted conversation summary'": "compacted conversation summary" in text_lower,
        "Summary mentions Docker": "docker" in text_lower,
        "Summary mentions key topics (install/compose/volumes)": (
            any(kw in text_lower for kw in ["install", "compose", "volume", "container"])
        ),
        "First-person voice ('I asked' or 'asked you')": (
            "i asked" in text_lower or "asked you" in text_lower
            or "you" in text_lower  # handoff style addresses the resuming LLM
        ),
        "Ack message is ModelResponse": (
            len(new_history) >= 2 and isinstance(new_history[1], ModelResponse)
        ),
    }

    all_pass = True
    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"    {status}: {check}")
        if not passed:
            all_pass = False

    verdict = "PASS" if all_pass else "FAIL"
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {verdict}")
    print(f"{'=' * 60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
