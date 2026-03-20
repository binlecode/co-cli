"""Functional tests for _check_skill_grant() eligibility gate.

Tests validate the two ineligibility conditions:
  1. Registry-level: tool registered with requires_approval=True
  2. Explicit carve-out: run_shell_command
And the eligible path for a safe read-only tool.
"""

from co_cli.context._orchestrate import _check_skill_grant
from co_cli.deps import CoDeps, CoConfig, CoServices, CoSessionState
from co_cli.tools._shell_backend import ShellBackend


# Note: tool_approvals is intentionally sparse here for assertion coverage.
# In production, CoSessionState.tool_approvals is populated by agent.py:_register()
# and contains every registered tool (40+ entries). The sparse dict is sufficient
# to test both ineligibility conditions and the eligible path.
_session = CoSessionState(
    skill_tool_grants={"run_shell_command", "save_memory", "read_file"},
    tool_approvals={"save_memory": True, "run_shell_command": False, "read_file": False},
)
_deps = CoDeps(
    services=CoServices(shell=ShellBackend()),
    config=CoConfig(),
    session=_session,
)


def test_run_shell_command_denied_via_explicit_carve_out() -> None:
    """run_shell_command must be denied even though tool_approvals has it False."""
    assert _check_skill_grant("run_shell_command", _deps) is False


def test_save_memory_denied_via_registry_approval_gate() -> None:
    """save_memory has requires_approval=True in registry → denied by condition 1."""
    assert _check_skill_grant("save_memory", _deps) is False


def test_read_file_granted_as_eligible_tool() -> None:
    """read_file is requires_approval=False and not carve-out → skill grant allowed."""
    assert _check_skill_grant("read_file", _deps) is True


def test_tool_not_in_grants_returns_false() -> None:
    """Tool absent from skill_tool_grants must return False before any eligibility check."""
    assert _check_skill_grant("write_file", _deps) is False
