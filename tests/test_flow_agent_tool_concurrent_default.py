"""Behavioral tests for reviewer semaphore sharing."""

from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    fork_deps_for_reviewer,
)
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )


def test_semaphore_shared_across_reviewer_fork():
    deps = _make_deps()
    reviewer = fork_deps_for_reviewer(deps)
    assert reviewer.tool_dispatch_sem is deps.tool_dispatch_sem
