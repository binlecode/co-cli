"""Functional tests for shell tool."""

import pytest
from dataclasses import dataclass

from pydantic_ai import ModelRetry

from co_cli.tools.shell import run_shell_command
from co_cli.sandbox import Sandbox
from co_cli.deps import CoDeps


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def test_shell_executes_in_docker():
    """Test shell tool runs commands in Docker sandbox."""
    sandbox = Sandbox(container_name="co-test-shell")
    ctx = Context(deps=CoDeps(
        sandbox=sandbox,
        auto_confirm=True,
        session_id="test",
    ))

    try:
        result = run_shell_command(ctx, "pwd")
        assert "/workspace" in result
    finally:
        sandbox.cleanup()


def test_shell_nonzero_exit_raises_model_retry():
    """Non-zero exit code raises ModelRetry so the LLM can self-correct."""
    sandbox = Sandbox(container_name="co-test-shell-fail")
    ctx = Context(deps=CoDeps(
        sandbox=sandbox,
        auto_confirm=True,
        session_id="test",
    ))

    try:
        with pytest.raises(ModelRetry, match="Command failed"):
            run_shell_command(ctx, "ls /nonexistent_path_xyz")
    finally:
        sandbox.cleanup()
