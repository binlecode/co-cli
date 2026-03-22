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


# ---------------------------------------------------------------------------
# MCP routing correctness
# ---------------------------------------------------------------------------


def test_resolve_mcp_prefix_longest_match_wins() -> None:
    """Longest prefix wins when multiple prefixes could match."""
    s = resolve_approval_subject(
        "github_foo_bar", {}, mcp_prefixes=frozenset(["github", "github_foo"])
    )
    assert s.value == "github_foo:bar"


def test_resolve_mcp_prefix_only_no_trailing_name() -> None:
    """tool_name equal to 'prefix_' (no trailing name) falls through to generic subject."""
    s = resolve_approval_subject("github_", {}, mcp_prefixes=frozenset(["github"]))
    assert s.kind == "tool"
    assert s.can_remember is False


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

    # different directory
    s3 = resolve_approval_subject("write_file", {"path": "/other/foo.py"})
    assert is_auto_approved(s3, deps) is False


def test_path_no_cross_tool_leakage() -> None:
    """write_file approval does not auto-approve edit_file in the same directory."""
    deps = _deps()
    s = resolve_approval_subject("write_file", {"path": "/proj/src/foo.py"})
    remember_tool_approval(s, deps)

    s2 = resolve_approval_subject("edit_file", {"path": "/proj/src/foo.py"})
    assert is_auto_approved(s2, deps) is False


def test_domain_session_approval() -> None:
    """'a' for web_fetch stores a domain rule; same domain auto-approves, other domain does not."""
    deps = _deps()
    s = resolve_approval_subject("web_fetch", {"url": "https://x.com/page"})
    remember_tool_approval(s, deps)

    s2 = resolve_approval_subject("web_fetch", {"url": "https://x.com/other"})
    assert is_auto_approved(s2, deps) is True

    s3 = resolve_approval_subject("web_fetch", {"url": "https://y.com/page"})
    assert is_auto_approved(s3, deps) is False


def test_mcp_session_approval() -> None:
    """'a' for an MCP tool stores an exact server:tool rule; different tool on same server is not approved."""
    deps = _deps()
    s = resolve_approval_subject("gh_list_issues", {}, mcp_prefixes=frozenset(["gh"]))
    remember_tool_approval(s, deps)

    s2 = resolve_approval_subject("gh_list_issues", {}, mcp_prefixes=frozenset(["gh"]))
    assert is_auto_approved(s2, deps) is True

    s3 = resolve_approval_subject("gh_create_pr", {}, mcp_prefixes=frozenset(["gh"]))
    assert is_auto_approved(s3, deps) is False


def test_remember_tool_approval_deduplicates() -> None:
    """Calling remember twice for the same subject does not add duplicate rules."""
    deps = _deps()
    s = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    remember_tool_approval(s, deps)
    remember_tool_approval(s, deps)
    assert len(deps.session.session_approval_rules) == 1


def test_remember_generic_tool_is_noop() -> None:
    """remember_tool_approval for a generic tool (can_remember=False) stores nothing."""
    deps = _deps()
    s = resolve_approval_subject("save_memory", {"content": "hi"})
    remember_tool_approval(s, deps)
    assert deps.session.session_approval_rules == []


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
