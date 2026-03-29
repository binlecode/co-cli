"""Functional tests for the unified approval subject resolver and session rule helpers."""

from __future__ import annotations

from pydantic_ai import DeferredToolResults

from co_cli.deps import CoDeps, CoConfig, CoServices, SessionApprovalRule
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools._tool_approvals import (
    is_auto_approved,
    record_approval_choice,
    remember_tool_approval,
    resolve_approval_subject,
)


def _deps() -> CoDeps:
    return CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())


# ---------------------------------------------------------------------------
# Boundary conditions — incomplete model args must not produce malformed rules
# ---------------------------------------------------------------------------


def test_shell_empty_cmd_is_not_rememberable() -> None:
    """Empty cmd must not store a malformed shell rule."""
    deps = _deps()
    s = resolve_approval_subject("run_shell_command", {"cmd": ""})
    assert s.can_remember is False
    remember_tool_approval(s, deps)
    assert deps.session.session_approval_rules == []


def test_file_missing_path_is_not_rememberable() -> None:
    """write_file with no path must not store a malformed path rule."""
    deps = _deps()
    s = resolve_approval_subject("write_file", {})
    assert s.can_remember is False
    remember_tool_approval(s, deps)
    assert deps.session.session_approval_rules == []


def test_web_fetch_bad_url_is_not_rememberable() -> None:
    """web_fetch with an unparseable URL must not store a malformed domain rule."""
    deps = _deps()
    s = resolve_approval_subject("web_fetch", {"url": "not-a-url"})
    assert s.can_remember is False
    remember_tool_approval(s, deps)
    assert deps.session.session_approval_rules == []


def test_generic_tool_empty_name_is_not_rememberable() -> None:
    """Empty tool_name must not store a malformed tool rule."""
    deps = _deps()
    s = resolve_approval_subject("", {})
    assert s.can_remember is False
    remember_tool_approval(s, deps)
    assert deps.session.session_approval_rules == []


# ---------------------------------------------------------------------------
# MCP routing correctness
# ---------------------------------------------------------------------------


def test_mcp_tool_falls_through_to_generic() -> None:
    """MCP tool names fall through to the generic tool fallback (kind=tool, can_remember=True)."""
    s = resolve_approval_subject("github_foo_bar", {})
    assert s.kind == "tool"
    assert s.can_remember is True
    assert s.value == "github_foo_bar"


def test_resolve_mcp_prefix_only_no_trailing_name() -> None:
    """tool_name equal to 'github_' falls through to generic subject with can_remember=True."""
    s = resolve_approval_subject("github_", {})
    assert s.kind == "tool"
    assert s.can_remember is True


# ---------------------------------------------------------------------------
# Session approval — core behavior
# ---------------------------------------------------------------------------


def test_is_auto_approved_false_when_no_rules() -> None:
    """No rules → never auto-approved, even for rememberable subjects."""
    deps = _deps()
    s = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    assert is_auto_approved(s, deps) is False


def test_shell_session_approval() -> None:
    """'a' for a shell command stores a utility-level rule; same utility auto-approves."""
    deps = _deps()
    s = resolve_approval_subject("run_shell_command", {"cmd": "git commit -m fix"})
    remember_tool_approval(s, deps)

    # same utility, different command → auto-approved
    s2 = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    assert is_auto_approved(s2, deps) is True

    # different utility → not approved
    s3 = resolve_approval_subject("run_shell_command", {"cmd": "npm install"})
    assert is_auto_approved(s3, deps) is False


def test_path_session_approval() -> None:
    """'a' for a file write stores a parent-directory rule; same dir auto-approves, other dir does not."""
    deps = _deps()
    s = resolve_approval_subject("write_file", {"path": "/proj/src/foo.py"})
    remember_tool_approval(s, deps)

    # same directory, different file
    s2 = resolve_approval_subject("write_file", {"path": "/proj/src/bar.py"})
    assert is_auto_approved(s2, deps) is True
    assert s.value == "/proj/src"

    # different directory
    s3 = resolve_approval_subject("write_file", {"path": "/other/foo.py"})
    assert is_auto_approved(s3, deps) is False


def test_path_cross_tool_approval() -> None:
    """write_file approval in a directory also auto-approves edit_file in the same directory."""
    deps = _deps()
    s = resolve_approval_subject("write_file", {"path": "/proj/src/a.py"})
    remember_tool_approval(s, deps)

    # edit_file in the same directory → auto-approved (cross-tool approval is intentional)
    s2 = resolve_approval_subject("edit_file", {"path": "/proj/src/b.py"})
    assert is_auto_approved(s2, deps) is True

    # edit_file in a different directory → not approved
    s3 = resolve_approval_subject("edit_file", {"path": "/other/b.py"})
    assert is_auto_approved(s3, deps) is False


def test_domain_session_approval() -> None:
    """'a' for web_fetch stores a domain rule; same domain auto-approves, other domain does not."""
    deps = _deps()
    s = resolve_approval_subject("web_fetch", {"url": "https://x.com/page"})
    remember_tool_approval(s, deps)

    s2 = resolve_approval_subject("web_fetch", {"url": "https://x.com/other"})
    assert is_auto_approved(s2, deps) is True

    s3 = resolve_approval_subject("web_fetch", {"url": "https://y.com/page"})
    assert is_auto_approved(s3, deps) is False


def test_mcp_tool_session_approval() -> None:
    """MCP tools fall through to generic; 'a' stores a tool-scoped rule; different tool is not approved."""
    deps = _deps()
    s = resolve_approval_subject("github_list_issues", {})
    assert s.kind == "tool"
    assert s.value == "github_list_issues"
    assert s.can_remember is True

    remember_tool_approval(s, deps)
    assert len(deps.session.session_approval_rules) == 1

    # same tool name → auto-approved
    s2 = resolve_approval_subject("github_list_issues", {})
    assert is_auto_approved(s2, deps) is True

    # different tool name → not approved
    s3 = resolve_approval_subject("github_create_pr", {})
    assert is_auto_approved(s3, deps) is False


def test_generic_tool_session_approval() -> None:
    """'a' for a generic tool stores a tool-scoped rule; same tool auto-approves, different tool does not."""
    deps = _deps()
    s = resolve_approval_subject("save_memory", {"content": "hi"})
    remember_tool_approval(s, deps)

    # same tool → auto-approved
    s2 = resolve_approval_subject("save_memory", {"content": "other"})
    assert is_auto_approved(s2, deps) is True

    # different tool → not approved
    s3 = resolve_approval_subject("write_file", {"path": "/proj/src/foo.py"})
    assert is_auto_approved(s3, deps) is False


def test_remember_tool_approval_deduplicates() -> None:
    """Calling remember twice for the same subject does not add duplicate rules."""
    deps = _deps()
    s = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    remember_tool_approval(s, deps)
    remember_tool_approval(s, deps)
    assert len(deps.session.session_approval_rules) == 1


def test_deny_does_not_store_session_rule() -> None:
    """Denying a rememberable subject never stores a session rule."""
    deps = _deps()
    s = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    approvals = DeferredToolResults()
    record_approval_choice(
        approvals,
        tool_call_id="t1",
        approved=False,
        subject=s,
        deps=deps,
        remember=False,
    )
    assert deps.session.session_approval_rules == []
