"""Functional tests for slash commands and approval flow.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from pydantic_ai import DeferredToolResults
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart

from co_cli.agent import build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings, ROLE_TASK
from co_cli.deps import ApprovalKindEnum, CoDeps, CoConfig, CoSessionState, SessionApprovalRule
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.tool_approvals import (
    is_auto_approved,
    record_approval_choice,
    remember_tool_approval,
    resolve_approval_subject,
)
from co_cli.commands._commands import (
    dispatch, CommandContext,
    SkillConfig,
    LocalOnly, ReplaceTranscript, DelegateToAgent,
)
from co_cli.context._orchestrate import _TurnState, _build_interrupted_turn_result, run_turn
from co_cli.context._skill_env import cleanup_skill_run_state
from co_cli.display._core import console
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
# Exclude MCP servers: agent.run() spawns their processes inline per call; these tests cover built-in tools only.
_CONFIG_NO_MCP = replace(_CONFIG, mcp_servers={})
_REGISTRY = ModelRegistry.from_config(_CONFIG_NO_MCP)
_TASK_MODEL = _CONFIG_NO_MCP.role_models[ROLE_TASK].model
_TASK_RESOLVED = _REGISTRY.get(ROLE_TASK, ResolvedModel(model=None, settings=None))

# Tool registry and task agent built once at module level.
from co_cli.agent import build_tool_registry
_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
_AGENT = build_task_agent(config=_CONFIG_NO_MCP, role_model=_TASK_RESOLVED, tool_registry=_TOOL_REG)


def _make_ctx(
    message_history: list | None = None,
    *,
    memory_dir: "Path | None" = None,
) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    config = _CONFIG_NO_MCP
    if memory_dir is not None:
        config = replace(config, memory_dir=memory_dir)
    deps = CoDeps(
        shell=ShellBackend(),
        model_registry=_REGISTRY,
        tool_index=dict(_TOOL_REG.tool_index),
        config=config,
        session=CoSessionState(session_id="test-commands"),
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=_AGENT,
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

_PROMPT_SHELL = (
    "Use the run_shell_command tool to execute: git rev-parse --is-inside-work-tree\n"
    "Do NOT describe what you would do — call the tool now."
)


@pytest.mark.asyncio
async def test_approval_approve():
    """Approving a deferred tool call through production orchestration executes it and returns a response.

    run_turn() with SilentFrontend(approval_response="y") exercises the full approval loop:
    deferred tool → auto-approve → execution → LLM response.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model_registry=_REGISTRY,
        task_agents={"task": _AGENT},
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(session_id="test-approval"),
    )
    await ensure_ollama_warm(_TASK_MODEL, _CONFIG_NO_MCP.llm_host)
    try:
        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
            turn = await run_turn(
                agent=_AGENT,
                user_input=_PROMPT_SHELL,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(approval_response="y"),
            )
        # Verify a tool call was attempted (shell command was deferred and processed)
        tool_called = any(
            isinstance(part, ToolCallPart)
            for msg in turn.messages
            if isinstance(msg, ModelResponse)
            for part in msg.parts
        )
        assert tool_called, "Expected run_shell_command to be called and approved"
        assert isinstance(turn.output, str)
        assert len(turn.messages) > 0
    finally:
        deps.shell.cleanup()


@pytest.mark.asyncio
async def test_approval_deny():
    """Denying a deferred tool call through production orchestration; LLM still responds.

    run_turn() with SilentFrontend(approval_response="n") exercises the deny path:
    deferred tool → deny → LLM acknowledgement response.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model_registry=_REGISTRY,
        task_agents={"task": _AGENT},
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(session_id="test-denial"),
    )
    await ensure_ollama_warm(_TASK_MODEL, _CONFIG_NO_MCP.llm_host)
    try:
        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
            turn = await run_turn(
                agent=_AGENT,
                user_input=_PROMPT_SHELL,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(approval_response="n"),
            )
        assert isinstance(turn.output, str)
    finally:
        deps.shell.cleanup()


# --- /new session checkpoint ---


# --- /forget FTS eviction ---


@pytest.mark.asyncio
async def test_forget_command_evicts_fts_row(tmp_path):
    """/forget removes the file and evicts the FTS row in the same session."""
    from co_cli.knowledge._store import KnowledgeStore

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    content = (
        "---\nid: 1\nkind: memory\ncreated: '2026-01-01T00:00:00+00:00'\ntags: []\n---\n\n"
        "xyloquartz-forget-fts eviction keyword\n"
    )
    memory_file = memory_dir / "001-test-forget.md"
    memory_file.write_text(content, encoding="utf-8")

    idx = KnowledgeStore(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.sync_dir("memory", memory_dir)
    assert len(idx.search("xyloquartz-forget-fts")) == 1

    ctx = CommandContext(
        message_history=[],
        deps=CoDeps(
            shell=ShellBackend(), knowledge_store=idx,
            config=CoConfig(memory_dir=memory_dir),
            session=CoSessionState(session_id="test-forget-fts"),
        ),
        agent=_AGENT,
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
    ctx.deps.skill_commands["test-boundary-skill"] = test_skill
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
    ctx.deps.skill_commands["clear"] = test_skill
    result = await dispatch("/clear", ctx)
    # Must route to builtin: ReplaceTranscript with cleared history
    assert isinstance(result, ReplaceTranscript)
    assert result.history == []


# --- Approval subject scoping ---


def test_resolve_approval_subject_shell_scopes_to_utility():
    """Shell subject resolves to the first token of the command."""
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git status --short"})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.value == "git"
    assert subject.can_remember is True


def test_resolve_approval_subject_path_scopes_to_parent_dir():
    """File-write subject resolves to the parent directory of the target path."""
    subject = resolve_approval_subject("write_file", {"path": "/home/user/project/file.txt"})
    assert subject.kind == ApprovalKindEnum.PATH
    assert subject.value == "/home/user/project"
    assert subject.can_remember is True


def test_resolve_approval_subject_domain_scopes_to_hostname():
    """Web-fetch subject resolves to the hostname of the target URL."""
    subject = resolve_approval_subject("web_fetch", {"url": "https://docs.python.org/3/library/asyncio.html"})
    assert subject.kind == ApprovalKindEnum.DOMAIN
    assert subject.value == "docs.python.org"
    assert subject.can_remember is True


def test_resolve_approval_subject_generic_tool_fallback():
    """Unknown tools fall through to the generic-tool branch, keyed by tool name."""
    subject = resolve_approval_subject("create_gmail_draft", {"to": "test@example.com", "subject": "hi"})
    assert subject.kind == ApprovalKindEnum.TOOL
    assert subject.value == "create_gmail_draft"
    assert subject.can_remember is True


def test_is_auto_approved_false_before_remember():
    """Subject is not auto-approved when no session rule has been stored."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(),
        session=CoSessionState(session_id="test-autoapprove"),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git log"})
    assert is_auto_approved(subject, deps) is False


def test_remember_tool_approval_stores_rule_and_auto_approves():
    """remember_tool_approval stores a session rule; subsequent is_auto_approved returns True."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(),
        session=CoSessionState(session_id="test-remember"),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git log"})
    remember_tool_approval(subject, deps)
    assert SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git") in deps.session.session_approval_rules
    assert is_auto_approved(subject, deps) is True


def test_remember_tool_approval_is_idempotent():
    """Calling remember_tool_approval twice does not duplicate the session rule."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(),
        session=CoSessionState(session_id="test-idem"),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    remember_tool_approval(subject, deps)
    remember_tool_approval(subject, deps)
    rule = SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    assert deps.session.session_approval_rules.count(rule) == 1


def test_record_approval_choice_with_remember_stores_rule():
    """record_approval_choice with remember=True stores a session rule via remember_tool_approval."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(),
        session=CoSessionState(session_id="test-record"),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git push"})
    approvals = DeferredToolResults()
    record_approval_choice(
        approvals,
        tool_call_id="call-1",
        approved=True,
        subject=subject,
        deps=deps,
        remember=True,
    )
    assert approvals.approvals["call-1"] is True
    assert is_auto_approved(subject, deps) is True


def test_record_approval_choice_deny_does_not_store_rule():
    """Denied approvals must not persist a session rule."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(),
        session=CoSessionState(session_id="test-deny-record"),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git push"})
    approvals = DeferredToolResults()
    record_approval_choice(
        approvals,
        tool_call_id="call-2",
        approved=False,
        subject=subject,
        deps=deps,
        remember=False,
    )
    assert is_auto_approved(subject, deps) is False
    assert deps.session.session_approval_rules == []


# --- Orchestration: interrupted turn result ---


def test_build_interrupted_turn_result_drops_dangling_tool_call():
    """_build_interrupted_turn_result drops the last ModelResponse when it has unanswered ToolCallParts.

    The dangling tool call response is removed so history ends at a clean point,
    and an abort marker is appended for the next turn.
    """
    clean_request = ModelRequest(parts=[UserPromptPart(content="run ls")])
    dangling_response = ModelResponse(
        parts=[ToolCallPart(tool_name="run_shell_command", args='{"cmd": "ls"}', tool_call_id="call-x")]
    )
    turn_state = _TurnState(
        current_input=None,
        current_history=[clean_request, dangling_response],
    )

    result = _build_interrupted_turn_result(turn_state)

    assert result.interrupted is True
    assert result.outcome == "continue"
    assert dangling_response not in result.messages
    # Abort marker must be the last message
    last = result.messages[-1]
    assert isinstance(last, ModelRequest)
    assert any("interrupted" in part.content.lower() for part in last.parts if isinstance(part, UserPromptPart))


def test_build_interrupted_turn_result_keeps_clean_history():
    """_build_interrupted_turn_result preserves messages that have no dangling ToolCallParts."""
    clean_request = ModelRequest(parts=[UserPromptPart(content="hello")])
    clean_response = ModelResponse(parts=[])
    turn_state = _TurnState(
        current_input=None,
        current_history=[clean_request, clean_response],
    )

    result = _build_interrupted_turn_result(turn_state)

    assert result.interrupted is True
    # Both original messages are retained — neither is a dangling tool call response
    assert clean_request in result.messages
    assert clean_response in result.messages



# ---------------------------------------------------------------------------
# cleanup_skill_run_state — env var restore and skill name clear
# ---------------------------------------------------------------------------


def test_cleanup_skill_restores_set_env_var():
    """Key that was set before skill run is restored to its original value."""
    import os
    key = "TEST_CO_SKILL_RESTORE_KEY"
    original = "original-value"
    os.environ[key] = original
    try:
        os.environ[key] = "skill-injected-value"
        deps = CoDeps(shell=ShellBackend(), config=CoConfig())
        cleanup_skill_run_state({key: original}, deps)
        assert os.environ[key] == original
    finally:
        os.environ.pop(key, None)


def test_cleanup_skill_removes_absent_env_var():
    """Key that was absent before skill run is removed after cleanup."""
    import os
    key = "TEST_CO_SKILL_ABSENT_KEY"
    os.environ.pop(key, None)
    os.environ[key] = "skill-injected-value"
    try:
        deps = CoDeps(shell=ShellBackend(), config=CoConfig())
        cleanup_skill_run_state({key: None}, deps)
        assert key not in os.environ
    finally:
        os.environ.pop(key, None)


def test_cleanup_skill_clears_active_skill_name():
    """active_skill_name is cleared to None after cleanup."""
    deps = CoDeps(shell=ShellBackend(), config=CoConfig())
    deps.runtime.active_skill_name = "my-skill"
    cleanup_skill_run_state({}, deps)
    assert deps.runtime.active_skill_name is None
