"""Tests for approval subject resolution and session approval persistence."""

from pydantic_ai import DeferredToolResults

from co_cli.config.core import settings
from co_cli.deps import ApprovalKindEnum, CoDeps, SessionApprovalRule
from co_cli.tools.approvals import (
    is_auto_approved,
    record_approval_choice,
    remember_tool_approval,
    resolve_approval_subject,
)
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
    subject = resolve_approval_subject("file_write", {"path": "src/foo.py", "content": content})
    assert "src/foo.py" in subject.display
    assert str(len(content.encode())) in subject.display
    assert "bytes" in subject.display


def test_write_file_display_includes_scope_hint():
    """write_file approval includes user-legible scope hint."""
    subject = resolve_approval_subject("file_write", {"path": "src/foo.py", "content": "x"})
    assert "allow all writes" in subject.display
    assert "this session" in subject.display


# --- patch display ---


def test_patch_display_includes_path_and_snippets():
    """patch approval shows path, old_string snippet, and new_string snippet."""
    subject = resolve_approval_subject(
        "file_patch",
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
    """patch approval truncates old_string/new_string longer than 400 chars."""
    long_old = "x" * 500
    subject = resolve_approval_subject(
        "file_patch",
        {"path": "a.py", "old_string": long_old, "new_string": "short"},
    )
    assert "…" in subject.display


def test_patch_display_includes_replace_all():
    """patch approval shows replace_all flag."""
    subject = resolve_approval_subject(
        "file_patch",
        {"path": "a.py", "old_string": "x", "new_string": "y", "replace_all": True},
    )
    assert "True" in subject.display


def test_patch_display_includes_scope_hint():
    """patch approval includes user-legible scope hint."""
    subject = resolve_approval_subject(
        "file_patch",
        {"path": "src/bar.py", "old_string": "x", "new_string": "y"},
    )
    assert "allow all writes" in subject.display
    assert "this session" in subject.display


# --- scope wording — no bracket notation ---


def test_shell_hint_uses_noun_phrase():
    """Shell approval hint is a noun phrase, not bracket notation."""
    subject = resolve_approval_subject("shell", {"cmd": "git status"})
    assert "[always → session:" not in subject.display
    assert "this session" in subject.display


def test_path_hint_uses_noun_phrase():
    """Path approval hint is a noun phrase, not bracket notation."""
    subject = resolve_approval_subject("file_write", {"path": "src/foo.py", "content": ""})
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
    subject = resolve_approval_subject("shell", {"cmd": "git status"})
    assert is_auto_approved(subject, deps)


def test_is_auto_approved_no_match_different_utility():
    """Stored shell rule does not auto-approve a different utility."""
    deps = _make_deps()
    deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    subject = resolve_approval_subject("shell", {"cmd": "rm -rf /"})
    assert not is_auto_approved(subject, deps)


def test_is_auto_approved_no_match_empty_rules():
    """No stored rules means no auto-approval."""
    deps = _make_deps()
    subject = resolve_approval_subject("file_write", {"path": "src/foo.py", "content": "x"})
    assert not is_auto_approved(subject, deps)


def test_remember_tool_approval_stores_rule_and_auto_approves():
    """remember_tool_approval stores a session rule; subsequent is_auto_approved returns True."""
    deps = _make_deps()
    subject = resolve_approval_subject("shell", {"cmd": "git log"})
    remember_tool_approval(subject, deps)
    assert (
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
        in deps.session.session_approval_rules
    )
    assert is_auto_approved(subject, deps) is True


def test_remember_tool_approval_is_idempotent():
    """Calling remember_tool_approval twice does not duplicate the session rule."""
    deps = _make_deps()
    subject = resolve_approval_subject("shell", {"cmd": "git status"})
    remember_tool_approval(subject, deps)
    remember_tool_approval(subject, deps)
    rule = SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    assert deps.session.session_approval_rules.count(rule) == 1


def test_record_approval_choice_with_remember_stores_rule():
    """record_approval_choice with remember=True stores a session rule via remember_tool_approval."""
    deps = _make_deps()
    subject = resolve_approval_subject("shell", {"cmd": "git push"})
    approvals = DeferredToolResults()
    record_approval_choice(
        approvals,
        tool_call_id="call-1",
        approved=True,
        subject=subject,
        deps=deps,
        remember=True,
    )
    assert approvals.approvals["call-1"] is True
    assert is_auto_approved(subject, deps) is True


def test_record_approval_choice_deny_does_not_store_rule():
    """Denied approvals must not persist a session rule."""
    deps = _make_deps()
    subject = resolve_approval_subject("shell", {"cmd": "git push"})
    approvals = DeferredToolResults()
    record_approval_choice(
        approvals,
        tool_call_id="call-2",
        approved=False,
        subject=subject,
        deps=deps,
        remember=False,
    )
    assert is_auto_approved(subject, deps) is False
    assert deps.session.session_approval_rules == []


# --- subject kind/value/scope resolution ---


def test_resolve_approval_subject_shell_scopes_to_utility():
    """Shell subject resolves to the first token of the command."""
    subject = resolve_approval_subject("shell", {"cmd": "git status --short"})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.value == "git"
    assert subject.can_remember is True


def test_resolve_approval_subject_path_scopes_to_parent_dir():
    """File-write subject resolves to the parent directory of the target path."""
    subject = resolve_approval_subject("file_write", {"path": "/home/user/project/file.txt"})
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
        "google_gmail_draft", {"to": "test@example.com", "subject": "hi"}
    )
    assert subject.kind == ApprovalKindEnum.TOOL
    assert subject.value == "google_gmail_draft"
    assert subject.can_remember is True


# --- write_file preview ---


def test_write_file_preview_populated():
    """write_file subject has preview containing content lines when content is non-empty."""
    content = "line one\nline two\nline three"
    subject = resolve_approval_subject("file_write", {"path": "f.py", "content": content})
    assert subject.preview is not None
    assert "line one" in subject.preview
    assert "line two" in subject.preview


def test_write_file_preview_truncated():
    """write_file preview is capped at 30 lines with a truncation marker when exceeded."""
    content = "line\n" * 50
    subject = resolve_approval_subject("file_write", {"path": "f.py", "content": content})
    assert subject.preview is not None
    assert "... (" in subject.preview
    assert subject.preview.count("\n") < 50


def test_write_file_preview_none_for_empty_content():
    """write_file subject has preview=None when content is an empty string."""
    subject = resolve_approval_subject("file_write", {"path": "f.py", "content": ""})
    assert subject.preview is None


# --- approval-loop wiring regression ---
