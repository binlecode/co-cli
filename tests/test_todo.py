"""Functional tests for session-scoped todo tools (write_todos, read_todos)."""

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.todo import read_todos, write_todos

_AGENT = build_agent(config=settings)


def _make_ctx() -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        session=CoSessionState(),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# --- write_todos ---


def test_write_todos_stores_and_returns_counts():
    """write_todos stores items in session and returns correct counts."""
    ctx = _make_ctx()
    result = write_todos(
        ctx,
        [
            {"content": "Step 1", "status": "pending"},
            {"content": "Step 2", "status": "in_progress", "priority": "high"},
            {"content": "Step 3", "status": "completed"},
        ],
    )

    assert result.metadata["count"] == 3
    assert result.metadata["pending"] == 1
    assert result.metadata["in_progress"] == 1
    assert len(ctx.deps.session.session_todos) == 3


def test_write_todos_replaces_previous_list():
    """write_todos replaces the entire list — not additive."""
    ctx = _make_ctx()
    write_todos(ctx, [{"content": "First", "status": "pending"}])
    assert len(ctx.deps.session.session_todos) == 1

    write_todos(ctx, [{"content": "Replaced", "status": "completed"}])
    assert len(ctx.deps.session.session_todos) == 1
    assert ctx.deps.session.session_todos[0]["content"] == "Replaced"


def test_write_todos_defaults_priority_to_medium():
    """Items without explicit priority default to medium."""
    ctx = _make_ctx()
    write_todos(ctx, [{"content": "No priority", "status": "pending"}])

    assert ctx.deps.session.session_todos[0]["priority"] == "medium"


def test_write_todos_rejects_invalid_status():
    """Invalid status produces validation error, list NOT saved."""
    ctx = _make_ctx()
    result = write_todos(ctx, [{"content": "Bad", "status": "bogus"}])

    assert result.metadata["count"] == 0
    assert "errors" in result.metadata
    assert ctx.deps.session.session_todos == []


def test_write_todos_rejects_invalid_priority():
    """Invalid priority produces validation error."""
    ctx = _make_ctx()
    result = write_todos(ctx, [{"content": "Bad", "status": "pending", "priority": "urgent"}])

    assert result.metadata["count"] == 0
    assert "errors" in result.metadata


def test_write_todos_rejects_empty_content():
    """Empty content string produces validation error."""
    ctx = _make_ctx()
    result = write_todos(ctx, [{"content": "", "status": "pending"}])

    assert result.metadata["count"] == 0
    assert "errors" in result.metadata


def test_write_todos_rejects_non_dict_item():
    """Non-dict items produce validation error."""
    ctx = _make_ctx()
    result = write_todos(ctx, ["not a dict"])

    assert result.metadata["count"] == 0
    assert "errors" in result.metadata


# --- read_todos ---


def test_read_todos_empty_session():
    """read_todos on empty session returns zero count."""
    ctx = _make_ctx()
    result = read_todos(ctx)

    assert result.metadata["count"] == 0
    assert result.metadata["todos"] == []


def test_read_todos_reflects_written_state():
    """read_todos returns what write_todos stored."""
    ctx = _make_ctx()
    write_todos(
        ctx,
        [
            {"content": "Task A", "status": "pending", "priority": "high"},
            {"content": "Task B", "status": "completed"},
        ],
    )

    result = read_todos(ctx)

    assert result.metadata["count"] == 2
    assert result.metadata["pending"] == 1
    assert result.metadata["in_progress"] == 0
    assert len(result.metadata["todos"]) == 2
    assert result.metadata["todos"][0]["content"] == "Task A"


def test_read_todos_all_complete_message():
    """read_todos display says 'All items completed' when none pending."""
    ctx = _make_ctx()
    write_todos(ctx, [{"content": "Done", "status": "completed"}])

    result = read_todos(ctx)

    assert "All items completed" in result.return_value
