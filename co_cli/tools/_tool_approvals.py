"""Helpers for deferred tool approvals — unified session-scoped model.

All approval subjects resolve to a single ApprovalSubject dataclass.
'a' (always) stores a SessionApprovalRule in deps.session.session_approval_rules.
No cross-session persistence — approval rules are cleared when the session ends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic_ai import DeferredToolResults, ToolDenied

from co_cli.deps import CoDeps, SessionApprovalRule


@dataclass(frozen=True)
class ApprovalSubject:
    """Resolved representation of what is being approved.

    tool_name:   the registered tool name (e.g. "run_shell_command")
    kind:        category matching SessionApprovalRule.kind
    value:       the scoped key used for session rule matching
    display:     human-readable description shown in the approval prompt
    can_remember: whether 'a' should store a session rule
    """

    tool_name: str
    kind: str
    value: str
    display: str
    can_remember: bool


def resolve_approval_subject(
    tool_name: str,
    args: dict[str, Any],
    *,
    mcp_prefixes: frozenset[str] = frozenset(),
) -> ApprovalSubject:
    """Map a deferred tool call to its approval subject.

    Resolution order:
      run_shell_command → shell subject (utility = first token)
      write_file / edit_file → path subject (parent directory)
      web_fetch → domain subject (parsed hostname)
      MCP tool (prefix match) → mcp_tool subject
      everything else → generic tool subject (can_remember=False)
    """
    # Shell branch: scope to the utility (first token of cmd) so "always" approval
    # covers all future invocations of the same utility, not just the exact command.
    if tool_name == "run_shell_command":
        cmd = args.get("cmd", "")
        tokens = cmd.split()
        utility = tokens[0] if tokens else cmd
        hint = f"[always → session: {utility} *]" if utility else ""
        return ApprovalSubject(
            tool_name=tool_name,
            kind="shell",
            value=utility,
            display=f"run_shell_command(cmd={cmd!r})\n  {hint}" if hint else f"run_shell_command(cmd={cmd!r})",
            can_remember=bool(utility),
        )

    # File-path branch: scope to the parent directory so "always" approval covers
    # all writes/edits within the same directory.  Keyed as {tool}:{parent_dir} to
    # prevent write_file and edit_file rules from cross-approving each other.
    if tool_name in ("write_file", "edit_file"):
        path = args.get("path", "")
        parent = str(Path(path).parent) if path else ""
        # scope by tool_name so write_file and edit_file don't cross-approve
        value = f"{tool_name}:{parent}" if parent else ""
        hint = f"[always → session: {parent}/**]" if parent else ""
        return ApprovalSubject(
            tool_name=tool_name,
            kind="path",
            value=value,
            display=f"{tool_name}(path={path!r})\n  {hint}" if hint else f"{tool_name}(path={path!r})",
            can_remember=bool(parent),
        )

    # Web-domain branch: scope to the hostname so "always" approval covers all
    # fetches to the same domain regardless of path or query string.
    if tool_name == "web_fetch":
        url = args.get("url", "")
        domain = urlparse(url).hostname or ""
        hint = f"[always → session: {domain}]" if domain else ""
        return ApprovalSubject(
            tool_name=tool_name,
            kind="domain",
            value=domain,
            display=f"web_fetch(url={url!r})\n  {hint}" if hint else f"web_fetch(url={url!r})",
            can_remember=bool(domain),
        )

    # MCP-tool branch: match by server-name prefix (longest prefix wins).  Value is
    # "{server}:{tool}" so "always" approval is scoped to one tool on one server.
    for prefix in sorted(mcp_prefixes, key=len, reverse=True):
        if tool_name.startswith(f"{prefix}_"):
            mcp_tool_name = tool_name[len(prefix) + 1:]
            if not mcp_tool_name:
                continue
            value = f"{prefix}:{mcp_tool_name}"
            return ApprovalSubject(
                tool_name=tool_name,
                kind="mcp_tool",
                value=value,
                display=f"{tool_name}(...)\n  [always → session: {value}]",
                can_remember=True,
            )

    # Generic-tool fallback: no rememberable scope can be derived, so "always" is
    # unavailable.  The user must approve each invocation individually.
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return ApprovalSubject(
        tool_name=tool_name,
        kind="tool",
        value=tool_name,
        display=f"{tool_name}({args_str})",
        can_remember=False,
    )


def decode_tool_args(raw_args: str | dict[str, Any] | None) -> dict[str, Any]:
    """Normalize deferred-tool args into a dict for approval handling."""
    if isinstance(raw_args, str):
        try:
            decoded = json.loads(raw_args)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return raw_args or {}


def is_auto_approved(subject: ApprovalSubject, deps: CoDeps) -> bool:
    """Return True when this subject matches a remembered session approval rule.

    Approval matching is exact: kind + value must both match the stored rule.
    There is no wildcard expansion at match time — wildcards are a display
    hint only.  The stored value is always the resolved scope key produced
    by resolve_approval_subject() (e.g. the utility name, parent dir, domain).
    """
    if not subject.can_remember:
        return False
    rule = SessionApprovalRule(kind=subject.kind, value=subject.value)
    return rule in deps.session.session_approval_rules


def remember_tool_approval(subject: ApprovalSubject, deps: CoDeps) -> None:
    """Store a session approval rule for this subject if rememberable."""
    if not subject.can_remember:
        return
    rule = SessionApprovalRule(kind=subject.kind, value=subject.value)
    if rule not in deps.session.session_approval_rules:
        deps.session.session_approval_rules.append(rule)


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
