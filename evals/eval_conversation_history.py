"""Eval: multi-turn conversation history stress test across Ollama models.

Three tiers of conversation history reasoning:

  Tier 1 — Basic back-reference (2 turns, text only)
  Tier 2 — Deep history (3+ turns, distraction in between, corrections)
  Tier 3 — Tool output in history (synthetic ToolCallPart/ToolReturnPart)

Cross-model validation isolates behavioral quirks from system-level bugs.

Usage:
    LLM_PROVIDER=ollama uv run python scripts/eval_conversation_history.py
"""

import asyncio
import logging
import sys
import time

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------

# Each case has:
#   tier      — 1 (basic), 2 (deep), 3 (tool output)
#   turns     — list of user prompts; last one is scored
#   pass_keywords / fail_keywords — scoring rules
#   synthetic_history — (tier 3 only) manually constructed message list
#                       injected before the final scored prompt

CASES = [
    # ── Tier 1: Basic back-reference (2 turns, text only) ────────────

    {
        "id": "t1-numbered-ref",
        "tier": 1,
        "turns": [
            "Give me exactly 3 options: 1) CI/CD 2) Kubernetes 3) Docker. Just the numbered list.",
            "the first one",
        ],
        "pass_keywords": ["ci/cd", "ci cd", "continuous integration", "continuous delivery"],
        "fail_keywords": ["no context", "clarify", "what do you mean", "not sure what", "previous context"],
    },
    {
        "id": "t1-yes-continue",
        "tier": 1,
        "turns": [
            "Explain what Docker containers are in one sentence, then ask if I want more detail.",
            "yes",
        ],
        "pass_keywords": ["docker", "container"],
        "fail_keywords": ["what would you like", "not sure what", "no context", "clarify what"],
    },

    # ── Tier 2: Deep history (3+ turns, distraction, corrections) ────

    {
        "id": "t2-ref-across-distraction",
        "tier": 2,
        "turns": [
            "I'm setting up a Python project. I want to use pytest for testing.",
            "By the way, what's the weather usually like in San Francisco?",
            "OK back to my project — which testing framework did I say I wanted?",
        ],
        "pass_keywords": ["pytest"],
        "fail_keywords": ["no context", "don't know", "not sure", "didn't mention", "haven't mentioned"],
    },
    {
        "id": "t2-correction-chain",
        "tier": 2,
        "turns": [
            "I want to deploy my app to AWS Lambda.",
            "Actually wait, I changed my mind. Let's deploy to Google Cloud Run instead.",
            "So where are we deploying to?",
        ],
        "pass_keywords": ["cloud run", "google cloud"],
        # Don't include "lambda"/"aws" — correct response may mention them in negation
        "fail_keywords": ["no context", "not sure", "clarify", "don't know", "didn't mention"],
    },
    {
        "id": "t2-detail-from-turn1",
        "tier": 2,
        "turns": [
            "My server is running on port 8443 with TLS enabled.",
            "Can you explain what TLS certificates are?",
            "Remind me, what port is my server on?",
        ],
        "pass_keywords": ["8443"],
        "fail_keywords": ["don't know", "not sure", "no context", "didn't mention", "haven't specified"],
    },
    {
        "id": "t2-accumulation",
        "tier": 2,
        "turns": [
            "I have three microservices: auth-service, billing-service, and notification-service.",
            "The auth-service runs on port 3001 and billing-service on port 3002.",
            "notification-service is on port 3003.",
            "What port does billing-service run on?",
        ],
        "pass_keywords": ["3002"],
        "fail_keywords": ["don't know", "not sure", "no context", "not mentioned"],
    },

    # ── Tier 3: Tool output in history (synthetic messages) ──────────

    {
        "id": "t3-tool-output-ref",
        "tier": 3,
        "synthetic_history": [
            # User asked to search notes
            ("user", "Search my notes for deployment guides"),
            # Model called a tool
            ("tool_call", "search_notes", '{"query": "deployment"}', "call_001"),
            # Tool returned results
            ("tool_return", "search_notes", (
                "Found 3 notes:\n"
                "1. deploy-kubernetes.md — K8s deployment playbook\n"
                "2. deploy-cloudrun.md — Cloud Run setup guide\n"
                "3. ci-cd-pipeline.md — GitHub Actions CI/CD"
            ), "call_001"),
            # Model presented results
            ("assistant", (
                "I found 3 deployment-related notes:\n"
                "1. deploy-kubernetes.md — K8s deployment playbook\n"
                "2. deploy-cloudrun.md — Cloud Run setup guide\n"
                "3. ci-cd-pipeline.md — GitHub Actions CI/CD\n\n"
                "Would you like me to open any of these?"
            )),
        ],
        "turns": ["which note was the second result?"],
        "pass_keywords": ["deploy-cloudrun", "cloud run", "cloudrun"],
        "fail_keywords": ["no context", "don't see", "which notes", "what second", "previous context"],
    },
    {
        "id": "t3-shell-output-ref",
        "tier": 3,
        "synthetic_history": [
            # User asked to run a command
            ("user", "Run 'ls -la /app/config/' in the shell"),
            # Model called shell tool
            ("tool_call", "run_shell_command", '{"cmd": "ls -la /app/config/"}', "call_002"),
            # Shell returned file listing
            ("tool_return", "run_shell_command", (
                "total 24\n"
                "drwxr-xr-x 2 root root 4096 Jan 15 10:00 .\n"
                "-rw-r--r-- 1 root root  842 Jan 15 09:30 database.yml\n"
                "-rw-r--r-- 1 root root 1205 Jan 15 09:30 redis.conf\n"
                "-rw-r--r-- 1 root root  376 Jan 15 09:30 secrets.env"
            ), "call_002"),
            # Model presented results
            ("assistant", (
                "Here are the files in /app/config/:\n"
                "- database.yml (842 bytes)\n"
                "- redis.conf (1205 bytes)\n"
                "- secrets.env (376 bytes)\n\n"
                "Would you like me to read any of these?"
            )),
        ],
        "turns": ["what's the biggest file in there?"],
        "pass_keywords": ["redis", "redis.conf", "1205"],
        "fail_keywords": ["no context", "don't know", "which directory", "haven't seen", "previous context"],
    },
    {
        "id": "t3-interleaved-tool-chat",
        "tier": 3,
        "synthetic_history": [
            # Turn 1: casual chat
            ("user", "I'm working on a FastAPI project called 'invoice-api'."),
            ("assistant", "Got it — invoice-api with FastAPI. How can I help?"),
            # Turn 2: tool call
            ("user", "Search my drive for the API spec document"),
            ("tool_call", "search_drive_files", '{"query": "invoice API spec"}', "call_003"),
            ("tool_return", "search_drive_files", (
                '{"display": "1. Invoice API Spec v2.pdf (shared Jan 10)\\n'
                '2. Old API Design.docx (archived)", "count": 2}'
            ), "call_003"),
            ("assistant", (
                "Found 2 files:\n"
                "1. Invoice API Spec v2.pdf (shared Jan 10)\n"
                "2. Old API Design.docx (archived)\n\n"
                "Want me to open either?"
            )),
            # Turn 3: more chat
            ("user", "Not yet. What's a good way to structure FastAPI routes?"),
            ("assistant", (
                "For FastAPI, I'd recommend organizing routes with APIRouter:\n"
                "- routers/invoices.py — CRUD endpoints\n"
                "- routers/payments.py — payment processing\n"
                "- routers/health.py — health checks\n\n"
                "Each router handles one domain concern."
            )),
        ],
        "turns": ["what was my project called again?"],
        "pass_keywords": ["invoice-api", "invoice api"],
        "fail_keywords": ["no context", "don't know", "not sure", "haven't mentioned", "previous context"],
    },
]


# Models to test
MODELS_TO_TEST = [
    "qwen3:30b-a3b-thinking-2507-q8_0",
    "glm-4.7-flash:q4_k_m-agentic",
    "glm-4.7-flash:q8_0-agentic",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _describe_messages(messages: list) -> list[str]:
    """Describe pydantic-ai message list for diagnostics."""
    desc = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            parts = []
            for p in msg.parts:
                if isinstance(p, UserPromptPart):
                    parts.append(f"UserPrompt({len(p.content)} chars)")
                elif isinstance(p, ToolReturnPart):
                    c = p.content
                    clen = len(c) if isinstance(c, str) else len(str(c))
                    parts.append(f"ToolReturn:{p.tool_name}({clen} chars)")
                else:
                    parts.append(type(p).__name__)
            desc.append(f"  [{i}] Request: {', '.join(parts)}")
        elif isinstance(msg, ModelResponse):
            parts = []
            for p in msg.parts:
                if isinstance(p, TextPart):
                    parts.append(f"Text({len(p.content)} chars)")
                elif isinstance(p, ToolCallPart):
                    parts.append(f"ToolCall:{p.tool_name}")
                else:
                    parts.append(type(p).__name__)
            desc.append(f"  [{i}] Response: {', '.join(parts)}")
        else:
            desc.append(f"  [{i}] {type(msg).__name__}")
    return desc


def _build_synthetic_history(entries: list) -> list:
    """Convert synthetic_history tuples into pydantic-ai message objects.

    Entry formats:
      ("user", "text")
      ("assistant", "text")
      ("tool_call", tool_name, args_json, call_id)
      ("tool_return", tool_name, content, call_id)
    """
    messages = []
    for entry in entries:
        kind = entry[0]
        if kind == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=entry[1])]))
        elif kind == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=entry[1])]))
        elif kind == "tool_call":
            _, tool_name, args, call_id = entry
            messages.append(ModelResponse(parts=[
                ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id),
            ]))
        elif kind == "tool_return":
            _, tool_name, content, call_id = entry
            messages.append(ModelRequest(parts=[
                ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id),
            ]))
    return messages


def _make_eval_settings(model_settings):
    """Build deterministic eval settings (temp=0) from model settings."""
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
    return eval_settings


# ---------------------------------------------------------------------------
# Case runners
# ---------------------------------------------------------------------------


def _patch_dangling_tool_calls(messages: list) -> list:
    """Patch history if last response has unanswered tool calls.

    Overeager models (e.g. GLM) may trigger tool calls during setup turns.
    pydantic-ai rejects the next agent.run() if history ends with dangling
    ToolCallPart without matching ToolReturnPart.  Same pattern as
    co_cli/_orchestrate.py:_patch_dangling_tool_calls.
    """
    if not messages:
        return messages

    last_msg = messages[-1]
    if not (hasattr(last_msg, "kind") and last_msg.kind == "response"):
        return messages

    tool_calls = [p for p in last_msg.parts if isinstance(p, ToolCallPart)]
    if not tool_calls:
        return messages

    return_parts = [
        ToolReturnPart(
            tool_name=tc.tool_name,
            tool_call_id=tc.tool_call_id,
            content="[eval: tool not available during setup turn]",
        )
        for tc in tool_calls
    ]
    return messages + [ModelRequest(parts=return_parts)]


async def run_case(case: dict, agent, deps: CoDeps, model_settings, model_label: str) -> dict:
    """Run a multi-turn test case. Supports all three tiers."""
    eval_settings = _make_eval_settings(model_settings)
    limits = UsageLimits(request_limit=3)

    # Build history: either from live LLM turns or synthetic messages
    history = []

    if case.get("synthetic_history"):
        # Tier 3: start with synthetic history
        history = _build_synthetic_history(case["synthetic_history"])
    else:
        # Tier 1 & 2: run setup turns live through the LLM
        setup_turns = case["turns"][:-1]
        for prompt in setup_turns:
            result = await agent.run(
                prompt,
                deps=deps,
                message_history=history,
                model_settings=eval_settings,
                usage_limits=limits,
            )
            # Patch dangling tool calls before next turn — overeager models
            # may trigger tool calls during conversational setup prompts
            history = _patch_dangling_tool_calls(result.all_messages())

    setup_history_len = len(history)

    # Final scored turn
    scored_prompt = case["turns"][-1]
    t0 = time.monotonic()
    result = await agent.run(
        scored_prompt,
        deps=deps,
        message_history=history,
        model_settings=eval_settings,
        usage_limits=limits,
    )
    elapsed = time.monotonic() - t0

    # Extract text from result — DeferredToolRequests means the model
    # returned a tool call instead of a text answer (overeager behavior)
    if isinstance(result.output, DeferredToolRequests):
        # Try to find any text in the last ModelResponse
        followup_response = "[model returned tool call instead of text]"
        for msg in reversed(result.all_messages()):
            if isinstance(msg, ModelResponse):
                text_parts = [p.content for p in msg.parts if isinstance(p, TextPart)]
                if text_parts:
                    followup_response = " ".join(text_parts)
                break
    else:
        followup_response = str(result.output)

    all_messages = result.all_messages()
    response_lower = followup_response.lower()

    # Score
    has_pass_kw = any(kw in response_lower for kw in case["pass_keywords"])
    has_fail_kw = any(kw in response_lower for kw in case["fail_keywords"])
    passed = has_pass_kw and not has_fail_kw

    return {
        "id": case["id"],
        "tier": case["tier"],
        "model": model_label,
        "passed": passed,
        "has_pass_keyword": has_pass_kw,
        "has_fail_keyword": has_fail_kw,
        "followup_response": followup_response[:500],
        "setup_history_len": setup_history_len,
        "total_msgs": len(all_messages),
        "msg_structure": _describe_messages(all_messages),
        "elapsed": elapsed,
    }


def _switch_model(agent, model_name: str):
    """Switch the agent to a different Ollama model, rebuilding prompt and settings."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from co_cli.prompts import assemble_prompt
    from co_cli.prompts.model_quirks import normalize_model_name, get_model_inference

    ollama_host = settings.ollama_host
    provider = OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama")
    agent.model = OpenAIChatModel(model_name=model_name, provider=provider)

    normalized = normalize_model_name(model_name)
    new_prompt, _manifest = assemble_prompt(
        "ollama",
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _dump_case_detail(result: dict):
    """Print detailed pydantic-ai message analysis for debugging."""
    print(f"    History before scored turn: {result['setup_history_len']} msgs")
    print(f"    Total after scored turn:    {result['total_msgs']} msgs")
    for line in result["msg_structure"]:
        print(f"      {line}")


async def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    provider = settings.llm_provider.lower()
    if provider != "ollama":
        print(f"ERROR: This eval requires LLM_PROVIDER=ollama (got: {provider})")
        print("Usage: LLM_PROVIDER=ollama uv run python scripts/eval_conversation_history.py")
        return 1

    tier_counts = {}
    for c in CASES:
        tier_counts[c["tier"]] = tier_counts.get(c["tier"], 0) + 1

    print("=" * 70)
    print("Cross-Model Conversation History Stress Test")
    print(f"Provider: {provider}")
    print(f"Models:   {len(MODELS_TO_TEST)}")
    print(f"Cases:    {len(CASES)} total — "
          + ", ".join(f"tier {t}: {n}" for t, n in sorted(tier_counts.items())))
    print("=" * 70)

    agent, _, _ = get_agent()
    deps = CoDeps(
        shell=ShellBackend(),
        obsidian_vault_path=None,
        google_credentials_path=None,
        shell_safe_commands=[],
        memory_max_count=settings.memory_max_count,
        memory_dedup_window_days=settings.memory_dedup_window_days,
        memory_dedup_threshold=settings.memory_dedup_threshold,
        memory_decay_strategy=settings.memory_decay_strategy,
        memory_decay_percentage=settings.memory_decay_percentage,
        max_history_messages=settings.max_history_messages,
        tool_output_trim_chars=settings.tool_output_trim_chars,
        summarization_model=settings.summarization_model,
    )

    all_results: list[dict] = []

    for model_name in MODELS_TO_TEST:
        print(f"\n{'─' * 60}")
        print(f"MODEL: {model_name}")
        print(f"{'─' * 60}")

        model_settings = _switch_model(agent, model_name)
        t0 = time.monotonic()

        for case in CASES:
            label = f"[{model_name}] [{case['id']}]"
            print(f"\n  {label} ", end="", flush=True)
            try:
                result = await run_case(case, agent, deps, model_settings, model_name)
                all_results.append(result)
                status = "PASS" if result["passed"] else "FAIL"
                print(f"{status} ({result['elapsed']:.1f}s)")

                print(f"    Response: {result['followup_response'][:200]}")

                if not result["passed"]:
                    print(f"    Pass kw: {result['has_pass_keyword']}, "
                          f"Fail kw: {result['has_fail_keyword']}")

                _dump_case_detail(result)

            except Exception as e:
                print(f"ERROR: {e}")
                import traceback
                traceback.print_exc()

        elapsed = time.monotonic() - t0
        model_results = [r for r in all_results if r["model"] == model_name]
        model_passed = sum(1 for r in model_results if r["passed"])
        print(f"\n  {model_name}: {model_passed}/{len(model_results)} passed ({elapsed:.1f}s)")

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for model_name in MODELS_TO_TEST:
        model_results = [r for r in all_results if r["model"] == model_name]
        passed = sum(1 for r in model_results if r["passed"])
        total = len(model_results)
        status = "PASS" if passed == total else "FAIL"
        print(f"\n  {model_name:40s}  {passed}/{total}  {status}")

        for tier in sorted(tier_counts):
            tier_results = [r for r in model_results if r["tier"] == tier]
            tier_pass = sum(1 for r in tier_results if r["passed"])
            tier_total = len(tier_results)
            tier_label = {1: "basic", 2: "deep", 3: "tool"}[tier]
            markers = " ".join("ok" if r["passed"] else "FAIL" for r in tier_results)
            print(f"    tier {tier} ({tier_label:5s}): {tier_pass}/{tier_total}  [{markers}]")

    total_passed = sum(1 for r in all_results if r["passed"])
    total_cases = len(all_results)
    print(f"\n  Overall: {total_passed}/{total_cases}")

    if total_passed < total_cases:
        print("\n  VERDICT: FAIL")
        return 1
    else:
        print("\n  VERDICT: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
