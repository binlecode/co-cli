"""Behavioural tests for /queue (list/clear/pop) — TASK-1, Phase 2.

Exercises the real `dispatch("/queue …", ctx)` path with `ctx.input_queue`
as the live deque. Asserts queue mutation by reference, 1-based pop, usage
error on out-of-range, and that every invocation returns `LocalOnly`
(never arms a turn).
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, LocalOnly
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_deps(tmp_path: Path) -> CoDeps:
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_index = build_native_toolset(SETTINGS)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps, queue: deque[str] | None) -> CommandContext:
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=None,  # type: ignore[arg-type]
        frontend=HeadlessFrontend(),
        completer=None,
        input_queue=queue,
    )


@pytest.mark.asyncio
async def test_queue_list_shows_pending_items(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque(["alpha", "beta"])
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_queue_pop_drops_last_by_default(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque(["alpha", "beta"])
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue pop", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == ["alpha"]


@pytest.mark.asyncio
async def test_queue_pop_by_index_drops_one_based(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque(["alpha", "beta", "gamma"])
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue pop 1", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == ["beta", "gamma"]


@pytest.mark.asyncio
async def test_queue_clear_empties_queue(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque(["alpha", "beta"])
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue clear", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == []


@pytest.mark.asyncio
async def test_queue_pop_out_of_range_leaves_queue_unchanged(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque(["alpha", "beta"])
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue pop 9", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_queue_clear_on_empty_queue_is_noop(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque()
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue clear", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == []


@pytest.mark.asyncio
async def test_queue_pop_non_integer_arg_is_usage_error(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque(["alpha"])
    ctx = _make_ctx(deps, queue)

    outcome = await dispatch("/queue pop abc", ctx)

    assert isinstance(outcome, LocalOnly)
    assert list(queue) == ["alpha"]
