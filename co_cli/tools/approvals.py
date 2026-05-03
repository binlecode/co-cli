"""Helpers for deferred tool approvals — unified session-scoped model.

All approval subjects resolve to a single ApprovalSubject dataclass.
'a' (always) stores a SessionApprovalRule in deps.session.session_approval_rules.
No cross-session persistence — approval rules are cleared when the session ends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic_ai import ApprovalRequired, DeferredToolResults, ToolDenied

from co_cli.deps import ApprovalKindEnum, ApprovalSubject, CoDeps, SessionApprovalRule, ToolInfo


class QuestionRequired(ApprovalRequired):
    """Raised by clarify to pause execution for user-input questions.

    Subclasses ApprovalRequired so pydantic-ai's deferred tool mechanism handles it.
    The orchestrator discriminates this variant by checking for "questions" in metadata
    (present only on QuestionRequired, not on plain ApprovalRequired).
    """

    def __init__(self, *, questions: list[dict]) -> None:
        super().__init__(metadata={"questions": questions})
        self.questions = questions


def _build_file_write_preview(content: str | None) -> str | None:
    """Build a preview string for file_write content.

    Returns None when content is missing, non-string, or empty.
    Caps at 30 lines and 4000 chars; appends '... (+N more lines)' when truncated.
    """
    if not isinstance(content, str) or not content:
        return None
    content_lines = content.split("\n")
    total_lines = len(content_lines)
    preview_lines = content_lines[:30]
    preview_text = "\n".join(preview_lines)
    char_capped = len(preview_text) > 4000
    if char_capped:
        preview_text = preview_text[:4000]
    line_capped = total_lines > 30
    if char_capped or line_capped:
        extra = total_lines - len(preview_lines)
        if extra > 0:
            preview_text += f"\n... (+{extra} more lines)"
        else:
            preview_text += "\n... (truncated)"
    return preview_text


def resolve_approval_subject(
    tool_name: str,
    args: dict[str, Any],
    tool_info: ToolInfo | None = None,
) -> ApprovalSubject:
    """Map a deferred tool call to its approval subject.

    Resolution order:
      tool_info.approval_subject_fn → registered per-tool resolver (highest priority)
      shell → shell subject (utility = first token)
      file_write / file_patch → path subject (parent directory)
      web_fetch → domain subject (parsed hostname)
      everything else → tool subject (can_remember=True)
    """
    if tool_info is not None and tool_info.approval_subject_fn is not None:
        return tool_info.approval_subject_fn(args)

    # Shell branch: scope to the utility (first token of cmd) so "always" approval
    # covers all future invocations of the same utility, not just the exact command.
    if tool_name == "shell":
        cmd = args.get("cmd", "")
        tokens = cmd.split()
        utility = tokens[0] if tokens else cmd
        hint = f"(allow all {utility} commands this session?)" if utility else ""
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.SHELL,
            value=utility,
            display=f"shell(cmd={cmd!r})\n  {hint}" if hint else f"shell(cmd={cmd!r})",
            can_remember=bool(utility),
        )

    # File-path branch: scope to the parent directory so "always" approval covers
    # all writes/edits within the same directory.  Both file_write and file_patch
    # resolve to the same bare parent_dir value so cross-tool approval is intentional.
    if tool_name in ("file_write", "file_patch"):
        path = args.get("path", "")
        parent = str(Path(path).parent) if path else ""
        hint = f"(allow all writes to {parent}/ this session?)" if parent else ""

        if tool_name == "file_write":
            content = args.get("content", "")
            byte_count = len(content.encode()) if isinstance(content, str) else 0
            lines = [f"file_write(path={path!r}, {byte_count} bytes)"]
            preview = _build_file_write_preview(content)
        else:
            old_string = args.get("old_string", "")
            new_string = args.get("new_string", "")
            replace_all = args.get("replace_all", False)
            old_snip = (old_string[:400] + "…") if len(old_string) > 400 else old_string
            new_snip = (new_string[:400] + "…") if len(new_string) > 400 else new_string
            lines = [
                f"file_patch(path={path!r})",
                f"  old_string:  {old_snip!r}",
                f"  new_string:  {new_snip!r}",
                f"  replace_all: {replace_all}",
            ]
            preview = None

        if hint:
            lines.append(f"  {hint}")
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.PATH,
            value=parent,
            display="\n".join(lines),
            can_remember=bool(parent),
            preview=preview,
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
