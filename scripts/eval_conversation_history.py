"""Eval: multi-turn conversation history resolution across Ollama models.

Tests whether the model correctly uses the message array for context
when the user makes back-references like "the first one", "option 2", etc.

Cross-model validation: runs the same cases against GLM-4.7 and Qwen3
to isolate whether history loss is a model quirk or a system-level bug.

Captures the exact HTTP request body sent to Ollama's OpenAI-compatible
endpoint so we can verify message_history is properly serialised.

Usage:
    LLM_PROVIDER=ollama uv run python scripts/eval_conversation_history.py
"""

import asyncio
import logging
import sys
import time

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend


# ---------------------------------------------------------------------------
# Test cases
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

# Models to test (Ollama model names)
MODELS_TO_TEST = [
    "glm-4.7-flash:q4_k_m",
    "glm-4.7-flash:q4_k_m-agentic",
    "glm-4.7-flash:q8_0-agentic",
    "qwen3:30b-a3b-thinking-2507-q8_0",
]


def _describe_messages(messages: list) -> list[str]:
    """Describe pydantic-ai message list for diagnostics."""
    desc = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            parts = []
            for p in msg.parts:
                if isinstance(p, UserPromptPart):
                    parts.append(f"UserPrompt({len(p.content)} chars)")
                else:
                    parts.append(type(p).__name__)
            desc.append(f"  [{i}] Request: {', '.join(parts)}")
        elif isinstance(msg, ModelResponse):
            parts = []
            for p in msg.parts:
                if isinstance(p, TextPart):
                    parts.append(f"Text({len(p.content)} chars)")
                else:
                    parts.append(type(p).__name__)
            desc.append(f"  [{i}] Response: {', '.join(parts)}")
        else:
            desc.append(f"  [{i}] {type(msg).__name__}")
    return desc


async def run_case(case: dict, agent, deps: CoDeps, model_settings, model_label: str) -> dict:
    """Run a single multi-turn test case. Returns result dict."""
    eval_settings = ModelSettings(temperature=0)
    if model_settings:
        base = {
            "temperature": 0,
            "top_p": getattr(model_settings, "top_p", None),
            "max_tokens": getattr(model_settings, "max_tokens", None),
        }
        if hasattr(model_settings, "extra_body") and model_settings.extra_body:
            base["extra_body"] = model_settings.extra_body
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
    all_messages = result2.all_messages()
    response_lower = followup_response.lower()

    # Score
    has_pass_kw = any(kw in response_lower for kw in case["pass_keywords"])
    has_fail_kw = any(kw in response_lower for kw in case["fail_keywords"])
    passed = has_pass_kw and not has_fail_kw

    return {
        "id": case["id"],
        "model": model_label,
        "passed": passed,
        "has_pass_keyword": has_pass_kw,
        "has_fail_keyword": has_fail_kw,
        "setup_response": setup_response[:300],
        "followup_response": followup_response[:500],
        "turn1_history_len": len(history),
        "turn2_total_msgs": len(all_messages),
        "turn1_structure": _describe_messages(history),
        "turn2_structure": _describe_messages(all_messages),
    }


def _switch_model(agent, model_name: str):
    """Switch the agent to a different Ollama model, rebuilding prompt and settings."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from co_cli.prompts import get_system_prompt
    from co_cli.prompts.model_quirks import normalize_model_name, get_model_inference

    ollama_host = settings.ollama_host
    provider = OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama")
    agent.model = OpenAIChatModel(model_name=model_name, provider=provider)

    normalized = normalize_model_name(model_name)
    new_prompt = get_system_prompt(
        "ollama",
        personality=settings.personality,
        model_name=normalized,
    )
    agent.system_prompt = new_prompt

    inf = get_model_inference("ollama", normalized)
    num_ctx = inf.get("num_ctx", settings.ollama_num_ctx)
    extra: dict = {"num_ctx": num_ctx}
    extra.update(inf.get("extra_body", {}))

    return ModelSettings(
        temperature=inf.get("temperature", 0.7),
        top_p=inf.get("top_p", 1.0),
        max_tokens=inf.get("max_tokens", 16384),
        extra_body=extra,
    )


def _dump_case_detail(result: dict):
    """Print detailed pydantic-ai message analysis for debugging."""
    print(f"    Turn 1 history ({result['turn1_history_len']} msgs):")
    for line in result["turn1_structure"]:
        print(f"      {line}")
    print(f"    Turn 2 total ({result['turn2_total_msgs']} msgs):")
    for line in result["turn2_structure"]:
        print(f"      {line}")


async def main():
    # Suppress noisy loggers
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    provider = settings.llm_provider.lower()
    if provider != "ollama":
        print(f"ERROR: This eval requires LLM_PROVIDER=ollama (got: {provider})")
        print("Usage: LLM_PROVIDER=ollama uv run python scripts/eval_conversation_history.py")
        return 1

    print("=" * 70)
    print("Cross-Model Conversation History Eval")
    print(f"Provider: {provider}")
    print(f"Models: {', '.join(MODELS_TO_TEST)}")
    print(f"Cases: {len(CASES)}")
    print("=" * 70)

    agent, _, _ = get_agent()
    deps = CoDeps(
        sandbox=SubprocessBackend(),
        obsidian_vault_path=None,
        google_credentials_path=None,
        slack_client=None,
        shell_safe_commands=[],
    )

    # Show system prompt for first model (diagnostic)
    from co_cli.prompts import get_system_prompt
    from co_cli.prompts.model_quirks import normalize_model_name
    for m in MODELS_TO_TEST:
        norm = normalize_model_name(m)
        prompt = get_system_prompt("ollama", personality=settings.personality, model_name=norm)
        print(f"\n  System prompt for {m}: {len(prompt)} chars")
        print(f"  First 200 chars: {prompt[:200]}...")

    all_results: list[dict] = []

    for model_name in MODELS_TO_TEST:
        print(f"\n{'─' * 60}")
        print(f"MODEL: {model_name}")
        print(f"{'─' * 60}")

        model_settings = _switch_model(agent, model_name)
        t0 = time.monotonic()

        for case in CASES:
            print(f"\n  [{model_name}] [{case['id']}] ", end="", flush=True)
            try:
                result = await run_case(case, agent, deps, model_settings, model_name)
                all_results.append(result)
                status = "PASS" if result["passed"] else "FAIL"
                print(status)

                # Always show followup response
                print(f"    Followup: {result['followup_response'][:200]}")

                if not result["passed"]:
                    print(f"    Setup:    {result['setup_response'][:120]}...")
                    print(f"    Pass kw: {result['has_pass_keyword']}, Fail kw: {result['has_fail_keyword']}")

                # Always dump message structure for diagnosis
                _dump_case_detail(result)

            except Exception as e:
                print(f"ERROR: {e}")
                import traceback
                traceback.print_exc()

        elapsed = time.monotonic() - t0
        model_results = [r for r in all_results if r["model"] == model_name]
        model_passed = sum(1 for r in model_results if r["passed"])
        print(f"\n  {model_name}: {model_passed}/{len(model_results)} passed ({elapsed:.1f}s)")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for model_name in MODELS_TO_TEST:
        model_results = [r for r in all_results if r["model"] == model_name]
        passed = sum(1 for r in model_results if r["passed"])
        total = len(model_results)
        status = "PASS" if passed == total else "FAIL"
        print(f"  {model_name:40s}  {passed}/{total}  {status}")

        for r in model_results:
            marker = "ok" if r["passed"] else "FAIL"
            print(f"    {r['id']:25s}  t1={r['turn1_history_len']}msgs  t2={r['turn2_total_msgs']}msgs  {marker}")

    total_passed = sum(1 for r in all_results if r["passed"])
    total_cases = len(all_results)
    print(f"\nOverall: {total_passed}/{total_cases}")

    if total_passed < total_cases:
        print("\nVERDICT: FAIL")
        print("Root cause: check message structure dumps above")
        print("  - If turn1 has 2+ msgs and turn2 has 4+ msgs: history IS sent, model ignores it (behavioral)")
        print("  - If turn2 msgs == turn1 msgs: history not growing (system bug)")
        return 1
    else:
        print("\nVERDICT: PASS — all models resolve back-references correctly")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
