"""Functional tests for shell arg validation in _approval.py and approval helpers."""

from co_cli._approval import _is_safe_command, _validate_args
from co_cli._approval_risk import classify_tool_call, ApprovalRisk
from co_cli._orchestrate import _check_skill_grant
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend
from co_cli.shell_policy import evaluate_shell_command, ShellDecision


_SAFE_LIST = ["ls", "cat", "grep", "git status", "git diff", "git log", "git show"]


# -- _validate_args unit cases -------------------------------------------------


def test_validate_args_empty():
    """Empty args string is safe."""
    assert _validate_args("") is True


def test_validate_args_simple_flags():
    """Single-letter and long flags pass."""
    assert _validate_args("-v") is True
    assert _validate_args("--short") is True
    assert _validate_args("-la --color") is True


def test_validate_args_word_only():
    """Plain words pass."""
    assert _validate_args("HEAD") is True
    assert _validate_args("HEAD~1") is True


def test_validate_args_absolute_path_rejected():
    """Tokens with / are rejected."""
    assert _validate_args("/etc/passwd") is False
    assert _validate_args("/dev/null") is False


def test_validate_args_glob_rejected():
    """Tokens containing glob characters are rejected."""
    assert _validate_args("foo*") is False
    assert _validate_args("*.py") is False
    assert _validate_args("file?") is False


def test_validate_args_traversal_rejected():
    """Path traversal sequences are rejected."""
    assert _validate_args("../etc/passwd") is False
    assert _validate_args("./script.sh") is False
    assert _validate_args("~/secret") is False


def test_validate_args_null_byte_rejected():
    """Null bytes are rejected."""
    assert _validate_args("foo\x00bar") is False


def test_validate_args_brace_expansion_rejected():
    """Shell brace expansion is rejected."""
    assert _validate_args("{a,b}") is False


# -- _is_safe_command integration cases ----------------------------------------


def test_path_escape_in_args_rejected():
    """git diff with absolute paths in args is rejected."""
    assert _is_safe_command("git diff --no-index /etc/passwd /dev/null", _SAFE_LIST) is False


def test_relative_path_in_args_rejected():
    """Commands with path traversal in args are rejected."""
    assert _is_safe_command("cat ../secret.txt", _SAFE_LIST) is False


def test_glob_in_args_rejected():
    """Commands with glob patterns in args are rejected."""
    assert _is_safe_command("grep foo*", _SAFE_LIST) is False
    assert _is_safe_command("ls *.py", _SAFE_LIST) is False


def test_safe_flag_args_allowed():
    """Commands with plain flags pass."""
    assert _is_safe_command("git status --short", _SAFE_LIST) is True
    assert _is_safe_command("git diff HEAD~1", _SAFE_LIST) is True
    assert _is_safe_command("git log --oneline", _SAFE_LIST) is True


def test_no_args_allowed():
    """Commands with no args pass."""
    assert _is_safe_command("ls", _SAFE_LIST) is True
    assert _is_safe_command("git status", _SAFE_LIST) is True


def test_chaining_still_rejected():
    """Chaining operators are still rejected before arg validation."""
    assert _is_safe_command("ls; rm -rf /", _SAFE_LIST) is False
    assert _is_safe_command("git status && git push", _SAFE_LIST) is False


# -- Shell policy engine (evaluate_shell_command) --------------------------


def test_shell_policy_deny_heredoc():
    """Heredoc injection pattern is DENY tier — blocked without user prompt."""
    result = evaluate_shell_command("cat << EOF\nsecret\nEOF", _SAFE_LIST)
    assert result.decision == ShellDecision.DENY
    assert "heredoc" in result.reason


def test_shell_policy_deny_absolute_path_destruction():
    """rm -rf / is DENY tier."""
    result = evaluate_shell_command("rm -rf /", _SAFE_LIST)
    assert result.decision == ShellDecision.DENY


def test_shell_policy_allow_safe_prefix():
    """Command matching a safe prefix is ALLOW tier — auto-approved."""
    result = evaluate_shell_command("ls", _SAFE_LIST)
    assert result.decision == ShellDecision.ALLOW


def test_shell_policy_require_approval_unknown():
    """Unknown command falls through to REQUIRE_APPROVAL."""
    result = evaluate_shell_command("curl https://example.com", _SAFE_LIST)
    assert result.decision == ShellDecision.REQUIRE_APPROVAL


# -- Approval risk classifier ----------------------------------------------


def test_high_risk_annotation_write_file():
    """write_file is classified HIGH risk."""
    risk = classify_tool_call("write_file", {"path": "foo.txt", "content": "x"})
    assert risk == ApprovalRisk.HIGH


def test_low_risk_web_search():
    """web_search is classified LOW risk."""
    risk = classify_tool_call("web_search", {"query": "hello"})
    assert risk == ApprovalRisk.LOW


def test_medium_risk_default():
    """Unknown tool defaults to MEDIUM risk."""
    risk = classify_tool_call("some_unknown_tool", {})
    assert risk == ApprovalRisk.MEDIUM


# -- _check_skill_grant pure helper ----------------------------------------


def test_check_skill_grant_match():
    """_check_skill_grant returns True when tool is in active_skill_allowed_tools."""
    deps = CoDeps(shell=ShellBackend(), active_skill_allowed_tools={"run_shell_command"})
    assert _check_skill_grant("run_shell_command", deps) is True


def test_check_skill_grant_no_match():
    """_check_skill_grant returns False when tool is not in active_skill_allowed_tools."""
    deps = CoDeps(shell=ShellBackend(), active_skill_allowed_tools={"run_shell_command"})
    assert _check_skill_grant("web_search", deps) is False


def test_check_skill_grant_empty_set():
    """_check_skill_grant returns False when active_skill_allowed_tools is empty."""
    deps = CoDeps(shell=ShellBackend())
    assert _check_skill_grant("run_shell_command", deps) is False
