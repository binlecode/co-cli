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

from co_cli.deps import ApprovalKindEnum, CoDeps, SessionApprovalRule


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
    kind: ApprovalKindEnum
    value: str
    display: str
    can_remember: bool


def resolve_approval_subject(
    tool_name: str,
    args: dict[str, Any],
) -> ApprovalSubject:
    """Map a deferred tool call to its approval subject.

    Resolution order:
      run_shell_command → shell subject (utility = first token)
      write_file / edit_file → path subject (parent directory)
      web_fetch → domain subject (parsed hostname)
      everything else → tool subject (can_remember=True)
    """
    # Shell branch: scope to the utility (first token of cmd) so "always" approval
    # covers all future invocations of the same utility, not just the exact command.
    if tool_name == "run_shell_command":
        cmd = args.get("cmd", "")
        tokens = cmd.split()
        utility = tokens[0] if tokens else cmd
        hint = f"(allow all {utility} commands this session?)" if utility else ""
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.SHELL,
            value=utility,
            display=f"run_shell_command(cmd={cmd!r})\n  {hint}" if hint else f"run_shell_command(cmd={cmd!r})",
            can_remember=bool(utility),
        )

    # File-path branch: scope to the parent directory so "always" approval covers
    # all writes/edits within the same directory.  Both write_file and edit_file
    # resolve to the same bare parent_dir value so cross-tool approval is intentional.
    if tool_name in ("write_file", "edit_file"):
        path = args.get("path", "")
        parent = str(Path(path).parent) if path else ""
        hint = f"(allow all writes to {parent}/ this session?)" if parent else ""

        if tool_name == "write_file":
            content = args.get("content", "")
            byte_count = len(content.encode()) if isinstance(content, str) else 0
            lines = [f"write_file(path={path!r}, {byte_count} bytes)"]
        else:
            search = args.get("search", "")
            replacement = args.get("replacement", "")
            replace_all = args.get("replace_all", False)
            search_snip = (search[:60] + "…") if len(search) > 60 else search
            repl_snip = (replacement[:60] + "…") if len(replacement) > 60 else replacement
            lines = [
                f"edit_file(path={path!r})",
                f"  search:      {search_snip!r}",
                f"  replacement: {repl_snip!r}",
                f"  replace_all: {replace_all}",
            ]

        if hint:
            lines.append(f"  {hint}")
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.PATH,
            value=parent,
            display="\n".join(lines),
            can_remember=bool(parent),
        )

    # Web-domain branch: scope to the hostname so "always" approval covers all
    # fetches to the same domain regardless of path or query string.
    if tool_name == "web_fetch":
        url = args.get("url", "")
        domain = urlparse(url).hostname or ""
        hint = f"(allow all fetches to {domain} this session?)" if domain else ""
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.DOMAIN,
            value=domain,
            display=f"web_fetch(url={url!r})\n  {hint}" if hint else f"web_fetch(url={url!r})",
            can_remember=bool(domain),
        )

    # Generic-tool fallback: scope to the tool name so "always" approval covers
    # all future invocations of the same tool, including MCP tools.
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    hint = f"(always allow {tool_name} this session?)" if tool_name else ""
    return ApprovalSubject(
        tool_name=tool_name,
        kind=ApprovalKindEnum.TOOL,
        value=tool_name,
        display=f"{tool_name}({args_str})\n  {hint}" if hint else f"{tool_name}({args_str})",
        can_remember=bool(tool_name),
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
    The stored value is always the resolved scope key produced by
    resolve_approval_subject() (e.g. the utility name, parent dir, domain) —
    never the full command string or path.
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
