"""Tests for approval subject resolution — display format and session-rule matching."""

from co_cli.config._core import settings
from co_cli.context.tool_approvals import is_auto_approved, resolve_approval_subject
from co_cli.deps import ApprovalKindEnum, CoDeps, SessionApprovalRule
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=settings,
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


# --- patch display ---


def test_patch_display_includes_path_and_snippets():
    """patch approval shows path, old_string snippet, and new_string snippet."""
    subject = resolve_approval_subject(
        "patch",
        {
            "path": "src/bar.py",
            "old_string": "old text",
            "new_string": "new text",
            "replace_all": False,
        },
    )
    assert "src/bar.py" in subject.display
    assert "old text" in subject.display
    assert "new text" in subject.display


def test_patch_display_truncates_long_old_string():
    """patch approval truncates old_string/new_string longer than 60 chars."""
    long_old = "x" * 100
    subject = resolve_approval_subject(
        "patch",
        {"path": "a.py", "old_string": long_old, "new_string": "short"},
    )
    assert "…" in subject.display


def test_patch_display_includes_replace_all():
    """patch approval shows replace_all flag."""
    subject = resolve_approval_subject(
        "patch",
        {"path": "a.py", "old_string": "x", "new_string": "y", "replace_all": True},
    )
    assert "True" in subject.display


def test_patch_display_includes_scope_hint():
    """patch approval includes user-legible scope hint."""
    subject = resolve_approval_subject(
        "patch",
        {"path": "src/bar.py", "old_string": "x", "new_string": "y"},
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
    assert "this session" in subject.display


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


# --- subject kind/value/scope resolution ---


def test_resolve_approval_subject_shell_scopes_to_utility():
    """Shell subject resolves to the first token of the command."""
    subject = resolve_approval_subject("run_shell_command", {"cmd": "git status --short"})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.value == "git"
    assert subject.can_remember is True


def test_resolve_approval_subject_path_scopes_to_parent_dir():
    """File-write subject resolves to the parent directory of the target path."""
    subject = resolve_approval_subject("write_file", {"path": "/home/user/project/file.txt"})
    assert subject.kind == ApprovalKindEnum.PATH
    assert subject.value == "/home/user/project"
    assert subject.can_remember is True


def test_resolve_approval_subject_domain_scopes_to_hostname():
    """Web-fetch subject resolves to the hostname of the target URL."""
    subject = resolve_approval_subject(
        "web_fetch", {"url": "https://docs.python.org/3/library/asyncio.html"}
    )
    assert subject.kind == ApprovalKindEnum.DOMAIN
    assert subject.value == "docs.python.org"
    assert subject.can_remember is True


def test_resolve_approval_subject_generic_tool_fallback():
    """Unknown tools fall through to the generic-tool branch, keyed by tool name."""
    subject = resolve_approval_subject(
        "create_gmail_draft", {"to": "test@example.com", "subject": "hi"}
    )
    assert subject.kind == ApprovalKindEnum.TOOL
    assert subject.value == "create_gmail_draft"
    assert subject.can_remember is True
