"""Functional tests for slash commands, approval flow, and safe command classification.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
from dataclasses import replace

import pytest

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.usage import UsageLimits

from co_cli._approval import _is_safe_command
from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli._shell_backend import ShellBackend
from co_cli._commands import dispatch, CommandContext, COMMANDS, _cmd_skills

_CONFIG = CoConfig(
    role_models={k: list(v) for k, v in settings.role_models.items()},
    llm_provider=settings.llm_provider,
    ollama_host=settings.ollama_host,
)


def _make_ctx(message_history: list | None = None) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    agent, _, tool_names, _ = get_agent()
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=replace(_CONFIG, session_id="test-commands"),
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=agent,
        tool_names=tool_names,
    )


def _make_agent_and_deps(container_name: str = "co-test-approval"):
    """Build a real agent + deps for approval flow tests."""
    agent, model_settings, _, _ = get_agent()
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=replace(_CONFIG, session_id="test-approval"),
    )
    return agent, model_settings, deps


async def _trigger_shell_call(agent, deps, model_settings, *, retries: int = 3):
    """Ask the LLM to run a shell command. Returns DeferredToolRequests result.

    Retries up to *retries* times because smaller models occasionally respond
    with text instead of calling the tool.
    """
    prompt = (
        "Use the run_shell_command tool to execute: echo hello_approval_test\n"
        "Do NOT describe what you would do — call the tool now."
    )
    last_output = None
    for _ in range(retries):
        result = await agent.run(
            prompt,
            deps=deps,
            model_settings=model_settings,
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
    async with asyncio.timeout(60):
        handled, new_history = await dispatch("/compact", ctx)
    assert handled is True
    assert isinstance(new_history, list)
    assert len(new_history) > 0


# --- Registry sanity ---


def test_commands_registry_complete():
    """All expected commands are registered."""
    expected = {
        "help", "clear", "new", "status", "tools", "history", "compact",
        "forget", "approvals", "checkpoint", "rewind", "skills",
        "tasks", "cancel", "background",
    }
    assert set(COMMANDS.keys()) == expected


@pytest.mark.asyncio
async def test_skills_list(monkeypatch):
    """/skills list output contains a pre-populated skill name."""
    import io
    from rich.console import Console as _Console
    from rich.theme import Theme as _Theme
    from co_cli._commands import _cmd_skills, SKILL_COMMANDS, SkillCommand
    from co_cli import _commands as _cmds

    SKILL_COMMANDS["testskill"] = SkillCommand(name="testskill", description="A test skill")
    buf = io.StringIO()
    # Provide the minimal semantic styles that the table uses
    themed = _Console(file=buf, no_color=True, theme=_Theme({"accent": "default", "success": "default"}))
    monkeypatch.setattr(_cmds, "console", themed)
    try:
        ctx = _make_ctx()
        await _cmd_skills(ctx, "list")
        output = buf.getvalue()
        assert "testskill" in output
    finally:
        SKILL_COMMANDS.pop("testskill", None)


def test_skills_check():
    """_diagnose_requires_failures returns non-empty list with 'bins' for a missing binary."""
    from co_cli._commands import _diagnose_requires_failures

    result = _diagnose_requires_failures({"bins": ["definitely-not-a-real-binary-xyz"]})
    assert len(result) > 0
    assert any("bins" in r for r in result)


@pytest.mark.asyncio
async def test_skills_install_local(tmp_path, monkeypatch):
    """/skills install <path> copies file to skills_dir and registers the skill."""
    import io
    from rich.console import Console as _Console
    from co_cli._commands import SKILL_COMMANDS
    from co_cli import _commands as _cmds

    src = tmp_path / "myinstallskill.md"
    src.write_text("---\ndescription: My installed skill\n---\nDo something.", encoding="utf-8")

    skills_dir = tmp_path / ".co-cli" / "skills"
    buf = io.StringIO()
    monkeypatch.setattr(_cmds, "console", _Console(file=buf, no_color=True))

    ctx = _make_ctx()
    ctx.deps.config.skills_dir = skills_dir
    SKILL_COMMANDS.clear()
    try:
        await _cmd_skills(ctx, f"install {src}")
        assert (skills_dir / "myinstallskill.md").exists()
        assert "myinstallskill" in SKILL_COMMANDS
    finally:
        SKILL_COMMANDS.clear()


def test_skills_install_scan_warning():
    """_scan_skill_content returns non-empty list for content with rm -rf /."""
    from co_cli._commands import _scan_skill_content

    result = _scan_skill_content("rm -rf /\n")
    assert len(result) > 0
    assert any("destructive_shell" in w for w in result)


@pytest.mark.asyncio
async def test_skills_install_url_error(tmp_path, monkeypatch):
    """/skills install http://127.0.0.1:1/skill.md fails gracefully with error message."""
    import io
    from rich.console import Console as _Console
    from co_cli import _commands as _cmds

    buf = io.StringIO()
    monkeypatch.setattr(_cmds, "console", _Console(file=buf, no_color=True))

    ctx = _make_ctx()
    ctx.deps.config.skills_dir = tmp_path / ".co-cli" / "skills"

    result = await _cmd_skills(ctx, "install http://127.0.0.1:1/skill.md")
    assert result is None
    output = buf.getvalue().lower()
    assert "failed" in output or "error" in output


# --- Approval flow (programmatic, no TTY) ---


@pytest.mark.asyncio
async def test_approval_approve():
    """Approving a deferred tool call executes it and returns LLM response.

    Requires running LLM + Docker.
    """
    agent, model_settings, deps = _make_agent_and_deps("co-test-approve")
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        async with asyncio.timeout(60):
            result = await _trigger_shell_call(agent, deps, model_settings)

            approvals = DeferredToolResults()
            for call in result.output.approvals:
                approvals.approvals[call.tool_call_id] = True

            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model_settings=model_settings,
                usage_limits=turn_limits,
                usage=result.usage(),
            )

            while isinstance(resumed.output, DeferredToolRequests):
                more_approvals = DeferredToolResults()
                for call in resumed.output.approvals:
                    more_approvals.approvals[call.tool_call_id] = True
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=more_approvals,
                    model_settings=model_settings,
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
    agent, model_settings, deps = _make_agent_and_deps("co-test-deny")
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        async with asyncio.timeout(60):
            result = await _trigger_shell_call(agent, deps, model_settings)

            approvals = DeferredToolResults()
            for call in result.output.approvals:
                approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

            resumed = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model_settings=model_settings,
                usage_limits=turn_limits,
                usage=result.usage(),
            )

            while isinstance(resumed.output, DeferredToolRequests):
                deny_approvals = DeferredToolResults()
                for call in resumed.output.approvals:
                    deny_approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")
                resumed = await agent.run(
                    None,
                    deps=deps,
                    message_history=resumed.all_messages(),
                    deferred_tool_results=deny_approvals,
                    model_settings=model_settings,
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
    agent, model_settings, deps = _make_agent_and_deps("co-test-budget")
    budget = settings.max_request_limit
    turn_limits = UsageLimits(request_limit=budget)
    try:
        async with asyncio.timeout(60):
            result = await agent.run(
                "Run this exact shell command: echo budget_test",
                deps=deps,
                model_settings=model_settings,
                usage_limits=turn_limits,
            )

            while isinstance(result.output, DeferredToolRequests):
                approvals = DeferredToolResults()
                for call in result.output.approvals:
                    approvals.approvals[call.tool_call_id] = True
                result = await agent.run(
                    None,
                    deps=deps,
                    message_history=result.all_messages(),
                    deferred_tool_results=approvals,
                    model_settings=model_settings,
                    usage_limits=turn_limits,
                    usage=result.usage(),
                )

        assert result.usage().requests <= budget
        assert isinstance(result.output, str)
    finally:
        deps.services.shell.cleanup()


# --- /new session checkpoint ---


@pytest.mark.asyncio
async def test_cmd_new_checkpoints_and_clears(tmp_path, monkeypatch):
    """/new with history writes session-*.md and returns [] (clears history).

    Requires a running LLM provider.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

    monkeypatch.chdir(tmp_path)

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="What is Python?")]),
        ModelResponse(parts=[TextPart(content="Python is a programming language.")]),
        ModelRequest(parts=[UserPromptPart(content="Tell me about its history.")]),
        ModelResponse(parts=[TextPart(content="Guido van Rossum created Python in 1991.")]),
    ]
    ctx = _make_ctx(message_history=msgs)
    async with asyncio.timeout(60):
        handled, new_history = await dispatch("/new", ctx)

    assert handled is True
    assert new_history == [], "history must be cleared"

    memory_dir = tmp_path / ".co-cli" / "memory"
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
async def test_forget_command_evicts_fts_row(tmp_path, monkeypatch):
    """/forget removes the file and evicts the FTS row in the same session."""
    from co_cli._knowledge_index import KnowledgeIndex

    monkeypatch.chdir(tmp_path)
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    content = (
        "---\nid: 1\nkind: memory\ncreated: '2026-01-01T00:00:00+00:00'\ntags: []\n---\n\n"
        "xyloquartz-forget-fts eviction keyword\n"
    )
    memory_file = memory_dir / "001-test-forget.md"
    memory_file.write_text(content, encoding="utf-8")

    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.sync_dir("memory", memory_dir)
    assert len(idx.search("xyloquartz-forget-fts")) == 1

    agent, _, tool_names, _ = get_agent()
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


_SAFE_LIST = ["ls", "cat", "grep", "git status", "git diff", "git log"]


def test_safe_command_multi_word_prefix():
    """Multi-word prefix like 'git status' matches, but 'git push' does not."""
    assert _is_safe_command("git status", _SAFE_LIST) is True
    assert _is_safe_command("git status --short", _SAFE_LIST) is True
    assert _is_safe_command("git diff HEAD~1", _SAFE_LIST) is True
    assert _is_safe_command("git push origin main", _SAFE_LIST) is False
    assert _is_safe_command("git commit -m 'test'", _SAFE_LIST) is False


def test_safe_command_chaining_rejected():
    """Shell chaining operators always force approval."""
    assert _is_safe_command("ls; rm -rf /", _SAFE_LIST) is False
    assert _is_safe_command("cat file && rm file", _SAFE_LIST) is False
    assert _is_safe_command("ls || echo fail", _SAFE_LIST) is False
    assert _is_safe_command("ls | wc -l", _SAFE_LIST) is False
    assert _is_safe_command("echo `whoami`", _SAFE_LIST) is False
    assert _is_safe_command("echo $(whoami)", _SAFE_LIST) is False
    assert _is_safe_command("ls & rm -rf /", _SAFE_LIST) is False
    assert _is_safe_command("ls > /tmp/out", _SAFE_LIST) is False
    assert _is_safe_command("ls >> /tmp/out", _SAFE_LIST) is False
    assert _is_safe_command("sort < /etc/passwd", _SAFE_LIST) is False
    assert _is_safe_command("cat << EOF", _SAFE_LIST) is False
    assert _is_safe_command("ls\nrm -rf /", _SAFE_LIST) is False


def test_safe_command_partial_name_no_match():
    """A command starting with a safe prefix but not followed by space should not match."""
    assert _is_safe_command("lsblk", _SAFE_LIST) is False
    assert _is_safe_command("caterpillar", _SAFE_LIST) is False
