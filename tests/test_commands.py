"""Functional tests for slash commands and approval flow.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import Agent, DeferredToolResults
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.result import DeferredToolRequests
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli._model_factory import build_model
from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.commands._commands import (
    CommandContext,
    DelegateToAgent,
    LocalOnly,
    ReplaceTranscript,
    SkillConfig,
    dispatch,
)
from co_cli.config._core import settings
from co_cli.context.orchestrate import _build_interrupted_turn_result, _TurnState, run_turn
from co_cli.context.skill_env import cleanup_skill_run_state
from co_cli.context.tool_approvals import (
    is_auto_approved,
    record_approval_choice,
    remember_tool_approval,
    resolve_approval_subject,
)
from co_cli.deps import ApprovalKindEnum, CoDeps, CoSessionState, SessionApprovalRule
from co_cli.display._core import console
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = settings
# Exclude MCP servers: agent.run() spawns their processes inline per call; these tests cover built-in tools only.
_CONFIG_NO_MCP = _CONFIG.model_copy(update={"mcp_servers": {}})
_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)
_SUMM_MODEL = _CONFIG_NO_MCP.llm.model

# Tool registry and agent built once at module level.
# Uses noreason settings for fast, non-reasoning tool-calling tests.
from co_cli.agent import build_tool_registry

_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
_AGENT = Agent(
    _LLM_MODEL.model,
    deps_type=CoDeps,
    model_settings=NOREASON_SETTINGS,
    retries=_CONFIG_NO_MCP.tool_retries,
    output_type=[str, DeferredToolRequests],
    toolsets=[_TOOL_REG.toolset, *_TOOL_REG.mcp_toolsets],
)


def _make_ctx(
    message_history: list | None = None,
    *,
    memory_dir: "Path | None" = None,
) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        **({"memory_dir": memory_dir} if memory_dir is not None else {}),
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
    ctx.deps.skills_dir = tmp_path / ".co-cli" / "skills"
    result = await dispatch("/skills install http://127.0.0.1:1/skill.md", ctx)
    assert isinstance(result, LocalOnly)


@pytest.mark.asyncio
async def test_cmd_approvals_routing_and_clear(tmp_path):
    """/approvals list routes correctly; /approvals clear removes session approval rules."""
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    ctx = _make_ctx()
    ctx.deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    ctx.deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.DOMAIN, value="docs.python.org")
    )

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
@pytest.mark.local
async def test_approval_approve():
    """Approving a deferred tool call through production orchestration executes it and returns a response.

    run_turn() with SilentFrontend(approval_response="y") exercises the full approval loop:
    deferred tool → auto-approve → execution → LLM response.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
    )
    await ensure_ollama_warm(_SUMM_MODEL, _CONFIG_NO_MCP.llm.host)
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
@pytest.mark.local
async def test_approval_deny():
    """Denying a deferred tool call through production orchestration; LLM still responds.

    run_turn() with SilentFrontend(approval_response="n") exercises the deny path:
    deferred tool → deny → LLM acknowledgement response.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
    )
    await ensure_ollama_warm(_SUMM_MODEL, _CONFIG_NO_MCP.llm.host)
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


# --- Safe command classification ---


# --- Two-mode dispatch boundary ---


@pytest.mark.asyncio
async def test_compact_noop_empty_history():
    """/compact on empty history returns LocalOnly — nothing to compact."""
    ctx = _make_ctx(message_history=[])
    result = await dispatch("/compact", ctx)
    assert isinstance(result, LocalOnly)


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


def test_is_auto_approved_false_before_remember():
    """Subject is not auto-approved when no session rule has been stored."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        session=CoSessionState(),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git log"})
    assert is_auto_approved(subject, deps) is False


def test_remember_tool_approval_stores_rule_and_auto_approves():
    """remember_tool_approval stores a session rule; subsequent is_auto_approved returns True."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        session=CoSessionState(),
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git log"})
    remember_tool_approval(subject, deps)
    assert (
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
        in deps.session.session_approval_rules
    )
    assert is_auto_approved(subject, deps) is True


def test_remember_tool_approval_is_idempotent():
    """Calling remember_tool_approval twice does not duplicate the session rule."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        session=CoSessionState(),
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
        config=make_settings(),
        session=CoSessionState(),
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
        config=make_settings(),
        session=CoSessionState(),
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
        parts=[
            ToolCallPart(
                tool_name="run_shell_command", args='{"cmd": "ls"}', tool_call_id="call-x"
            )
        ]
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
    assert any(
        "interrupted" in part.content.lower()
        for part in last.parts
        if isinstance(part, UserPromptPart)
    )


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
        deps = CoDeps(shell=ShellBackend(), config=make_settings())
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
        deps = CoDeps(shell=ShellBackend(), config=make_settings())
        cleanup_skill_run_state({key: None}, deps)
        assert key not in os.environ
    finally:
        os.environ.pop(key, None)


def test_cleanup_skill_clears_active_skill_name():
    """active_skill_name is cleared to None after cleanup."""
    deps = CoDeps(shell=ShellBackend(), config=make_settings())
    deps.runtime.active_skill_name = "my-skill"
    cleanup_skill_run_state({}, deps)
    assert deps.runtime.active_skill_name is None


# ---------------------------------------------------------------------------
# /memory list and /memory count
# ---------------------------------------------------------------------------


def _write_memory(
    memory_dir: Path, filename: str, entry_id: str, content: str, **fm_extra
) -> Path:
    """Write a minimal valid memory file and return its path."""
    from datetime import UTC, datetime

    created = fm_extra.pop("created", datetime.now(UTC).isoformat())
    kind = fm_extra.pop("kind", "memory")
    tags = fm_extra.pop("tags", [])
    tags_yaml = "[" + ", ".join(tags) + "]"
    extra_lines = "".join(f"{k}: {v}\n" for k, v in fm_extra.items())
    raw = (
        f"---\nid: {entry_id}\nkind: {kind}\ncreated: '{created}'\ntags: {tags_yaml}\n"
        f"{extra_lines}---\n\n{content}\n"
    )
    path = memory_dir / filename
    path.write_text(raw, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_cmd_memory_list_all(tmp_path):
    """/memory list with no filters shows all seeded memories."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "aaa-0001", "alpha content")
    _write_memory(memory_dir, "b.md", "bbb-0002", "beta content")
    _write_memory(memory_dir, "c.md", "ccc-0003", "gamma content")

    ctx = _make_ctx(memory_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "aaa-0001" in out
    assert "bbb-0002" in out
    assert "ccc-0003" in out


@pytest.mark.asyncio
async def test_cmd_memory_list_query(tmp_path):
    """/memory list <keyword> returns only entries whose content contains the keyword."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "aaa-0001", "unique-zeta-keyword in here")
    _write_memory(memory_dir, "b.md", "bbb-0002", "nothing special")
    _write_memory(memory_dir, "c.md", "ccc-0003", "also nothing relevant")

    ctx = _make_ctx(memory_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list unique-zeta-keyword", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "aaa-0001" in out
    assert "bbb-0002" not in out
    assert "ccc-0003" not in out


@pytest.mark.asyncio
async def test_cmd_memory_list_older_than(tmp_path):
    """/memory list --older-than 30 returns only entries older than 30 days."""
    from datetime import UTC, datetime, timedelta

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    recent_date = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    _write_memory(memory_dir, "old.md", "old-entry-id", "old content", created=old_date)
    _write_memory(memory_dir, "recent.md", "new-entry-id", "recent content", created=recent_date)

    ctx = _make_ctx(memory_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list --older-than 30", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "old-entr" in out
    assert "new-entr" not in out


@pytest.mark.asyncio
async def test_cmd_memory_list_type(tmp_path):
    """/memory list --type feedback returns only entries with type: feedback."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "fb.md", "fb-entry-id", "feedback content", type="feedback")
    _write_memory(memory_dir, "pr.md", "pr-entry-id", "project content", type="project")

    ctx = _make_ctx(memory_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list --type feedback", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "fb-entry" in out
    assert "pr-entry" not in out


@pytest.mark.asyncio
async def test_cmd_memory_count_all(tmp_path):
    """/memory count prints the total number of memories."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "id-alpha", "alpha")
    _write_memory(memory_dir, "b.md", "id-beta", "beta")
    _write_memory(memory_dir, "c.md", "id-gamma", "gamma")

    ctx = _make_ctx(memory_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory count", ctx)

    assert isinstance(result, LocalOnly)
    assert "3" in cap.get()


@pytest.mark.asyncio
async def test_cmd_memory_count_query(tmp_path):
    """/memory count <keyword> prints the count of matching entries only."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "id-match1", "xylophone-unique-token here")
    _write_memory(memory_dir, "b.md", "id-match2", "xylophone-unique-token also")
    _write_memory(memory_dir, "c.md", "id-nomatch", "nothing relevant at all")

    ctx = _make_ctx(memory_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory count xylophone-unique-token", ctx)

    assert isinstance(result, LocalOnly)
    assert "2" in cap.get()


# ---------------------------------------------------------------------------
# /memory forget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_memory_forget_no_args(tmp_path):
    """/memory forget with no args prints usage and deletes nothing (BC-1)."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    mem_file = _write_memory(memory_dir, "a.md", "id-should-stay", "some content")

    ctx = _make_ctx(memory_dir=memory_dir)
    result = await dispatch("/memory forget", ctx)

    assert isinstance(result, LocalOnly)
    assert mem_file.exists(), "File must NOT be deleted when no args supplied"


@pytest.mark.asyncio
async def test_cmd_memory_forget_confirm_yes(tmp_path):
    """/memory forget <query> with y confirmation deletes all matched files."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    f1 = _write_memory(memory_dir, "a.md", "id-del-1", "zeta-forget-token content")
    f2 = _write_memory(memory_dir, "b.md", "id-del-2", "zeta-forget-token also here")
    f3 = _write_memory(memory_dir, "c.md", "id-keep", "unrelated content")

    ctx = _make_ctx(memory_dir=memory_dir)
    ctx.input_fn = lambda _: "y"
    result = await dispatch("/memory forget zeta-forget-token", ctx)

    assert isinstance(result, LocalOnly)
    assert not f1.exists(), "Matched file 1 must be deleted"
    assert not f2.exists(), "Matched file 2 must be deleted"
    assert f3.exists(), "Unmatched file must NOT be deleted"


@pytest.mark.asyncio
async def test_cmd_memory_forget_confirm_no(tmp_path):
    """/memory forget aborts when user answers n — no files deleted."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    f1 = _write_memory(memory_dir, "a.md", "id-safe-1", "omega-abort-token content")
    f2 = _write_memory(memory_dir, "b.md", "id-safe-2", "omega-abort-token also")

    ctx = _make_ctx(memory_dir=memory_dir)
    ctx.input_fn = lambda _: "n"
    result = await dispatch("/memory forget omega-abort-token", ctx)

    assert isinstance(result, LocalOnly)
    assert f1.exists(), "File 1 must NOT be deleted on n confirmation"
    assert f2.exists(), "File 2 must NOT be deleted on n confirmation"


@pytest.mark.asyncio
async def test_cmd_memory_forget_no_match(tmp_path):
    """/memory forget <query> with no matching entries prints No memories matched."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "id-exists", "some totally different content")

    ctx = _make_ctx(memory_dir=memory_dir)
    ctx.input_fn = lambda _: "y"
    with console.capture() as cap:
        result = await dispatch("/memory forget nonexistent-zzz-query", ctx)

    assert isinstance(result, LocalOnly)
    assert "No memories matched" in cap.get()


# ---------------------------------------------------------------------------
# /memory registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_memory_registered(tmp_path):
    """/memory list dispatches successfully — command is registered in BUILTIN_COMMANDS."""
    ctx = _make_ctx(memory_dir=tmp_path)
    result = await dispatch("/memory list", ctx)
    assert isinstance(result, LocalOnly)
