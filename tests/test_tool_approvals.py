"""Unit tests for approval-subject resolver and auto-approval helpers."""

from __future__ import annotations

from co_cli.deps import CoDeps, CoConfig, CoServices
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools._tool_approvals import (
    CommandPatternApprovalSubject,
    ToolApprovalSubject,
    is_auto_approved,
    resolve_approval_subject,
)


def test_resolve_approval_subject_direct_tool() -> None:
    """Direct tools resolve to ToolApprovalSubject keyed by tool name."""
    subject = resolve_approval_subject("save_memory", {})
    assert subject == ToolApprovalSubject(tool_name="save_memory")


def test_resolve_approval_subject_shell() -> None:
    """run_shell_command resolves to CommandPatternApprovalSubject with derived pattern."""
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git commit -m fix"})
    assert subject == CommandPatternApprovalSubject(
        tool_name="run_shell_command",
        cmd="git commit -m fix",
        pattern="git commit *",
    )


def test_is_auto_approved_tool_subject_checks_session() -> None:
    """ToolApprovalSubject is auto-approved when tool_name is in session_tool_approvals."""
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    deps.session.session_tool_approvals.add("save_memory")
    assert is_auto_approved(ToolApprovalSubject(tool_name="save_memory"), deps) is True


def test_is_auto_approved_command_pattern_returns_false() -> None:
    """CommandPatternApprovalSubject is never auto-approved from session.

    The persistent approval check already occurred inside run_shell_command
    before the deferred request was raised — returning True here would
    double-approve without the persistent check.
    """
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    subject = CommandPatternApprovalSubject(
        tool_name="run_shell_command",
        cmd="git status",
        pattern="git status *",
    )
    assert is_auto_approved(subject, deps) is False
