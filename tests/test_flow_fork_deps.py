"""Tests for fork_deps and fork_deps_for_reviewer factories."""

from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    fork_deps,
    fork_deps_for_reviewer,
)


def _make_deps() -> CoDeps:
    """Minimal CoDeps for fork tests — no live services needed."""
    from co_cli.tools.shell_backend import ShellBackend

    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
    )


def test_fork_deps_increments_agent_depth() -> None:
    """fork_deps increments agent_depth on the child runtime."""
    parent = _make_deps()
    assert parent.runtime.agent_depth == 0

    child = fork_deps(parent)
    assert child.runtime.agent_depth == 1


def test_fork_deps_for_reviewer_increments_agent_depth() -> None:
    """fork_deps_for_reviewer delegates to fork_deps — agent_depth is incremented."""
    parent = _make_deps()
    child = fork_deps_for_reviewer(parent)
    assert child.runtime.agent_depth == 1


def test_fork_deps_does_not_share_runtime() -> None:
    """Child runtime is a fresh CoRuntimeState — mutations don't bleed into parent."""
    parent = _make_deps()
    child = fork_deps(parent)
    child.runtime.compaction_skip_count = 99
    assert parent.runtime.compaction_skip_count == 0


def test_background_status_callback_not_cleared_by_reset_for_turn() -> None:
    """background_status_callback survives reset_for_turn — it's cross-turn state."""
    runtime = CoRuntimeState()

    def cb(_: str) -> None:
        return None

    runtime.background_status_callback = cb
    runtime.reset_for_turn()
    assert runtime.background_status_callback is cb
