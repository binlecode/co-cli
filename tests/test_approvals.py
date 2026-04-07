"""Tests for approval subject resolution — display format and session-rule matching."""

from pathlib import Path

from co_cli.config import settings
from co_cli.deps import ApprovalKindEnum, CoDeps, CoConfig, SessionApprovalRule
from co_cli.tools.shell_backend import ShellBackend
from co_cli.context.tool_approvals import is_auto_approved, resolve_approval_subject


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=CoConfig.from_settings(settings, cwd=Path.cwd()),
    )


# --- write_file display ---


def test_write_file_display_includes_path_and_byte_count():
    """write_file approval shows path and byte count derived from content."""
    content = "hello world"
    subject = resolve_approval_subject("write_file", {"path": "src/foo.py", "content": content})
    assert "src/foo.py" in subject.display
    assert str(len(content.encode())) in subject.display
    assert "bytes" in subject.display


def test_write_file_display_includes_scope_hint():
    """write_file approval includes user-legible scope hint."""
    subject = resolve_approval_subject("write_file", {"path": "src/foo.py", "content": "x"})
    assert "allow all writes" in subject.display
    assert "this session" in subject.display


# --- edit_file display ---


def test_edit_file_display_includes_path_and_snippets():
    """edit_file approval shows path, search snippet, and replacement snippet."""
    subject = resolve_approval_subject(
        "edit_file",
        {"path": "src/bar.py", "search": "old text", "replacement": "new text", "replace_all": False},
    )
    assert "src/bar.py" in subject.display
    assert "old text" in subject.display
    assert "new text" in subject.display


def test_edit_file_display_truncates_long_search():
    """edit_file approval truncates search/replacement strings longer than 60 chars."""
    long_search = "x" * 100
    subject = resolve_approval_subject(
        "edit_file",
        {"path": "a.py", "search": long_search, "replacement": "short"},
    )
    assert "…" in subject.display


def test_edit_file_display_includes_replace_all():
    """edit_file approval shows replace_all flag."""
    subject = resolve_approval_subject(
        "edit_file",
        {"path": "a.py", "search": "x", "replacement": "y", "replace_all": True},
    )
    assert "True" in subject.display


def test_edit_file_display_includes_scope_hint():
    """edit_file approval includes user-legible scope hint."""
    subject = resolve_approval_subject(
        "edit_file",
        {"path": "src/bar.py", "search": "x", "replacement": "y"},
    )
    assert "allow all writes" in subject.display
    assert "this session" in subject.display


# --- scope wording — no bracket notation ---


def test_shell_hint_uses_noun_phrase():
    """Shell approval hint is a noun phrase, not bracket notation."""
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    assert "[always → session:" not in subject.display
    assert "this session" in subject.display


def test_path_hint_uses_noun_phrase():
    """Path approval hint is a noun phrase, not bracket notation."""
    subject = resolve_approval_subject("write_file", {"path": "src/foo.py", "content": ""})
    assert "[always → session:" not in subject.display


def test_domain_hint_uses_noun_phrase():
    """Domain approval hint is a noun phrase, not bracket notation."""
    subject = resolve_approval_subject("web_fetch", {"url": "https://example.com/page"})
    assert "[always → session:" not in subject.display
    assert "this session" in subject.display


def test_tool_hint_uses_noun_phrase():
    """Generic tool approval hint is a noun phrase, not bracket notation."""
    subject = resolve_approval_subject("some_mcp_tool", {"arg": "val"})
    assert "[always → session:" not in subject.display
    assert "this session" in subject.display


# --- is_auto_approved exact-match semantics unchanged ---


def test_is_auto_approved_matches_stored_shell_rule():
    """Stored shell rule auto-approves the matching utility."""
    deps = _make_deps()
    deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git status"})
    assert is_auto_approved(subject, deps)


def test_is_auto_approved_no_match_different_utility():
    """Stored shell rule does not auto-approve a different utility."""
    deps = _make_deps()
    deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    subject = resolve_approval_subject("run_shell_command", {"cmd": "rm -rf /"})
    assert not is_auto_approved(subject, deps)


def test_is_auto_approved_no_match_empty_rules():
    """No stored rules means no auto-approval."""
    deps = _make_deps()
    subject = resolve_approval_subject("write_file", {"path": "src/foo.py", "content": "x"})
    assert not is_auto_approved(subject, deps)
