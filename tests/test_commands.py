"""Functional tests for slash commands and approval flow.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied

from co_cli.agent import build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings, ROLE_TASK
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from co_cli.commands._commands import (
    dispatch, CommandContext,
    SkillConfig,
    LocalOnly, ReplaceTranscript, DelegateToAgent,
)
from co_cli.display._core import console
from tests._ollama import ensure_ollama_warm
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_TASK_MODEL = _CONFIG.role_models[ROLE_TASK].model
_TASK_RESOLVED = _REGISTRY.get(ROLE_TASK, ResolvedModel(model=None, settings=None))
# Exclude MCP servers: approval tests cover local tool deferral only; MCP tool schemas
# inflate context beyond LLM_TOOL_CONTEXT_TIMEOUT_SECS even with minimal system prompt.
_CONFIG_NO_MCP = replace(_CONFIG, mcp_servers={})

# build_task_agent: minimal 1-sentence system prompt + local tools only — uses
# LLM_TOOL_CONTEXT_TIMEOUT_SECS (20s) for tool schema KV fill time.
_AGENT = build_task_agent(config=_CONFIG_NO_MCP, resolved=_TASK_RESOLVED)


def _make_ctx(
    message_history: list | None = None,
    *,
    memory_dir: "Path | None" = None,
) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    config = _CONFIG
    if memory_dir is not None:
        config = replace(config, memory_dir=memory_dir)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=config,
        session=CoSessionState(session_id="test-commands"),
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=_AGENT.agent,
        tool_names=_AGENT.tool_names,
    )


def _make_agent_and_deps():
    """Build a real task agent + deps for approval flow tests.

    Uses ROLE_TASK (reasoning_effort=none) with build_task_agent — minimal system prompt
    uses LLM_TOOL_CONTEXT_TIMEOUT_SECS (20s) — 38 tools = ~10K schema tokens = ~12s KV fill.
    """
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY, task_agent=_AGENT.agent),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(session_id="test-approval"),
    )
    return _AGENT.agent, deps


async def _trigger_shell_call(agent, deps, *, retries: int = 3):
    """Ask the LLM to run a shell command. Returns DeferredToolRequests result.

    Retries up to *retries* times because smaller models occasionally respond
    with text instead of calling the tool.
    """
    prompt = (
        "Use the run_shell_command tool to execute: git rev-parse --is-inside-work-tree\n"
        "Do NOT describe what you would do — call the tool now."
    )
    await ensure_ollama_warm(_TASK_MODEL, _CONFIG.llm_host)
    last_output = None
    for _ in range(retries):
        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
            result = await agent.run(prompt, deps=deps)
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
async def test_cmd_help_includes_status_usage():
    """/help should carry enough /status usage detail to defer per-command help."""
    ctx = _make_ctx()
    with console.capture() as cap:
        await dispatch("/help", ctx)
    output = cap.get()

    assert "/status" in output
    assert "/status <task-id>" in output


# --- State-changing commands ---


@pytest.mark.asyncio
async def test_cmd_clear():
    """/clear returns ReplaceTranscript with empty history."""
    ctx = _make_ctx(message_history=["fake_msg_1", "fake_msg_2"])
    result = await dispatch("/clear", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert result.history == []


@pytest.mark.asyncio
async def test_skills_install_local(tmp_path):
    """/skills install <path> copies file to skills_dir and registers the skill."""
    src = tmp_path / "myinstallskill.md"
    src.write_text("---\ndescription: My installed skill\n---\nDo something.", encoding="utf-8")

    skills_dir = tmp_path / ".co-cli" / "skills"
    ctx = _make_ctx()
    ctx.deps.config = replace(ctx.deps.config, skills_dir=skills_dir)
    await dispatch(f"/skills install {src}", ctx)
    assert (skills_dir / "myinstallskill.md").exists()
    assert "myinstallskill" in ctx.deps.capabilities.skill_commands


@pytest.mark.asyncio
async def test_skills_install_url_error(tmp_path):
    """/skills install with unreachable URL returns None (graceful failure)."""
    ctx = _make_ctx()
    ctx.deps.config = replace(ctx.deps.config, skills_dir=tmp_path / ".co-cli" / "skills")
    result = await dispatch("/skills install http://127.0.0.1:1/skill.md", ctx)
    assert isinstance(result, LocalOnly)


@pytest.mark.asyncio
async def test_cmd_approvals_routing_and_clear(tmp_path):
    """/approvals list routes correctly; /approvals clear removes session approval rules."""
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    ctx = _make_ctx()
    ctx.deps.session.session_approval_rules.append(SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git"))
    ctx.deps.session.session_approval_rules.append(SessionApprovalRule(kind=ApprovalKindEnum.DOMAIN, value="docs.python.org"))

    result = await dispatch("/approvals list", ctx)
    assert isinstance(result, LocalOnly)

    await dispatch("/approvals clear", ctx)
    assert ctx.deps.session.session_approval_rules == []


# --- Approval flow (programmatic, no TTY) ---


@pytest.mark.asyncio
async def test_approval_approve():
    """Approving a deferred tool call executes it and returns LLM response.

    Requires running LLM + Docker.
    """
    agent, deps = _make_agent_and_deps()
    assert deps.services.task_agent is not None, "task_agent must be built at session init"
    try:
        result = await _trigger_shell_call(agent, deps)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                usage=result.usage(),
            )

        max_hops = 5
        hops = 0
        while isinstance(resumed.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            more_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                more_approvals.approvals[call.tool_call_id] = True
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=more_approvals,
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
    agent, deps = _make_agent_and_deps()
    try:
        result = await _trigger_shell_call(agent, deps)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                usage=result.usage(),
            )

        max_hops = 5
        hops = 0
        while isinstance(resumed.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            deny_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                deny_approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=deny_approvals,
                    usage=resumed.usage(),
                )

        assert isinstance(resumed.output, str)
    finally:
        deps.services.shell.cleanup()


# --- /new session checkpoint ---


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

    ctx = CommandContext(
        message_history=[],
        deps=CoDeps(
            services=CoServices(shell=ShellBackend(), knowledge_index=idx),
            config=CoConfig(memory_dir=memory_dir),
            session=CoSessionState(session_id="test-forget-fts"),
        ),
        agent=_AGENT.agent,
        tool_names=_AGENT.tool_names,
    )

    result = await dispatch("/forget 1", ctx)

    assert isinstance(result, LocalOnly)
    assert not memory_file.exists(), "File must be deleted by /forget"
    assert len(idx.search("xyloquartz-forget-fts")) == 0, "FTS row must be evicted after /forget"

    idx.close()


# --- Safe command classification ---


# --- Two-mode dispatch boundary ---


@pytest.mark.asyncio
async def test_compact_noop_empty_history():
    """/compact on empty history returns LocalOnly — nothing to compact."""
    ctx = _make_ctx(message_history=[])
    result = await dispatch("/compact", ctx)
    assert isinstance(result, LocalOnly)


@pytest.mark.asyncio
async def test_dispatch_system_op_returns_replace_transcript():
    """System-op commands (e.g. /clear) must return ReplaceTranscript."""
    ctx = _make_ctx(message_history=["msg"])
    result = await dispatch("/clear", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert result.history == []
    assert result.compaction_applied is False


@pytest.mark.asyncio
async def test_dispatch_skill_returns_delegate_to_agent():
    """Skill dispatch must return DelegateToAgent."""
    test_skill = SkillConfig(name="test-boundary-skill", body="Do the thing.", description="test")
    ctx = _make_ctx()
    ctx.deps.capabilities.skill_commands["test-boundary-skill"] = test_skill
    result = await dispatch("/test-boundary-skill", ctx)
    assert isinstance(result, DelegateToAgent)
    assert result.delegated_input == "Do the thing."


@pytest.mark.asyncio
async def test_dispatch_unknown_command_returns_local_only():
    """Unknown slash command returns LocalOnly — stays local, no agent turn."""
    ctx = _make_ctx()
    result = await dispatch("/xyzzy-no-such-command", ctx)
    assert isinstance(result, LocalOnly)


@pytest.mark.asyncio
async def test_dispatch_builtin_takes_precedence_over_same_name_skill():
    """Built-in command must win over a skill registered with the same name."""
    # 'clear' is a builtin — registering a skill with the same name must not shadow it
    test_skill = SkillConfig(name="clear", body="skill body", description="test")
    ctx = _make_ctx(message_history=["msg"])
    ctx.deps.capabilities.skill_commands["clear"] = test_skill
    result = await dispatch("/clear", ctx)
    # Must route to builtin: ReplaceTranscript with cleared history
    assert isinstance(result, ReplaceTranscript)
    assert result.history == []

