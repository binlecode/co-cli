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
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import RunUsage


from co_cli.agent import build_agent, build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings, ROLE_TASK, WebPolicy
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from co_cli.context._orchestrate import run_turn
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS, LLM_MULTI_SEGMENT_TIMEOUT_SECS, LLM_DEFERRED_TURN_TIMEOUT_SECS, FILE_DB_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_TASK_MODEL = _CONFIG.role_models[ROLE_TASK].model

# Tool selection tests use ROLE_TASK (reasoning_effort=none) with build_task_agent.
# build_task_agent uses a 1-sentence system prompt vs the full 16K system prompt in
# build_agent — reducing context from ~27K to ~10K tokens so calls complete faster.
_TASK_RESOLVED = _REGISTRY.get(ROLE_TASK, ResolvedModel(model=None, settings=None))
# Agents built once at module level to avoid per-test construction overhead.
_AGENT_NOREASON = build_task_agent(config=_CONFIG, resolved=_TASK_RESOLVED)
# web_search case needs search="ask" so the tool is deferred rather than auto-executed.
_WEB_ASK_CFG = replace(_CONFIG, web_policy=WebPolicy(search="ask"))
_AGENT_WEB_ASK = build_task_agent(config=_WEB_ASK_CFG, resolved=_TASK_RESOLVED)


def _make_deps(session_id: str) -> CoDeps:
    return CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=_CONFIG,
        session=CoSessionState(session_id=session_id),
    )


def _make_deps_web_deferred(session_id: str) -> CoDeps:
    """Deps with web_search deferred (ask) so the test verifies tool name + query from turn 1.

    No need to execute the actual search or wait for a second LLM turn.
    """
    return CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=replace(
            _CONFIG,
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
    # web_search: registered as deferred (search="ask") — denial drives two separate
    # agent.run() calls with no tool execution between them. Both segments pay the full
    # tool-context KV-fill cost (~20s each). Uses LLM_DEFERRED_TURN_TIMEOUT_SECS (3×).
    # run_shell_command: "git status" executes inline within one agent.run() call
    # (two LLM segments inside one run, tool execution between). Uses LLM_MULTI_SEGMENT_TIMEOUT_SECS (2×).
    if expected_tool == "web_search":
        agent = _AGENT_WEB_ASK.agent
        deps = _make_deps_web_deferred(f"test-tool-{expected_tool}")
        call_timeout = LLM_DEFERRED_TURN_TIMEOUT_SECS
        # Deny deferred web_search — avoids executing the search while verifying tool selection.
        frontend = SilentFrontend(approval_response="n")
    elif expected_tool == "run_shell_command":
        agent = _AGENT_NOREASON.agent
        deps = _make_deps(f"test-tool-{expected_tool}")
        call_timeout = LLM_MULTI_SEGMENT_TIMEOUT_SECS
        frontend = SilentFrontend(approval_response="y")
    else:
        agent = _AGENT_NOREASON.agent
        deps = _make_deps(f"test-tool-{expected_tool}")
        call_timeout = LLM_MULTI_SEGMENT_TIMEOUT_SECS
        frontend = SilentFrontend(approval_response="y")

    await ensure_ollama_warm(_TASK_MODEL, _CONFIG.llm_host)
    last_details = "no run executed"
    max_attempts = 3
    for attempt in range(max_attempts):
        tool_name = None
        args = None
        try:
            async with asyncio.timeout(call_timeout):
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
        except (ModelHTTPError, ModelAPIError) as e:
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
            agent=_AGENT_NOREASON.agent,
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
            agent=_AGENT_NOREASON.agent,
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
    from co_cli.tools._background import TaskRunner, TaskStorage
    from co_cli.tools.task_control import check_task_status
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
    agent = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd())).agent
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "test-background-task-description"
    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
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


@pytest.mark.asyncio
async def test_list_background_tasks_surfaces_description(tmp_path):
    """list_background_tasks includes task descriptions in both metadata and display output."""
    from co_cli.tools._background import TaskRunner, TaskStorage
    from co_cli.tools.task_control import list_background_tasks
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
    agent = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd())).agent
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "background task list description"
    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        await runner.start_task(
            "echo hello",
            str(tmp_path),
            {"description": task_description},
            None,
        )
        await asyncio.sleep(0.5)
        result = await list_background_tasks(ctx)

    assert result["count"] == 1
    assert result["tasks"][0]["description"] == task_description
    assert task_description in result["display"]
