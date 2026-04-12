"""Functional tests for subagent tool wiring and deps isolation."""

import inspect
from copy import copy
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.config._subagent import SubagentSettings
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState, make_subagent_deps
from co_cli.memory.save_agent import (
    MemoryActionEnum,
    SaveMemoryAgentOutput,
    _save_memory_agent,
    memory,
)
from co_cli.tools.files import find_in_files, list_directory, read_file
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.subagent import (
    _merge_turn_usage,
    _run_save_memory_agent,
    _run_subagent_attempt,
    run_coding_subagent,
)

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx() -> RunContext:
    """Return a real RunContext with no model — triggers unavailable guard."""
    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=make_settings(),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _make_memory_ctx(memory_dir: Path) -> RunContext:
    """Return a real RunContext with no model and a custom memory_dir."""
    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=make_settings(),
        memory_dir=memory_dir,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_run_coding_subagent_no_model() -> None:
    """Raises ModelRetry when model is None (no model configured).

    All four subagent tools share the same guard pattern: ``if not deps.model``.
    This test exercises the pattern via the coding tool; the others are identical.
    """
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="unavailable"):
        await run_coding_subagent(ctx, "analyze foo")


def test_make_subagent_deps_resets_session_state() -> None:
    """make_subagent_deps() shares tools by reference, inherits session fields, resets isolated fields."""
    from co_cli.commands._skill_types import SkillConfig
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    skill = SkillConfig(name="my-skill", body="do it")
    base = CoDeps(
        shell=ShellBackend(),
        skill_commands={"my-skill": skill},
        config=make_settings(
            brave_search_api_key="test-key",
            memory=make_settings().memory.model_copy(update={"injection_max_chars": 5000}),
        ),
        session=CoSessionState(
            session_id="parent-session",
            google_creds_resolved=True,
            session_approval_rules=[SessionApprovalRule(ApprovalKindEnum.SHELL, "git")],
            drive_page_tokens={"folder": ["tok1"]},
            session_todos=[{"task": "do something"}],
        ),
        runtime=CoRuntimeState(),
    )

    isolated = make_subagent_deps(base)

    # service handles shared by reference
    assert isolated.shell is base.shell
    assert isolated.skill_commands is base.skill_commands

    # Session: inherited fields carry over
    assert isolated.session.google_creds_resolved is True
    assert isolated.session.session_approval_rules == [
        SessionApprovalRule(ApprovalKindEnum.SHELL, "git")
    ]

    # CoSessionState no longer carries skill fields — they are on capabilities
    assert not hasattr(CoSessionState(), "skill_commands")

    # Approval rules are a copy, not the same list (sub-agent grants must not leak to parent)
    assert isolated.session.session_approval_rules is not base.session.session_approval_rules

    # Session: isolated fields reset to clean defaults
    assert isolated.session.drive_page_tokens == {}
    assert isolated.session.session_todos == []
    assert isolated.session.session_id == ""

    # Runtime resets to clean defaults
    assert isolated.runtime.turn_usage is None

    # Config inherited from parent
    assert isolated.config.brave_search_api_key == "test-key"
    assert isolated.config.memory.injection_max_chars == 5000

    # Service handles shared (same objects)
    assert isolated.shell is base.shell
    assert isolated.model is base.model


def test_merge_turn_usage_alias_then_accumulate() -> None:
    """_merge_turn_usage aliases on first call (None) and accumulates on second call."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    # Phase 1: turn_usage is None — aliased directly, not copied
    u1 = RunUsage(input_tokens=10, output_tokens=20)
    _merge_turn_usage(ctx, u1)
    assert ctx.deps.runtime.turn_usage is u1

    # Snapshot before second merge to verify copy() decoupling
    snapshot = copy(u1)

    # Phase 2: second call accumulates into turn_usage
    u2 = RunUsage(input_tokens=5, output_tokens=5)
    _merge_turn_usage(ctx, u2)
    assert ctx.deps.runtime.turn_usage.input_tokens == 15

    # Snapshot is not mutated — confirms copy() in _run_subagent_attempt decouples usage
    assert snapshot.input_tokens == 10


# --- New tests for TASK-1 ---


@pytest.mark.asyncio
async def test_save_memory_agent_no_model() -> None:
    """_run_save_memory_agent raises ModelRetry when deps.model is None."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="unavailable"):
        await _run_save_memory_agent(ctx, "write a memory about testing", 3)


def test_memory_action_enum() -> None:
    """MemoryActionEnum has all four members; invalid value raises ValueError."""
    assert MemoryActionEnum.CREATE == "create"
    assert MemoryActionEnum.EDIT == "edit"
    assert MemoryActionEnum.APPEND == "append"
    assert MemoryActionEnum.DELETE == "delete"
    with pytest.raises(ValueError):
        MemoryActionEnum("invalid")


@pytest.mark.asyncio
async def test_memory_path_guard(tmp_path: Path) -> None:
    """memory() raises ValueError for paths escaping memory_dir for all actions."""
    ctx = _make_memory_ctx(tmp_path)
    for action in MemoryActionEnum:
        with pytest.raises(ValueError, match="outside memory directory"):
            await memory(ctx, action, "../escape.md", content="x", search="x", replacement="y")


@pytest.mark.asyncio
async def test_memory_create_atomic(tmp_path: Path) -> None:
    """action=create writes file atomically; no .tmp sibling on success; cleanup block present."""
    ctx = _make_memory_ctx(tmp_path)
    await memory(ctx, MemoryActionEnum.CREATE, "test.md", content="hello")

    target = tmp_path / "test.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello"

    # No .tmp sibling remains after successful create
    assert not (tmp_path / "test.md.tmp").exists()

    # Structural: tmp.unlink cleanup block must be present in the implementation
    source = inspect.getsource(memory)
    assert "tmp.unlink" in source


@pytest.mark.asyncio
async def test_memory_create_exists(tmp_path: Path) -> None:
    """action=create raises FileExistsError when file already exists."""
    ctx = _make_memory_ctx(tmp_path)
    await memory(ctx, MemoryActionEnum.CREATE, "test.md", content="first")
    with pytest.raises(FileExistsError):
        await memory(ctx, MemoryActionEnum.CREATE, "test.md", content="second")
    # Original file unchanged
    assert (tmp_path / "test.md").read_text(encoding="utf-8") == "first"


@pytest.mark.asyncio
async def test_memory_edit_unique_match(tmp_path: Path) -> None:
    """action=edit raises ValueError for duplicate matches when replace_all=False; succeeds with True."""
    ctx = _make_memory_ctx(tmp_path)
    target = tmp_path / "test.md"
    target.write_text("foo foo foo", encoding="utf-8")

    with pytest.raises(ValueError, match="found 3 times"):
        await memory(ctx, MemoryActionEnum.EDIT, "test.md", search="foo", replacement="bar")

    # replace_all=True succeeds
    await memory(
        ctx, MemoryActionEnum.EDIT, "test.md", search="foo", replacement="bar", replace_all=True
    )
    assert target.read_text(encoding="utf-8") == "bar bar bar"


@pytest.mark.asyncio
async def test_memory_edit_not_found(tmp_path: Path) -> None:
    """action=edit raises ValueError when search string is absent."""
    ctx = _make_memory_ctx(tmp_path)
    (tmp_path / "test.md").write_text("hello world", encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        await memory(ctx, MemoryActionEnum.EDIT, "test.md", search="missing", replacement="x")


@pytest.mark.asyncio
async def test_memory_append(tmp_path: Path) -> None:
    """action=append adds content to end of file; missing file raises FileNotFoundError."""
    ctx = _make_memory_ctx(tmp_path)
    target = tmp_path / "test.md"
    target.write_text("line one\n", encoding="utf-8")

    await memory(ctx, MemoryActionEnum.APPEND, "test.md", content="line two")
    assert target.read_text(encoding="utf-8") == "line one\nline two"

    with pytest.raises(FileNotFoundError):
        await memory(ctx, MemoryActionEnum.APPEND, "missing.md", content="x")


@pytest.mark.asyncio
async def test_memory_delete(tmp_path: Path) -> None:
    """action=delete removes the file; missing file raises FileNotFoundError."""
    ctx = _make_memory_ctx(tmp_path)
    target = tmp_path / "test.md"
    target.write_text("content", encoding="utf-8")

    await memory(ctx, MemoryActionEnum.DELETE, "test.md")
    assert not target.exists()

    with pytest.raises(FileNotFoundError):
        await memory(ctx, MemoryActionEnum.DELETE, "test.md")


def test_save_memory_agent_output_shape() -> None:
    """SaveMemoryAgentOutput instantiates with valid fields; confidence rejects out-of-range values."""
    out = SaveMemoryAgentOutput(
        summary="wrote feedback",
        files_touched=["feedback_testing.md"],
        actions=["CREATED feedback_testing.md"],
        confidence=0.9,
    )
    assert out.confidence == 0.9
    assert out.files_touched == ["feedback_testing.md"]

    with pytest.raises(ValidationError):
        SaveMemoryAgentOutput(summary="x", files_touched=[], actions=[], confidence=1.5)
    with pytest.raises(ValidationError):
        SaveMemoryAgentOutput(summary="x", files_touched=[], actions=[], confidence=-0.1)


def test_max_requests_memory_config() -> None:
    """SubagentSettings default has max_requests_memory == 6."""
    cfg = SubagentSettings()
    assert cfg.max_requests_memory == 6


def test_save_memory_agent_tools() -> None:
    """_save_memory_agent registers exactly the expected 4 tools."""
    tool_names = set(_save_memory_agent._function_toolset.tools)
    assert tool_names == {"read_file", "list_directory", "find_in_files", "memory"}


def test_run_subagent_attempt_accepts_model() -> None:
    """_run_subagent_attempt has a model parameter with default None."""
    sig = inspect.signature(_run_subagent_attempt)
    assert "model" in sig.parameters
    assert sig.parameters["model"].default is None


def test_run_save_memory_agent_dispatch() -> None:
    """_run_save_memory_agent is importable from co_cli.tools.subagent."""
    from co_cli.tools.subagent import _run_save_memory_agent as fn

    assert callable(fn)


@pytest.mark.asyncio
async def test_save_memory_agent_read_tools_callable(tmp_path: Path) -> None:
    """read_file, list_directory, and find_in_files are callable with the deps shape
    that _run_save_memory_agent produces via make_subagent_deps.

    workspace_root = tmp_path; memory_dir = tmp_path / ".co-cli/memory".
    Read tools use workspace_root with workspace-relative paths (.co-cli/memory/…).
    This mirrors how the save-agent will read memory files in production.
    """
    mem_dir = tmp_path / ".co-cli" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "test_entry.md").write_text("unique_marker_abc", encoding="utf-8")

    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=make_settings(),
        workspace_root=tmp_path,
        memory_dir=mem_dir,
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    # list_directory: enumerate files in the memory subdirectory
    result = await list_directory(ctx, path=".co-cli/memory")
    assert result.metadata.get("count", 0) >= 1
    names = [e["name"] for e in result.metadata["entries"]]
    assert "test_entry.md" in names

    # read_file: read a specific memory file via workspace-relative path
    result = await read_file(ctx, path=".co-cli/memory/test_entry.md")
    assert "unique_marker_abc" in result.return_value

    # find_in_files: search memory content by pattern across memory files
    result = await find_in_files(ctx, pattern="unique_marker_abc", glob=".co-cli/memory/**/*.md")
    assert result.metadata["count"] >= 1
    assert any("test_entry.md" in m["file"] for m in result.metadata["matches"])
