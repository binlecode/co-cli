"""Functional tests for shell tool.

All tests hit real services — no mocks, no stubs.
Tests run on any system (no Docker required).
"""

import os
import pytest

from pydantic_ai import ApprovalRequired, ModelRetry

from co_cli._exec_approvals import add_approval
from co_cli.tools.shell import run_shell_command
from co_cli._shell_backend import ShellBackend
from co_cli.deps import CoDeps, CoServices, CoConfig


from dataclasses import dataclass


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps
    tool_call_approved: bool = True


def _make_ctx(**config_overrides) -> Context:
    shell = config_overrides.pop("shell", ShellBackend())
    return Context(deps=CoDeps(
        services=CoServices(shell=shell),
        config=CoConfig(session_id="test", **config_overrides),
    ))


# --- Basic execution ---


@pytest.mark.asyncio
async def test_shell_basic_exec():
    """ShellBackend runs a command and returns output."""
    ctx = _make_ctx()

    result = await run_shell_command(ctx, "echo hello")
    assert "hello" in result


@pytest.mark.asyncio
async def test_shell_safe_command_runs_without_deferred_approval():
    """Safe-prefix commands execute even when orchestration approval is absent."""
    ctx = Context(
        deps=CoDeps(
            services=CoServices(shell=ShellBackend()),
            config=CoConfig(session_id="test", shell_safe_commands=["pwd"]),
        ),
        tool_call_approved=False,
    )

    result = await run_shell_command(ctx, "pwd")
    assert result.strip()


@pytest.mark.asyncio
async def test_shell_nonzero_exit():
    """Non-zero exit code raises ModelRetry."""
    ctx = _make_ctx()

    with pytest.raises(ModelRetry, match="Shell: command failed"):
        await run_shell_command(ctx, "ls /nonexistent_path_xyz_subprocess")


# --- Timeout ---


@pytest.mark.asyncio
async def test_shell_timeout():
    """Command exceeding timeout raises ModelRetry with timeout message."""
    ctx = _make_ctx()

    with pytest.raises(ModelRetry, match="Shell: command timed out"):
        await run_shell_command(ctx, "sleep 30", timeout=2)


@pytest.mark.asyncio
async def test_shell_timeout_clamped():
    """Tool clamps timeout to shell_max_timeout ceiling."""
    ctx = _make_ctx(shell_max_timeout=2)

    with pytest.raises(ModelRetry, match="Shell: command timed out"):
        await run_shell_command(ctx, "sleep 30", timeout=300)


# --- Shell features ---


@pytest.mark.asyncio
async def test_shell_pipe():
    """Pipes work in shell backend."""
    ctx = _make_ctx()

    result = await run_shell_command(ctx, "echo hello world | wc -w")
    assert result.strip() == "2"


@pytest.mark.asyncio
async def test_shell_requires_deferred_approval_for_unknown_command():
    """Commands outside the safe allowlist raise ApprovalRequired before execution."""
    ctx = Context(
        deps=CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig(session_id="test")),
        tool_call_approved=False,
    )

    with pytest.raises(ApprovalRequired):
        await run_shell_command(ctx, "echo hello world | wc -w")


@pytest.mark.asyncio
async def test_shell_persistent_approval_bypasses_deferred_prompt(tmp_path):
    """Remembered shell approvals allow later matching commands to execute directly."""
    path = tmp_path / "exec-approvals.json"
    add_approval(path, "echo hello world | wc -w", "run_shell_command")
    ctx = Context(
        deps=CoDeps(
            services=CoServices(shell=ShellBackend()),
            config=CoConfig(session_id="test", exec_approvals_path=path),
        ),
        tool_call_approved=False,
    )

    result = await run_shell_command(ctx, "echo hello world | wc -w")
    assert result.strip() == "2"


@pytest.mark.asyncio
async def test_shell_env_sanitized():
    """Shell backend sanitizes environment — dangerous vars are stripped."""
    ctx = _make_ctx()

    # PAGER should be forced to 'cat', not whatever the host has
    result = await run_shell_command(ctx, "echo $PAGER")
    assert result.strip() == "cat"

    # GIT_PAGER should also be forced to 'cat'
    result = await run_shell_command(ctx, "echo $GIT_PAGER")
    assert result.strip() == "cat"

    # PYTHONUNBUFFERED should be set
    result = await run_shell_command(ctx, "echo $PYTHONUNBUFFERED")
    assert result.strip() == "1"


@pytest.mark.asyncio
async def test_shell_dangerous_env_blocked():
    """Dangerous env vars from host do NOT propagate to subprocess."""
    # Temporarily set a dangerous var in our process
    old = os.environ.get("LD_PRELOAD")
    os.environ["LD_PRELOAD"] = "/tmp/evil.so"
    try:
        ctx = _make_ctx()

        result = await run_shell_command(ctx, "echo ${LD_PRELOAD:-unset}")
        assert result.strip() == "unset"
    finally:
        if old is None:
            os.environ.pop("LD_PRELOAD", None)
        else:
            os.environ["LD_PRELOAD"] = old


@pytest.mark.asyncio
async def test_shell_stderr_merged():
    """stderr is merged into stdout in shell backend."""
    ctx = _make_ctx()

    result = await run_shell_command(ctx, "echo 'err msg' >&2; echo 'ok'")
    assert "err msg" in result
    assert "ok" in result


@pytest.mark.asyncio
async def test_shell_cwd_is_host_cwd():
    """ShellBackend runs in the host working directory."""
    ctx = _make_ctx()

    result = await run_shell_command(ctx, "test -f pyproject.toml && echo exists")
    assert "exists" in result


@pytest.mark.asyncio
async def test_shell_variable_expansion():
    """Shell variable expansion works in shell backend."""
    ctx = _make_ctx()

    result = await run_shell_command(ctx, "X=42 && echo val=$X")
    assert "val=42" in result


@pytest.mark.asyncio
async def test_shell_deny_pattern_returns_terminal_error():
    """DENY-pattern commands return terminal_error dict without deferral or execution."""
    ctx = _make_ctx()

    result = await run_shell_command(ctx, "rm -rf /")
    assert isinstance(result, dict)
    assert result.get("error") is True


@pytest.mark.asyncio
async def test_shell_workspace_dir_param():
    """ShellBackend respects custom workspace_dir."""
    backend = ShellBackend(workspace_dir="/tmp")
    ctx = Context(deps=CoDeps(
        services=CoServices(shell=backend),
        config=CoConfig(session_id="test"),
    ))

    result = await run_shell_command(ctx, "pwd")
    # /tmp may resolve to /private/tmp on macOS
    assert "tmp" in result
