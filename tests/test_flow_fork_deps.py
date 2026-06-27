"""Tests for the fork_deps factory."""

from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    CoDeps,
    fork_deps,
)
from co_cli.display.headless import HeadlessFrontend


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


def test_fork_deps_does_not_share_runtime() -> None:
    """Child runtime is a fresh CoRuntimeState — mutations don't bleed into parent."""
    parent = _make_deps()
    child = fork_deps(parent)
    child.runtime.compaction_skip_count = 99
    assert parent.runtime.compaction_skip_count == 0


def test_reset_for_turn_clears_frontend() -> None:
    """The per-turn frontend reach-in is cleared by reset_for_turn()."""
    deps = _make_deps()
    deps.runtime.frontend = HeadlessFrontend()
    deps.runtime.reset_for_turn()
    assert deps.runtime.frontend is None


def test_fork_deps_child_does_not_inherit_frontend() -> None:
    """A delegated child never inherits the parent's frontend through runtime.

    fork_deps resets runtime, so propagation must be explicit (threaded into
    run_standalone_owned) — never silently carried on child_deps.runtime.
    """
    parent = _make_deps()
    parent.runtime.frontend = HeadlessFrontend()
    child = fork_deps(parent)
    assert child.runtime.frontend is None
