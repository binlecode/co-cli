"""Functional tests for slash commands and approval flow.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied

from co_cli.agent import build_agent
from co_cli._model_factory import ModelRegistry
from co_cli.config import settings, ROLE_SUMMARIZATION
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from co_cli.commands._commands import (
    dispatch, CommandContext,
    SkillConfig, _cmd_help, _cmd_skills,
    LocalOnly, ReplaceTranscript, DelegateToAgent,
)
from co_cli.display._core import console
from tests._ollama import ensure_ollama_warm
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_SUMMARIZATION_MODEL = _CONFIG.role_models["summarization"].model


_AGENT = build_agent(config=_CONFIG)


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
    """Build a real agent + deps for approval flow tests.

    Returns (agent, resolved_trigger, resolved_resume, deps):
    - resolved_trigger: summarization model (reasoning_effort=none) for turn 1 tool invocation
    - resolved_resume: summarization model for post-approval turns
    """
    from co_cli._model_factory import ResolvedModel
    _fallback = ResolvedModel(model=None, settings=None)
    resolved_trigger = _REGISTRY.get(ROLE_SUMMARIZATION, _fallback)
    resolved_resume = _REGISTRY.get(ROLE_SUMMARIZATION, _fallback)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=_CONFIG,
        session=CoSessionState(session_id="test-approval"),
    )
    return _AGENT.agent, resolved_trigger, resolved_resume, deps


async def _trigger_shell_call(agent, deps, resolved, *, retries: int = 3):
    """Ask the LLM to run a shell command. Returns DeferredToolRequests result.

    Retries up to *retries* times because smaller models occasionally respond
    with text instead of calling the tool.
    """
    prompt = (
        "Use the run_shell_command tool to execute: git rev-parse --is-inside-work-tree\n"
        "Do NOT describe what you would do — call the tool now."
    )
    await ensure_ollama_warm(_SUMMARIZATION_MODEL, _CONFIG.llm_host)
    last_output = None
    for _ in range(retries):
        async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
            result = await agent.run(
                prompt,
                deps=deps,
                model=resolved.model,
                model_settings=resolved.settings,

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
async def test_cmd_help_includes_status_usage():
    """/help should carry enough /status usage detail to defer per-command help."""
    ctx = _make_ctx()
    with console.capture() as cap:
        await _cmd_help(ctx, "")
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
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await dispatch("/compact", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert result.compaction_applied is True
    assert isinstance(result.history, list)
    assert len(result.history) > 0


@pytest.mark.asyncio
async def test_skills_install_local(tmp_path):
    """/skills install <path> copies file to skills_dir and registers the skill."""
    src = tmp_path / "myinstallskill.md"
    src.write_text("---\ndescription: My installed skill\n---\nDo something.", encoding="utf-8")

    skills_dir = tmp_path / ".co-cli" / "skills"
    ctx = _make_ctx()
    ctx.deps.config = replace(ctx.deps.config, skills_dir=skills_dir)
    await _cmd_skills(ctx, f"install {src}")
    assert (skills_dir / "myinstallskill.md").exists()
    assert "myinstallskill" in ctx.deps.capabilities.skill_commands


@pytest.mark.asyncio
async def test_skills_install_url_error(tmp_path):
    """/skills install with unreachable URL returns None (graceful failure)."""
    ctx = _make_ctx()
    ctx.deps.config = replace(ctx.deps.config, skills_dir=tmp_path / ".co-cli" / "skills")
    result = await _cmd_skills(ctx, "install http://127.0.0.1:1/skill.md")
    assert result is None


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


def test_approvals_list_rendering() -> None:
    """_rule_label returns correct human-readable (label, hint) pairs for all valid kinds."""
    from co_cli.commands._commands import _rule_label

    assert _rule_label("shell", "git") == ("shell utility", "git")
    assert _rule_label("path", "/proj/src") == ("writable dir", "/proj/src/**")
    assert _rule_label("domain", "x.com") == ("web domain", "x.com")
    assert _rule_label("tool", "save_memory") == ("tool", "save_memory")

    # No mcp_tool string may appear in any returned label or hint
    for kind in ("shell", "path", "domain", "tool"):
        label, hint = _rule_label(kind, "some_value")
        assert "mcp_tool" not in label
        assert "mcp_tool" not in hint


# --- Approval flow (programmatic, no TTY) ---


@pytest.mark.asyncio
async def test_approval_approve():
    """Approving a deferred tool call executes it and returns LLM response.

    Requires running LLM + Docker.
    """
    agent, resolved_trigger, resolved_resume, deps = _make_agent_and_deps()
    try:
        result = await _trigger_shell_call(agent, deps, resolved_trigger)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True

        async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model=resolved_resume.model,
                model_settings=resolved_resume.settings,
                usage=result.usage(),
            )

        max_hops = 5
        hops = 0
        while isinstance(resumed.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            more_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                more_approvals.approvals[call.tool_call_id] = True
            async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=more_approvals,
                    model=resolved_resume.model,
                    model_settings=resolved_resume.settings,
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
    try:
        result = await _trigger_shell_call(agent, deps, resolved_trigger)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

        async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model=resolved_resume.model,
                model_settings=resolved_resume.settings,
                usage=result.usage(),
            )

        max_hops = 5
        hops = 0
        while isinstance(resumed.output, DeferredToolRequests) and hops < max_hops:
            hops += 1
            deny_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                deny_approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")
            async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=deny_approvals,
                    model=resolved_resume.model,
                    model_settings=resolved_resume.settings,
                    usage=resumed.usage(),
                )

        assert isinstance(resumed.output, str)
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
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await dispatch("/new", ctx)

    assert isinstance(result, ReplaceTranscript)
    assert result.history == [], "history must be cleared"

    session_files = list(memory_dir.glob("session-*.md"))
    assert len(session_files) == 1, "exactly one session file must be written"

    content = session_files[0].read_text(encoding="utf-8")
    assert "provenance: session" in content, "frontmatter must contain provenance: session"

    from co_cli.knowledge._frontmatter import parse_frontmatter
    fm, _ = parse_frontmatter(content)
    assert fm.get("artifact_type") == "session_summary", (
        "session checkpoint must have artifact_type: session_summary in frontmatter"
    )


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


