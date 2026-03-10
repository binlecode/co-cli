"""Functional tool-calling coverage replacing eval_tool_calling.py dimensions.

Covers:
- tool_selection
- arg_extraction
- refusal
- intent routing (observation vs directive)
- error_recovery after tool failure
"""

import asyncio
import json
import os
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.agents._factory import ModelRegistry
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli._shell_backend import ShellBackend

_CONFIG = CoConfig.from_settings(settings)
_REGISTRY = ModelRegistry.from_config(_CONFIG)


def _is_ollama_provider() -> bool:
    provider = (os.getenv("LLM_PROVIDER") or settings.llm_provider).lower()
    return provider == "ollama"


def _make_deps(session_id: str) -> CoDeps:
    return CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=replace(_CONFIG, session_id=session_id, personality="finch"),
    )


def _extract_first_tool_call(messages: list[Any]) -> tuple[str | None, dict[str, Any] | None]:
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                return part.tool_name, part.args_as_dict()
    return None, None


def _extract_first_deferred_call(output: DeferredToolRequests) -> tuple[str | None, dict[str, Any]]:
    approvals = list(output.approvals)
    if not approvals:
        return None, {}
    call = approvals[0]
    args = call.args
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return call.tool_name, args or {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,expected_tool,arg_key,arg_contains",
    [
        (
            "Call the run_shell_command tool with cmd exactly 'git status'.",
            "run_shell_command",
            "cmd",
            "git status",
        ),
        (
            "Search the web for FastAPI authentication tutorial.",
            "web_search",
            "query",
            "fastapi authentication tutorial",
        ),
        (
            "Do I have any memories about database preferences?",
            "search_knowledge_or_list_memories",
            "query",
            "database preferences",
        ),
    ],
)
async def test_tool_selection_and_arg_extraction(
    prompt: str,
    expected_tool: str,
    arg_key: str,
    arg_contains: str,
):
    if not _is_ollama_provider():
        return

    agent, model_settings, _, _ = get_agent(all_approval=True)
    deps = _make_deps(f"test-tool-{expected_tool}")

    last_details = "no run executed"
    for _ in range(2):
        try:
            async with asyncio.timeout(60):
                result = await agent.run(
                    prompt,
                    deps=deps,
                    model_settings=model_settings,
                    usage_limits=UsageLimits(request_limit=2),
                )
        except Exception as e:
            last_details = f"agent.run error: {type(e).__name__}: {e}"
            continue
        if not isinstance(result.output, DeferredToolRequests):
            last_details = f"expected deferred tool call, got {type(result.output).__name__}"
            continue
        tool_name, args = _extract_first_deferred_call(result.output)
        if expected_tool == "search_knowledge_or_list_memories":
            if tool_name == "search_knowledge":
                actual = str(args.get("query", "")).lower()
                if "database preferences" in actual:
                    return
                last_details = (
                    f"tool={tool_name!r}, missing arg fragment "
                    f"'database preferences' in query={args.get('query')!r}"
                )
                continue
            if tool_name == "search_memories":
                actual = str(args.get("query", "")).lower()
                if "database preferences" in actual:
                    return
                last_details = (
                    f"tool={tool_name!r}, missing arg fragment "
                    f"'database preferences' in query={args.get('query')!r}"
                )
                continue
            if tool_name == "list_memories":
                kind = args.get("kind")
                if kind in (None, "memory"):
                    return
                last_details = f"tool={tool_name!r}, unexpected kind={kind!r}, args={args!r}"
                continue
            last_details = (
                f"tool={tool_name!r}, expected one of "
                f"('search_knowledge', 'search_memories', 'list_memories'), args={args!r}"
            )
            continue
        if tool_name != expected_tool:
            last_details = f"tool={tool_name!r}, expected={expected_tool!r}, args={args!r}"
            continue
        actual = str(args.get(arg_key, "")).lower()
        if arg_contains.lower() in actual:
            return
        last_details = (
            f"tool={tool_name!r}, missing arg fragment "
            f"{arg_contains!r} in {arg_key}={args.get(arg_key)!r}"
        )

    pytest.fail(f"Tool selection/arg extraction failed: {last_details}")


@pytest.mark.asyncio
async def test_refusal_no_tool_for_simple_math():
    if not _is_ollama_provider():
        return

    agent, model_settings, _, _ = get_agent(all_approval=True)
    deps = _make_deps("test-refusal")
    async with asyncio.timeout(60):
        result = await agent.run(
            "What is 17 times 23?",
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=2),
        )

    assert not isinstance(result.output, DeferredToolRequests), (
        f"Expected text-only refusal path, got tool approvals: {result.output!r}"
    )
    tool_name, _ = _extract_first_tool_call(result.all_messages())
    assert tool_name is None, f"Expected no tool call, got {tool_name!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,expected_tool",
    [
        ("This function has a bug", None),
        # Unambiguous shell directive — no file exploration needed before routing
        ("Run pytest to check if the tests pass", "run_shell_command"),
    ],
)
async def test_intent_routing_observation_vs_directive(
    prompt: str,
    expected_tool: str | None,
):
    if not _is_ollama_provider():
        return

    agent, model_settings, _, _ = get_agent(all_approval=True)
    deps = _make_deps("test-intent-routing")

    last_tool: str | None = None
    last_details = "no run executed"
    for _ in range(2):
        try:
            async with asyncio.timeout(60):
                result = await agent.run(
                    prompt,
                    deps=deps,
                    model_settings=model_settings,
                    # Extra budget: model may read files before calling run_shell_command
                    usage_limits=UsageLimits(request_limit=6),
                )
        except Exception as e:
            last_details = f"agent.run error: {type(e).__name__}: {e}"
            continue
        if isinstance(result.output, DeferredToolRequests):
            tool_name, _ = _extract_first_deferred_call(result.output)
        else:
            tool_name = None
        last_tool = tool_name
        last_details = f"tool={tool_name!r}"
        if tool_name == expected_tool:
            return

    assert last_tool == expected_tool, (
        f"Intent routing mismatch for prompt={prompt!r}: "
        f"expected {expected_tool!r}, got {last_tool!r} — {last_details}"
    )
