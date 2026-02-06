"""Functional tests for shell tool."""

import pytest
import docker
from dataclasses import dataclass

from co_cli.tools.shell import run_shell_command
from co_cli.sandbox import Sandbox
from co_cli.deps import CoDeps

# Check Docker availability
try:
    docker.from_env()
    DOCKER_AVAILABLE = True
except Exception:
    DOCKER_AVAILABLE = False


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available")
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
