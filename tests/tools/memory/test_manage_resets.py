"""Unit tests: memory_manage create/append/replace reset turns_since_memory_review to 0.

No LLM. No index_store. Real filesystem writes via real service layer.
Verifies that each mutating action resets the session counter to 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.service import save_memory_item
from co_cli.tools.memory.manage import memory_manage
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path: Path, initial_turns: int = 7) -> CoDeps:
    session = CoSessionState()
    session.turns_since_memory_review = initial_turns
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=session,
        memory_dir=memory_dir,
        index_store=None,
        memory_store=None,
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _is_error(result) -> bool:
    return result.metadata is not None and result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_resets_turns_since_memory_review(tmp_path: Path) -> None:
    """memory_manage(action='create') resets turns_since_memory_review to 0 on success."""
    deps = _make_deps(tmp_path, initial_turns=7)
    ctx = _make_ctx(deps)

    result = await memory_manage(
        ctx,
        action="create",
        name="Test Note",
        content="Some content for the test note.",
        kind="note",
    )

    assert not _is_error(result), f"Expected success, got error: {result}"
    assert deps.session.turns_since_memory_review == 0


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_resets_turns_since_memory_review(tmp_path: Path) -> None:
    """memory_manage(action='append') resets turns_since_memory_review to 0 on success."""
    deps = _make_deps(tmp_path, initial_turns=5)
    memory_dir = deps.memory_dir

    saved = save_memory_item(
        memory_dir,
        content="Original content.",
        memory_kind="note",
        title="Append Target",
    )
    ctx = _make_ctx(deps)

    result = await memory_manage(
        ctx,
        action="append",
        name=saved.filename_stem,
        content="Appended content.",
    )

    assert not _is_error(result), f"Expected success, got error: {result}"
    assert deps.session.turns_since_memory_review == 0


# ---------------------------------------------------------------------------
# replace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_resets_turns_since_memory_review(tmp_path: Path) -> None:
    """memory_manage(action='replace') resets turns_since_memory_review to 0 on success."""
    deps = _make_deps(tmp_path, initial_turns=3)
    memory_dir = deps.memory_dir

    saved = save_memory_item(
        memory_dir,
        content="Old content here.",
        memory_kind="note",
        title="Replace Target",
    )
    ctx = _make_ctx(deps)

    result = await memory_manage(
        ctx,
        action="replace",
        name=saved.filename_stem,
        content="New content here.",
        section="Old content here.",
    )

    assert not _is_error(result), f"Expected success, got error: {result}"
    assert deps.session.turns_since_memory_review == 0


# ---------------------------------------------------------------------------
# delete does NOT reset (not in spec)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_does_not_reset_turns_since_memory_review(tmp_path: Path) -> None:
    """memory_manage(action='delete') does not reset turns_since_memory_review."""
    deps = _make_deps(tmp_path, initial_turns=4)
    memory_dir = deps.memory_dir

    saved = save_memory_item(
        memory_dir,
        content="To be deleted.",
        memory_kind="note",
        title="Delete Target",
    )
    ctx = _make_ctx(deps)

    result = await memory_manage(ctx, action="delete", name=saved.filename_stem)

    assert not _is_error(result), f"Expected success, got error: {result}"
    # delete does not reset the counter
    assert deps.session.turns_since_memory_review == 4
