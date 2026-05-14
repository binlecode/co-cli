"""Tests for fork_deps, fork_deps_for_reviewer, and fork_deps_for_curator factories."""

from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    fork_deps,
    fork_deps_for_curator,
    fork_deps_for_reviewer,
)


def _make_deps() -> CoDeps:
    """Minimal CoDeps for fork tests — no live services needed."""
    from co_cli.tools.shell_backend import ShellBackend

    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
    )


def test_fork_deps_resets_approval_flags() -> None:
    """Plain fork_deps always resets approval flags to False."""
    parent = _make_deps()
    parent.runtime.auto_approve_skill_ops = True
    parent.runtime.auto_approve_knowledge_ops = True

    child = fork_deps(parent)
    assert child.runtime.auto_approve_skill_ops is False
    assert child.runtime.auto_approve_knowledge_ops is False


def test_fork_deps_for_reviewer_sets_both_flags() -> None:
    """fork_deps_for_reviewer grants both skill and knowledge write access."""
    parent = _make_deps()
    child = fork_deps_for_reviewer(parent)
    assert child.runtime.auto_approve_skill_ops is True
    assert child.runtime.auto_approve_knowledge_ops is True


def test_fork_deps_for_curator_sets_only_skill_flag() -> None:
    """fork_deps_for_curator grants skill write access only; knowledge stays gated."""
    parent = _make_deps()
    child = fork_deps_for_curator(parent)
    assert child.runtime.auto_approve_skill_ops is True
    assert child.runtime.auto_approve_knowledge_ops is False


def test_fork_deps_increments_agent_depth() -> None:
    """fork_deps increments agent_depth by 1."""
    parent = _make_deps()
    assert parent.runtime.agent_depth == 0
    child = fork_deps(parent)
    assert child.runtime.agent_depth == 1
    grandchild = fork_deps(child)
    assert grandchild.runtime.agent_depth == 2


def test_fork_deps_does_not_share_runtime() -> None:
    """Child runtime is a fresh CoRuntimeState — mutations don't bleed into parent."""
    parent = _make_deps()
    child = fork_deps(parent)
    child.runtime.auto_approve_skill_ops = True
    assert parent.runtime.auto_approve_skill_ops is False


def test_background_status_callback_not_cleared_by_reset_for_turn() -> None:
    """background_status_callback survives reset_for_turn — it's cross-turn state."""
    runtime = CoRuntimeState()

    def cb(_: str) -> None:
        return None

    runtime.background_status_callback = cb
    runtime.reset_for_turn()
    assert runtime.background_status_callback is cb
