"""Tests for approval subject resolution and session approval rules."""

from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import ApprovalKindEnum, CoDeps, CoSessionState
from co_cli.tools.approvals import (
    is_auto_approved,
    remember_tool_approval,
    resolve_approval_subject,
)
from co_cli.tools.shell_backend import ShellBackend


def _fresh_deps() -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, session=CoSessionState())


def test_resolve_shell_scopes_to_utility():
    """Shell approval must scope to the first token (utility) so all git commands share one rule."""
    subject = resolve_approval_subject("shell", {"cmd": "git status --porcelain"})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.value == "git"
    assert subject.can_remember is True
    assert "git status" in subject.display


def test_resolve_shell_empty_cmd_cannot_remember():
    """Empty shell command must produce can_remember=False since there is no utility to scope."""
    subject = resolve_approval_subject("shell", {"cmd": ""})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.can_remember is False


def test_resolve_file_write_scopes_to_parent_directory():
    """file_write approval must scope to the parent directory for directory-wide coverage."""
    subject = resolve_approval_subject(
        "file_write", {"path": "/home/user/project/main.py", "content": "x"}
    )
    assert subject.kind == ApprovalKindEnum.PATH
    assert subject.value == "/home/user/project"
    assert subject.can_remember is True


def test_resolve_file_patch_same_scope_as_file_write():
    """file_patch and file_write in the same directory must resolve to the same approval scope."""
    write_subj = resolve_approval_subject("file_write", {"path": "/a/b/c.py", "content": ""})
    patch_subj = resolve_approval_subject(
        "file_patch", {"path": "/a/b/c.py", "old_string": "x", "new_string": "y"}
    )
    assert write_subj.kind == patch_subj.kind
    assert write_subj.value == patch_subj.value


def test_resolve_web_fetch_scopes_to_domain():
    """web_fetch approval must scope to the hostname for domain-wide coverage."""
    subject = resolve_approval_subject("web_fetch", {"url": "https://example.com/some/path?q=1"})
    assert subject.kind == ApprovalKindEnum.DOMAIN
    assert subject.value == "example.com"
    assert subject.can_remember is True


def test_resolve_unknown_tool_uses_tool_kind():
    """Unrecognized tools must fall back to TOOL kind scoped to the tool name."""
    subject = resolve_approval_subject("memory_create", {"kind": "note", "title": "test"})
    assert subject.kind == ApprovalKindEnum.TOOL
    assert subject.value == "memory_create"
    assert subject.can_remember is True


def test_is_auto_approved_false_with_no_stored_rule():
    """is_auto_approved must return False when no matching rule exists in the session."""
    deps = _fresh_deps()
    subject = resolve_approval_subject("shell", {"cmd": "git status"})
    assert is_auto_approved(subject, deps) is False


def test_remember_and_then_auto_approved():
    """remember_tool_approval must persist a rule that is_auto_approved subsequently matches."""
    deps = _fresh_deps()
    subject = resolve_approval_subject("shell", {"cmd": "git status"})
    remember_tool_approval(subject, deps)
    assert is_auto_approved(subject, deps) is True


def test_remember_no_op_when_cannot_remember():
    """remember_tool_approval must not store a rule when subject.can_remember is False."""
    deps = _fresh_deps()
    subject = resolve_approval_subject("shell", {"cmd": ""})
    remember_tool_approval(subject, deps)
    assert len(deps.session.session_approval_rules) == 0


def test_approval_rule_matches_cross_tool_same_directory():
    """An 'always' rule set by file_write must satisfy file_patch approval for the same directory."""
    deps = _fresh_deps()
    write_subj = resolve_approval_subject("file_write", {"path": "/a/b/x.py", "content": "x"})
    remember_tool_approval(write_subj, deps)
    patch_subj = resolve_approval_subject(
        "file_patch", {"path": "/a/b/y.py", "old_string": "x", "new_string": "y"}
    )
    assert is_auto_approved(patch_subj, deps) is True


def test_approval_rule_does_not_match_different_directory():
    """A rule for one directory must not auto-approve writes in a different directory."""
    deps = _fresh_deps()
    write_subj = resolve_approval_subject("file_write", {"path": "/a/b/x.py", "content": "x"})
    remember_tool_approval(write_subj, deps)
    other_subj = resolve_approval_subject("file_write", {"path": "/a/c/x.py", "content": "x"})
    assert is_auto_approved(other_subj, deps) is False
