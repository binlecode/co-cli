"""Helpers for deferred tool approvals and shell approval persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai import DeferredToolResults, ToolDenied

from co_cli.tools._exec_approvals import add_approval, derive_pattern, find_approved, load_approvals, update_last_used
from co_cli.deps import CoDeps


# ---------------------------------------------------------------------------
# Approval subjects — explicit representation of what is being approved
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolApprovalSubject:
    """Direct tool approval — session-scoped by tool name."""

    tool_name: str


@dataclass(frozen=True)
class CommandPatternApprovalSubject:
    """Shell meta-tool approval — persistent, command-pattern-based.

    run_shell_command is a meta-tool: the approval subject is not the tool
    name but a derived command pattern extracted from the payload.
    """

    tool_name: str
    cmd: str
    pattern: str


ApprovalSubject = ToolApprovalSubject | CommandPatternApprovalSubject


def resolve_approval_subject(tool_name: str, args: dict[str, Any]) -> ApprovalSubject:
    """Map a deferred tool call to its approval subject.

    run_shell_command is a meta-tool — its approval subject is a derived
    command pattern, not the tool name. All other tools use the tool name
    as the subject directly.
    """
    if tool_name == "run_shell_command":
        cmd = args.get("cmd", "")
        return CommandPatternApprovalSubject(tool_name=tool_name, cmd=cmd, pattern=derive_pattern(cmd))
    return ToolApprovalSubject(tool_name=tool_name)


# ---------------------------------------------------------------------------
# Approval helpers — all operate on ApprovalSubject, not raw tool names
# ---------------------------------------------------------------------------


def decode_tool_args(raw_args: str | dict[str, Any] | None) -> dict[str, Any]:
    """Normalize deferred-tool args into a dict for approval handling."""
    if isinstance(raw_args, str):
        try:
            decoded = json.loads(raw_args)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return raw_args or {}


def format_tool_call_description(subject: ApprovalSubject, args: dict[str, Any]) -> str:
    """Build the user-facing approval description for one tool call."""
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    description = f"{subject.tool_name}({args_str})"
    remember_hint = approval_remember_hint(subject)
    if remember_hint:
        description = f"{description}\n  {remember_hint}"
    return description


def approval_remember_hint(subject: ApprovalSubject) -> str | None:
    """Return the prompt hint shown for an approval choice that can be remembered."""
    if isinstance(subject, CommandPatternApprovalSubject):
        return f"[always -> will remember: {subject.pattern}]"
    return None


def is_auto_approved(subject: ApprovalSubject, deps: CoDeps) -> bool:
    """Return True when this approval subject is already auto-approved.

    ToolApprovalSubject: checks the session approval set.
    CommandPatternApprovalSubject: always False — the persistent approval
    check already occurred inside run_shell_command before the deferred
    request was raised.
    """
    if isinstance(subject, ToolApprovalSubject):
        return subject.tool_name in deps.session.session_tool_approvals
    return False


def remember_tool_approval(subject: ApprovalSubject, deps: CoDeps) -> None:
    """Persist an approval choice using the subject-specific persistence strategy."""
    if isinstance(subject, CommandPatternApprovalSubject):
        add_approval(deps.config.exec_approvals_path, subject.cmd, subject.tool_name)
        return
    deps.session.session_tool_approvals.add(subject.tool_name)


def is_shell_command_persistently_approved(cmd: str, deps: CoDeps) -> bool:
    """Return True when cmd matches a remembered shell approval pattern."""
    entries = load_approvals(deps.config.exec_approvals_path)
    found = find_approved(cmd, entries)
    if found is None:
        return False
    update_last_used(deps.config.exec_approvals_path, found["id"])
    return True


def record_approval_choice(
    approvals: DeferredToolResults,
    *,
    tool_call_id: str,
    approved: bool,
    subject: ApprovalSubject,
    deps: CoDeps,
    remember: bool = False,
) -> None:
    """Record one approval result and optionally persist the approval choice."""
    if approved:
        approvals.approvals[tool_call_id] = True
        if remember:
            remember_tool_approval(subject, deps)
        return
    approvals.approvals[tool_call_id] = ToolDenied("User denied this action")
