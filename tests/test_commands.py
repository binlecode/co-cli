"""Functional tests for slash commands and approval flow.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
import json
from dataclasses import replace

import pytest

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli._model_factory import ModelRegistry
from co_cli.config import settings, ROLE_REASONING, ROLE_SUMMARIZATION
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.commands._commands import dispatch, CommandContext, _cmd_skills
from tests._ollama import ensure_ollama_warm

_CONFIG = CoConfig.from_settings(settings)
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_REASONING_MODEL = _CONFIG.role_models[ROLE_REASONING].model
_SUMMARIZATION_MODEL = _CONFIG.role_models["summarization"].model


def _make_ctx(
    message_history: list | None = None,
    *,
    memory_dir: "Path | None" = None,
) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    from pathlib import Path
    agent, tool_names, _ = get_agent()
    config = replace(_CONFIG, session_id="test-commands")
    if memory_dir is not None:
        config = replace(config, memory_dir=memory_dir)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=config,
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=agent,
        tool_names=tool_names,
    )


def _make_agent_and_deps():
    """Build a real agent + deps for approval flow tests.

    Returns (agent, resolved_trigger, resolved_resume, deps):
    - resolved_trigger: reasoning model for turn 1 tool invocation
    - resolved_resume: summarization model (think=False) for post-approval turns
    """
    from co_cli._model_factory import ResolvedModel
    agent, _, _ = get_agent()
    _fallback = ResolvedModel(model=None, settings=None)
    resolved_trigger = _REGISTRY.get(ROLE_REASONING, _fallback)
    resolved_resume = _REGISTRY.get(ROLE_SUMMARIZATION, _fallback)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=replace(_CONFIG, session_id="test-approval"),
    )
    return agent, resolved_trigger, resolved_resume, deps


async def _trigger_shell_call(agent, deps, resolved, *, retries: int = 3):
    """Ask the LLM to run a shell command. Returns DeferredToolRequests result.

    Retries up to *retries* times because smaller models occasionally respond
    with text instead of calling the tool.
    """
    prompt = (
        "Use the run_shell_command tool to execute: git rev-parse --is-inside-work-tree\n"
        "Do NOT describe what you would do — call the tool now."
    )
    await ensure_ollama_warm(_REASONING_MODEL, _CONFIG.llm_host)
    last_output = None
    for _ in range(retries):
        async with asyncio.timeout(60):
            result = await agent.run(
                prompt,
                deps=deps,
                model=resolved.model,
                model_settings=resolved.settings,
                usage_limits=UsageLimits(request_limit=settings.max_request_limit),
            )
        if isinstance(result.output, DeferredToolRequests):
            assert len(result.output.approvals) > 0
            return result
        last_output = result.output
    pytest.fail(
        f"Expected DeferredToolRequests after {retries} attempts, "
        f"got {type(last_output).__name__}: {last_output!r}"
    )


# --- Dispatch routing ---


@pytest.mark.asyncio
async def test_dispatch_non_slash():
    """Non-slash input returns (False, None) — not consumed."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("hello world", ctx)
    assert handled is False
    assert new_history is None


@pytest.mark.asyncio
async def test_dispatch_unknown_command():
    """Unknown /command returns (True, None) — consumed, no crash."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/unknown", ctx)
    assert handled is True
    assert new_history is None


# --- State-changing commands ---


@pytest.mark.asyncio
async def test_cmd_clear():
    """/clear returns empty list."""
    ctx = _make_ctx(message_history=["fake_msg_1", "fake_msg_2"])
    handled, new_history = await dispatch("/clear", ctx)
    assert handled is True
    assert new_history == []


@pytest.mark.asyncio
async def test_cmd_compact():
    """/compact with seeded history returns a new list.

    Requires a running LLM provider.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="What is Docker?")]),
    ]
    ctx = _make_ctx(message_history=msgs)
    await ensure_ollama_warm(_SUMMARIZATION_MODEL, _CONFIG.llm_host)
    async with asyncio.timeout(60):
        handled, new_history = await dispatch("/compact", ctx)
    assert handled is True
    assert isinstance(new_history, list)
    assert len(new_history) > 0


@pytest.mark.asyncio
async def test_skills_install_local(tmp_path):
    """/skills install <path> copies file to skills_dir and registers the skill."""
    from co_cli.commands._commands import SKILL_COMMANDS

    src = tmp_path / "myinstallskill.md"
    src.write_text("---\ndescription: My installed skill\n---\nDo something.", encoding="utf-8")

    skills_dir = tmp_path / ".co-cli" / "skills"
    ctx = _make_ctx()
    ctx.deps.config.skills_dir = skills_dir
    orig_skills = dict(SKILL_COMMANDS)
    SKILL_COMMANDS.clear()
    try:
        await _cmd_skills(ctx, f"install {src}")
        assert (skills_dir / "myinstallskill.md").exists()
        assert "myinstallskill" in SKILL_COMMANDS
    finally:
        SKILL_COMMANDS.clear()
        SKILL_COMMANDS.update(orig_skills)


@pytest.mark.asyncio
async def test_skills_install_url_error(tmp_path):
    """/skills install with unreachable URL returns None (graceful failure)."""
    ctx = _make_ctx()
    ctx.deps.config.skills_dir = tmp_path / ".co-cli" / "skills"
    result = await _cmd_skills(ctx, "install http://127.0.0.1:1/skill.md")
    assert result is None


@pytest.mark.asyncio
async def test_cmd_approvals_routing_and_clear(tmp_path):
    """/approvals list routes correctly; /approvals clear removes persisted approvals from disk."""
    approvals_path = tmp_path / ".co-cli" / "exec-approvals.json"
    approvals_path.parent.mkdir(parents=True)
    approvals_path.write_text(json.dumps([{
        "id": "abc123",
        "pattern": "git commit *",
        "tool_name": "run_shell_command",
        "created_at": "2026-03-10T00:00:00Z",
        "last_used_at": "2026-03-10T00:00:00Z",
    }]), encoding="utf-8")

    ctx = _make_ctx()
    ctx.deps.config.exec_approvals_path = approvals_path

    handled, _ = await dispatch("/approvals list", ctx)
    assert handled is True

    await dispatch("/approvals clear", ctx)
    assert json.loads(approvals_path.read_text(encoding="utf-8")) == []


# --- Approval flow (programmatic, no TTY) ---


@pytest.mark.asyncio
async def test_approval_approve():
    """Approving a deferred tool call executes it and returns LLM response.

    Requires running LLM + Docker.
    """
    agent, resolved_trigger, resolved_resume, deps = _make_agent_and_deps()
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        result = await _trigger_shell_call(agent, deps, resolved_trigger)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True

        async with asyncio.timeout(45):
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model=resolved_resume.model,
                model_settings=resolved_resume.settings,
                usage_limits=turn_limits,
                usage=result.usage(),
            )

        max_hops = 5
        hops = 0
        while isinstance(resumed.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            more_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                more_approvals.approvals[call.tool_call_id] = True
            async with asyncio.timeout(45):
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=more_approvals,
                    model=resolved_resume.model,
                    model_settings=resolved_resume.settings,
                    usage_limits=turn_limits,
                    usage=resumed.usage(),
                )

        assert isinstance(resumed.output, str)
        assert len(resumed.all_messages()) > 0
    finally:
        deps.services.shell.cleanup()


@pytest.mark.asyncio
async def test_approval_deny():
    """Denying a deferred tool call sends ToolDenied; LLM still responds.

    Requires running LLM + Docker.
    """
    agent, resolved_trigger, resolved_resume, deps = _make_agent_and_deps()
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        result = await _trigger_shell_call(agent, deps, resolved_trigger)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

        async with asyncio.timeout(45):
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model=resolved_resume.model,
                model_settings=resolved_resume.settings,
                usage_limits=turn_limits,
                usage=result.usage(),
            )

        max_hops = 5
        hops = 0
        while isinstance(resumed.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            deny_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                deny_approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")
            async with asyncio.timeout(45):
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=deny_approvals,
                    model=resolved_resume.model,
                    model_settings=resolved_resume.settings,
                    usage_limits=turn_limits,
                    usage=resumed.usage(),
                )

        assert isinstance(resumed.output, str)
    finally:
        deps.services.shell.cleanup()


@pytest.mark.asyncio
async def test_approval_budget_cumulative():
    """Multi-hop approval cannot exceed a single per-turn request budget.

    Requires running LLM + Docker.
    """
    agent, resolved_trigger, resolved_resume, deps = _make_agent_and_deps()
    budget = settings.max_request_limit
    turn_limits = UsageLimits(request_limit=budget)
    try:
        async with asyncio.timeout(60):
            result = await agent.run(
                "Run this exact shell command: git rev-parse --is-inside-work-tree",
                deps=deps,
                model=resolved_trigger.model,
                model_settings=resolved_trigger.settings,
                usage_limits=turn_limits,
            )

        max_hops = 5
        hops = 0
        while isinstance(result.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            approvals = DeferredToolResults()
            for call in result.output.approvals:
                approvals.approvals[call.tool_call_id] = True
            async with asyncio.timeout(45):
                result = await agent.run(
                    None,
                    deps=deps,
                    message_history=result.all_messages(),
                    deferred_tool_results=approvals,
                    model=resolved_resume.model,
                    model_settings=resolved_resume.settings,
                    usage_limits=turn_limits,
                    usage=result.usage(),
                )

        assert result.usage().requests <= budget
        assert isinstance(result.output, str)
    finally:
        deps.services.shell.cleanup()


# --- /new session checkpoint ---


@pytest.mark.asyncio
async def test_cmd_new_checkpoints_and_clears(tmp_path):
    """/new with history writes session-*.md and returns [] (clears history).

    Requires a running LLM provider.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

    memory_dir = tmp_path / ".co-cli" / "memory"
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="What is Python?")]),
        ModelResponse(parts=[TextPart(content="Python is a programming language.")]),
        ModelRequest(parts=[UserPromptPart(content="Tell me about its history.")]),
        ModelResponse(parts=[TextPart(content="Guido van Rossum created Python in 1991.")]),
    ]
    ctx = _make_ctx(message_history=msgs, memory_dir=memory_dir)
    await ensure_ollama_warm(_SUMMARIZATION_MODEL, _CONFIG.llm_host)
    async with asyncio.timeout(60):
        handled, new_history = await dispatch("/new", ctx)

    assert handled is True
    assert new_history == [], "history must be cleared"

    session_files = list(memory_dir.glob("session-*.md"))
    assert len(session_files) == 1, "exactly one session file must be written"

    content = session_files[0].read_text(encoding="utf-8")
    assert "provenance: session" in content, "frontmatter must contain provenance: session"


@pytest.mark.asyncio
async def test_cmd_new_empty_history_noop():
    """/new with empty history prints a message and returns None (no-op)."""
    ctx = _make_ctx(message_history=[])
    handled, new_history = await dispatch("/new", ctx)

    assert handled is True
    assert new_history is None, "must return None (no-op) on empty history"


# --- /forget FTS eviction ---


@pytest.mark.asyncio
async def test_forget_command_evicts_fts_row(tmp_path):
    """/forget removes the file and evicts the FTS row in the same session."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    content = (
        "---\nid: 1\nkind: memory\ncreated: '2026-01-01T00:00:00+00:00'\ntags: []\n---\n\n"
        "xyloquartz-forget-fts eviction keyword\n"
    )
    memory_file = memory_dir / "001-test-forget.md"
    memory_file.write_text(content, encoding="utf-8")

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.sync_dir("memory", memory_dir)
    assert len(idx.search("xyloquartz-forget-fts")) == 1

    agent, tool_names, _ = get_agent()
    ctx = CommandContext(
        message_history=[],
        deps=CoDeps(
            services=CoServices(shell=ShellBackend(), knowledge_index=idx),
            config=CoConfig(session_id="test-forget-fts", memory_dir=memory_dir),
        ),
        agent=agent,
        tool_names=tool_names,
    )

    handled, _ = await dispatch("/forget 1", ctx)

    assert handled is True
    assert not memory_file.exists(), "File must be deleted by /forget"
    assert len(idx.search("xyloquartz-forget-fts")) == 0, "FTS row must be evicted after /forget"

    idx.close()


# --- Safe command classification ---
