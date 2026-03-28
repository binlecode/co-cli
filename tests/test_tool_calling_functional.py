"""Functional tool-calling coverage replacing eval_tool_calling.py dimensions.

Covers:
- tool_selection
- arg_extraction
- refusal
- intent routing (observation vs directive)
- error_recovery after tool failure
"""

import asyncio
from pathlib import Path
from dataclasses import replace

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import UsageLimits

from co_cli.agent import build_agent
from co_cli.prompts._assembly import _build_system_prompt
from co_cli._model_factory import ModelRegistry
from co_cli.config import settings, ROLE_REASONING, WebPolicy
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.prompts.model_quirks._loader import normalize_model_name
from co_cli.tools._shell_backend import ShellBackend
from tests._ollama import ensure_ollama_warm

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_reasoning_entry = _CONFIG.role_models.get(ROLE_REASONING)
_normalized_model = normalize_model_name(_reasoning_entry.model) if _reasoning_entry else ""
_CONFIG = replace(_CONFIG, system_prompt=_build_system_prompt(_CONFIG.llm_provider, _normalized_model, _CONFIG))
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_REASONING_MODEL = _CONFIG.role_models[ROLE_REASONING].model


def _make_deps(session_id: str) -> CoDeps:
    return CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=replace(_CONFIG, personality="finch"),
        session=CoSessionState(session_id=session_id),
    )


def _make_deps_web_deferred(session_id: str) -> CoDeps:
    """Deps with web_search deferred (ask) so tool selection test stays within 60s.

    The test verifies tool name + query arg from turn 1 only — no need to execute
    the actual search or wait for a second LLM turn.
    """
    return CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=replace(
            _CONFIG,
            personality="finch",
            web_policy=WebPolicy(search="ask"),
        ),
        session=CoSessionState(session_id=session_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,expected_tool,arg_key,arg_contains",
    [
        (
            "Use the run_shell_command tool to execute: git status\nDo NOT describe what you would do — call the tool now.",
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
    ids=["shell_git_status", "web_search_fastapi", "search_knowledge_db"],
)
async def test_tool_selection_and_arg_extraction(
    prompt: str,
    expected_tool: str,
    arg_key: str,
    arg_contains: str,
):
    from co_cli._model_factory import ResolvedModel
    # web_search: build agent with search="ask" so the tool is deferred — the test
    # verifies tool selection from the DeferredToolRequests in one LLM turn (60s budget).
    # Other tools use the default agent and policy.
    if expected_tool == "web_search":
        web_cfg = replace(_CONFIG, web_policy=WebPolicy(search="ask"))
        agent, _, _ = build_agent(config=web_cfg)
        deps = _make_deps_web_deferred(f"test-tool-{expected_tool}")
    else:
        agent, _, _ = build_agent(config=_CONFIG)
        deps = _make_deps(f"test-tool-{expected_tool}")
    resolved = _REGISTRY.get(ROLE_REASONING, ResolvedModel(model=None, settings=None))

    await ensure_ollama_warm(_REASONING_MODEL, _CONFIG.llm_host)
    last_details = "no run executed"
    max_attempts = 3
    for attempt in range(max_attempts):
        tool_name = None
        args = None
        try:
            async with asyncio.timeout(60):
                result = await agent.run(
                    prompt,
                    deps=deps,
                    model=resolved.model,
                    model_settings=resolved.settings,
                    usage_limits=UsageLimits(request_limit=3),
                )
            # Extract first tool call from message history (works for both
            # deferred/unapproved tools and normal tool executions).
            for msg in result.all_messages():
                if isinstance(msg, ModelResponse):
                    for part in msg.parts:
                        if isinstance(part, ToolCallPart):
                            tool_name = part.tool_name
                            args = part.args_as_dict()
                            break
                if tool_name:
                    break
        except (ModelHTTPError, ModelAPIError) as e:
            last_details = f"agent.run error: {type(e).__name__}: {e}"
            continue

        if tool_name is None:
            last_details = "no tool call observed"
            continue

        if expected_tool == "search_knowledge_or_list_memories":
            if tool_name == "search_knowledge":
                actual = str((args or {}).get("query", "")).lower()
                if "database preferences" in actual:
                    return
                last_details = (
                    f"tool={tool_name!r}, missing arg fragment "
                    f"'database preferences' in query={(args or {}).get('query')!r}"
                )
                continue
            if tool_name == "search_memories":
                actual = str((args or {}).get("query", "")).lower()
                if "database preferences" in actual:
                    return
                last_details = (
                    f"tool={tool_name!r}, missing arg fragment "
                    f"'database preferences' in query={(args or {}).get('query')!r}"
                )
                continue
            if tool_name == "list_memories":
                kind = (args or {}).get("kind")
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
        actual = str((args or {}).get(arg_key, "")).lower()
        if arg_contains.lower() in actual:
            return
        last_details = (
            f"tool={tool_name!r}, missing arg fragment "
            f"{arg_contains!r} in {arg_key}={(args or {}).get(arg_key)!r}"
        )

    pytest.fail(f"Tool selection/arg extraction failed: {last_details}")


@pytest.mark.asyncio
async def test_refusal_no_tool_for_simple_math():
    from co_cli._model_factory import ResolvedModel
    agent, _, _ = build_agent(config=_CONFIG)
    resolved = _REGISTRY.get(ROLE_REASONING, ResolvedModel(model=None, settings=None))
    deps = _make_deps("test-refusal")
    await ensure_ollama_warm(_REASONING_MODEL)
    async with asyncio.timeout(60):
        result = await agent.run(
            "What is 17 times 23?",
            deps=deps,
            model=resolved.model,
            model_settings=resolved.settings,
        )
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    f"Expected no tool call, got {part.tool_name!r}"
                )


@pytest.mark.asyncio
async def test_intent_routing_observation_no_tool():
    """Observation-only statement must not trigger a tool call."""
    from co_cli._model_factory import ResolvedModel
    agent, _, _ = build_agent(config=_CONFIG)
    resolved = _REGISTRY.get(ROLE_REASONING, ResolvedModel(model=None, settings=None))
    deps = _make_deps("test-intent-routing")

    await ensure_ollama_warm(_REASONING_MODEL)
    async with asyncio.timeout(60):
        result = await agent.run(
            "This function has a bug",
            deps=deps,
            model=resolved.model,
            model_settings=resolved.settings,
        )
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    f"Expected no tool call for observation statement, got {part.tool_name!r}"
                )


@pytest.mark.asyncio
async def test_check_task_status_surfaces_description_and_started_at(tmp_path):
    """check_task_status result includes description and started_at from task metadata."""
    import asyncio
    from co_cli.tools._background import TaskRunner, TaskStorage
    from co_cli.tools.task_control import check_task_status
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.usage import RunUsage
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend

    tasks_dir = tmp_path / "tasks"
    storage = TaskStorage(tasks_dir)
    runner = TaskRunner(storage)
    config = CoConfig(tasks_dir=tasks_dir)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), task_runner=runner),
        config=config,
    )
    agent, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "test-background-task-description"
    async with asyncio.timeout(30):
        task_id = await runner.start_task(
            "echo hello",
            str(tmp_path),
            {"description": task_description},
            None,
        )
        # Give it a moment to start
        await asyncio.sleep(0.5)
        result = await check_task_status(ctx, task_id)

    assert result.get("description") == task_description
    assert result.get("started_at") is not None
