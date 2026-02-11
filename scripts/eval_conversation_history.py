"""Eval: multi-turn conversation history resolution.

Tests whether the model correctly uses the message array for context
when the user makes back-references like "the first one", "option 2", etc.

This directly tests the GLM-4.7-flash quirk where the model ignores
message_history and claims "there is no conversation history."

Usage:
    uv run python scripts/eval_conversation_history.py
"""

import asyncio
import sys
import time

from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend


# ---------------------------------------------------------------------------
# Test cases: (setup_prompt, followup_prompt, pass_keywords, fail_keywords)
#
# pass_keywords: response must contain at least one (case-insensitive)
# fail_keywords: response must NOT contain any (case-insensitive)
# ---------------------------------------------------------------------------

CASES = [
    {
        "id": "numbered-list-ref",
        "setup": "I'm interested in learning something new. Give me exactly 3 options: 1) CI/CD 2) Kubernetes 3) Docker",
        "followup": "the first one",
        "pass_keywords": ["ci/cd", "ci cd", "continuous integration", "continuous delivery"],
        "fail_keywords": ["ambiguous", "not sure what", "no context", "clarify", "what do you mean"],
    },
    {
        "id": "yes-continuation",
        "setup": "Can you explain what Docker containers are? Just give me a one-sentence definition, then ask if I want more detail.",
        "followup": "yes",
        "pass_keywords": ["docker", "container", "image"],
        "fail_keywords": ["what would you like", "not sure what", "no context", "clarify what"],
    },
    {
        "id": "option-number-ref",
        "setup": "I need to pick a database. Give me exactly 2 options: 1) PostgreSQL 2) SQLite. Just list them, nothing else.",
        "followup": "option 2",
        "pass_keywords": ["sqlite"],
        "fail_keywords": ["ambiguous", "not sure what", "no context", "which option"],
    },
]


async def run_case(case: dict, agent, deps: CoDeps, model_settings) -> dict:
    """Run a single multi-turn test case. Returns result dict."""
    eval_settings = ModelSettings(temperature=0)
    if model_settings:
        base = {
            "temperature": 0,
            "top_p": getattr(model_settings, "top_p", None),
            "max_tokens": getattr(model_settings, "max_tokens", None),
        }
        eval_settings = ModelSettings(**{k: v for k, v in base.items() if v is not None})

    limits = UsageLimits(request_limit=3)

    # Turn 1: setup prompt
    result1 = await agent.run(
        case["setup"],
        deps=deps,
        model_settings=eval_settings,
        usage_limits=limits,
    )
    setup_response = str(result1.output)
    history = result1.all_messages()

    # Turn 2: followup with back-reference
    result2 = await agent.run(
        case["followup"],
        deps=deps,
        message_history=history,
        model_settings=eval_settings,
        usage_limits=limits,
    )
    followup_response = str(result2.output)
    response_lower = followup_response.lower()

    # Score
    has_pass_kw = any(kw in response_lower for kw in case["pass_keywords"])
    has_fail_kw = any(kw in response_lower for kw in case["fail_keywords"])
    passed = has_pass_kw and not has_fail_kw

    return {
        "id": case["id"],
        "passed": passed,
        "has_pass_keyword": has_pass_kw,
        "has_fail_keyword": has_fail_kw,
        "setup_response": setup_response[:300],
        "followup_response": followup_response[:500],
        "history_length": len(history),
    }


async def main():
    provider = settings.llm_provider.lower()
    model = settings.gemini_model if provider == "gemini" else settings.ollama_model
    print(f"Model: {provider}/{model}")
    print(f"Cases: {len(CASES)}")
    print()

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        sandbox=SubprocessBackend(),
        obsidian_vault_path=None,
        google_credentials_path=None,
        slack_client=None,
        shell_safe_commands=[],
    )

    passed = 0
    failed = 0
    t0 = time.monotonic()

    for case in CASES:
        print(f"[{case['id']}] ", end="", flush=True)
        try:
            result = await run_case(case, agent, deps, model_settings)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"{status}")

            if not result["passed"]:
                failed += 1
                print(f"  Setup response:    {result['setup_response'][:120]}...")
                print(f"  Followup response: {result['followup_response'][:200]}...")
                print(f"  Pass keyword found: {result['has_pass_keyword']}")
                print(f"  Fail keyword found: {result['has_fail_keyword']}")
            else:
                passed += 1
                print(f"  Response: {result['followup_response'][:120]}...")

            print(f"  History messages: {result['history_length']}")
            print()

        except Exception as e:
            failed += 1
            print(f"ERROR: {e}")
            print()

    elapsed = time.monotonic() - t0
    print("=" * 50)
    print(f"Results: {passed}/{passed + failed} passed ({elapsed:.1f}s)")

    if failed > 0:
        print("VERDICT: FAIL — model not using conversation history")
        return 1
    else:
        print("VERDICT: PASS — model resolves back-references correctly")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
