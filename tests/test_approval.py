"""Functional tests for shell arg validation in _approval.py and approval helpers."""

from co_cli._approval import _is_safe_command, _validate_args
from co_cli._orchestrate import _check_skill_grant
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli._shell_backend import ShellBackend
from co_cli._shell_policy import evaluate_shell_command, ShellDecision


_SAFE_LIST = ["ls", "cat", "grep", "git status", "git diff", "git log", "git show"]


# -- _validate_args unit cases -------------------------------------------------


def test_validate_args_absolute_path_rejected():
    """Tokens with / are rejected."""
    assert _validate_args("/etc/passwd") is False
    assert _validate_args("/dev/null") is False


def test_validate_args_glob_rejected():
    """Tokens containing glob characters are rejected."""
    assert _validate_args("foo*") is False
    assert _validate_args("*.py") is False
    assert _validate_args("file?") is False


# -- _is_safe_command integration cases ----------------------------------------


def test_path_escape_in_args_rejected():
    """git diff with absolute paths in args is rejected."""
    assert _is_safe_command("git diff --no-index /etc/passwd /dev/null", _SAFE_LIST) is False


def test_glob_in_args_rejected():
    """Commands with glob patterns in args are rejected."""
    assert _is_safe_command("grep foo*", _SAFE_LIST) is False
    assert _is_safe_command("ls *.py", _SAFE_LIST) is False


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


# -- _check_skill_grant pure helper ----------------------------------------


def test_check_skill_grant_match():
    """_check_skill_grant returns True when tool is in skill_tool_grants."""
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig(), session=CoSessionState(skill_tool_grants={"run_shell_command"}))
    assert _check_skill_grant("run_shell_command", deps) is True


def test_check_skill_grant_no_match():
    """_check_skill_grant returns False when tool is not in skill_tool_grants."""
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig(), session=CoSessionState(skill_tool_grants={"run_shell_command"}))
    assert _check_skill_grant("web_search", deps) is False


def test_check_skill_grant_empty_set():
    """_check_skill_grant returns False when skill_tool_grants is empty."""
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    assert _check_skill_grant("run_shell_command", deps) is False
