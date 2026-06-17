"""Tests for the fork_deps factory."""

from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    CoDeps,
    fork_deps,
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


def test_fork_deps_does_not_share_runtime() -> None:
    """Child runtime is a fresh CoRuntimeState — mutations don't bleed into parent."""
    parent = _make_deps()
    child = fork_deps(parent)
    child.runtime.compaction_skip_count = 99
    assert parent.runtime.compaction_skip_count == 0
