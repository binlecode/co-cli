"""Helpers for deferred tool approvals and shell approval persistence."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import DeferredToolResults, ToolDenied

from co_cli._exec_approvals import add_approval, derive_pattern, find_approved, load_approvals, update_last_used
from co_cli.deps import CoDeps


def decode_tool_args(raw_args: str | dict[str, Any] | None) -> dict[str, Any]:
    """Normalize deferred-tool args into a dict for approval handling."""
    if isinstance(raw_args, str):
        decoded = json.loads(raw_args)
        return decoded if isinstance(decoded, dict) else {}
    return raw_args or {}


def format_tool_call_description(tool_name: str, args: dict[str, Any]) -> str:
    """Build the user-facing approval description for one tool call."""
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    description = f"{tool_name}({args_str})"
    remember_hint = approval_remember_hint(tool_name, args)
    if remember_hint:
        description = f"{description}\n  {remember_hint}"
    return description


def approval_remember_hint(tool_name: str, args: dict[str, Any]) -> str | None:
    """Return the prompt hint shown for an approval choice that can be remembered."""
    if tool_name != "run_shell_command":
        return None
    return f"[always -> will remember: {derive_pattern(args.get('cmd', ''))}]"


def is_session_auto_approved(tool_name: str, deps: CoDeps) -> bool:
    """Return True when the current session already auto-approves this tool."""
    return tool_name in deps.session.session_tool_approvals


def remember_tool_approval(tool_name: str, args: dict[str, Any], deps: CoDeps) -> None:
    """Persist an approval choice using the tool-specific persistence strategy."""
    if tool_name == "run_shell_command":
        add_approval(deps.config.exec_approvals_path, args.get("cmd", ""), tool_name)
        return
    deps.session.session_tool_approvals.add(tool_name)


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
    tool_name: str,
    args: dict[str, Any],
    deps: CoDeps,
    remember: bool = False,
) -> None:
    """Record one approval result and optionally persist the approval choice."""
    if approved:
        approvals.approvals[tool_call_id] = True
        if remember:
            remember_tool_approval(tool_name, args, deps)
        return
    approvals.approvals[tool_call_id] = ToolDenied("User denied this action")
