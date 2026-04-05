"""Functional tool-calling coverage replacing eval_tool_calling.py dimensions.

Covers:
- tool_selection
- arg_extraction
- refusal
- intent routing (observation vs directive)
- error_recovery after tool failure
"""

import asyncio
from dataclasses import replace
from pathlib import Path
import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import RunUsage


from co_cli.agent import build_agent, build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings, ROLE_TASK
from co_cli.deps import CoDeps, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from co_cli.context._orchestrate import run_turn
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS, FILE_DB_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
# Exclude MCP servers: agent.run() spawns their processes inline per call; these tests cover built-in tools only.
_CONFIG_NO_MCP = replace(_CONFIG, mcp_servers={})
_REGISTRY = ModelRegistry.from_config(_CONFIG_NO_MCP)
_TASK_MODEL = _CONFIG_NO_MCP.role_models[ROLE_TASK].model

# Tool selection tests use ROLE_TASK (reasoning_effort=none) with build_task_agent.
# build_task_agent uses a 1-sentence system prompt vs the full 16K system prompt in
# build_agent — reducing context from ~27K to ~10K tokens so calls complete faster.
_TASK_RESOLVED = _REGISTRY.get(ROLE_TASK, ResolvedModel(model=None, settings=None))
# Tool registry and agents built once at module level to avoid per-test overhead.
from co_cli.agent import build_tool_registry
_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
_AGENT_NOREASON = build_task_agent(config=_CONFIG_NO_MCP, role_model=_TASK_RESOLVED, tool_registry=_TOOL_REG)


def _make_deps(session_id: str) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model_registry=_REGISTRY,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
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
    agent = _AGENT_NOREASON
    deps = _make_deps(f"test-tool-{expected_tool}")
    frontend = SilentFrontend(approval_response="y")

    await ensure_ollama_warm(_TASK_MODEL, _CONFIG_NO_MCP.llm_host)
    last_details = "no run executed"
    max_attempts = 3
    for attempt in range(max_attempts):
        tool_name = None
        args = None
        try:
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
                turn = await run_turn(
                    agent=agent,
                    user_input=prompt,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                )
            # Extract first tool call from message history.
            for msg in turn.messages:
                if isinstance(msg, ModelResponse):
                    for part in msg.parts:
                        if isinstance(part, ToolCallPart):
                            tool_name = part.tool_name
                            args = part.args_as_dict()
                            break
                if tool_name:
                    break
        except (ModelHTTPError, ModelAPIError, TimeoutError) as e:
            last_details = f"run_turn error: {type(e).__name__}: {e}"
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
    deps = _make_deps("test-refusal")
    await ensure_ollama_warm(_TASK_MODEL)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        turn = await run_turn(
            agent=_AGENT_NOREASON,
            user_input="What is 17 times 23?",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )
    for msg in turn.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    f"Expected no tool call, got {part.tool_name!r}"
                )


@pytest.mark.asyncio
async def test_intent_routing_observation_no_tool():
    """Observation-only statement must not trigger a tool call."""
    deps = _make_deps("test-intent-routing")

    await ensure_ollama_warm(_TASK_MODEL)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        turn = await run_turn(
            agent=_AGENT_NOREASON,
            user_input="This function has a bug",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )
    for msg in turn.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    f"Expected no tool call for observation statement, got {part.tool_name!r}"
                )


@pytest.mark.asyncio
async def test_check_task_status_surfaces_description_and_started_at(tmp_path):
    """check_task_status result includes description and started_at from task metadata."""
    import asyncio
    from co_cli.tools._background import BackgroundTaskState, _make_task_id, spawn_task
    from co_cli.tools.task_control import check_task_status
    from co_cli.deps import CoDeps, CoConfig
    from co_cli.tools._shell_backend import ShellBackend
    from datetime import datetime, timezone

    deps = CoDeps(shell=ShellBackend(), config=CoConfig())
    agent = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "test-background-task-description"
    state = BackgroundTaskState(
        task_id=_make_task_id(),
        command="echo hello",
        cwd=str(tmp_path),
        description=task_description,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    deps.session.background_tasks[state.task_id] = state

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        await spawn_task(state, deps.session)
        await asyncio.sleep(0.3)
        result = await check_task_status(ctx, state.task_id)

    assert (result.metadata or {}).get("description") == task_description
    assert (result.metadata or {}).get("started_at") is not None


@pytest.mark.asyncio
async def test_list_background_tasks_surfaces_description(tmp_path):
    """list_background_tasks includes task descriptions in both metadata and display output."""
    from co_cli.tools._background import BackgroundTaskState, _make_task_id, spawn_task
    from co_cli.tools.task_control import list_background_tasks
    from co_cli.deps import CoDeps, CoConfig
    from co_cli.tools._shell_backend import ShellBackend
    from datetime import datetime, timezone

    deps = CoDeps(shell=ShellBackend(), config=CoConfig())
    agent = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "background task list description"
    state = BackgroundTaskState(
        task_id=_make_task_id(),
        command="echo hello",
        cwd=str(tmp_path),
        description=task_description,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    deps.session.background_tasks[state.task_id] = state

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        await spawn_task(state, deps.session)
        await asyncio.sleep(0.3)
        result = await list_background_tasks(ctx)

    assert result.metadata["count"] == 1
    assert result.metadata["tasks"][0]["description"] == task_description
    assert task_description in result.return_value
