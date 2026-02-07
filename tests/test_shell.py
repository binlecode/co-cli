"""Functional tests for shell tool.

All tests hit real services — no mocks, no stubs.
Docker tests require Docker running. Subprocess tests run on any system.
"""

import os
import pytest

from pydantic_ai import ModelRetry

from co_cli.tools.shell import run_shell_command
from co_cli.sandbox import DockerSandbox, SubprocessBackend, SandboxProtocol
from co_cli.deps import CoDeps


from dataclasses import dataclass


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def _make_ctx(sandbox: SandboxProtocol, **overrides) -> Context:
    return Context(deps=CoDeps(
        sandbox=sandbox,
        auto_confirm=True,
        session_id="test",
        **overrides,
    ))


# --- Basic execution ---


@pytest.mark.asyncio
async def test_shell_executes_in_docker():
    """Tool runs command in Docker sandbox and returns output."""
    sandbox = DockerSandbox(container_name="co-test-shell")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "pwd")
        assert "/workspace" in result
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_nonzero_exit_raises_model_retry():
    """Non-zero exit code raises ModelRetry so the LLM can self-correct."""
    sandbox = DockerSandbox(container_name="co-test-shell-fail")
    ctx = _make_ctx(sandbox)

    try:
        with pytest.raises(ModelRetry, match="Command failed"):
            await run_shell_command(ctx, "ls /nonexistent_path_xyz")
    finally:
        sandbox.cleanup()


# --- Timeout ---


@pytest.mark.asyncio
async def test_shell_timeout_raises_model_retry():
    """Command exceeding timeout raises ModelRetry with timeout message."""
    sandbox = DockerSandbox(container_name="co-test-shell-timeout")
    ctx = _make_ctx(sandbox)

    try:
        with pytest.raises(ModelRetry, match="Command failed"):
            await run_shell_command(ctx, "sleep 30", timeout=2)
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_timeout_clamped_to_ceiling():
    """Tool clamps timeout to sandbox_max_timeout ceiling."""
    sandbox = DockerSandbox(container_name="co-test-shell-clamp")
    ctx = _make_ctx(sandbox, sandbox_max_timeout=3)

    try:
        # Request 300s but ceiling is 3s — sleep 10 should be killed
        with pytest.raises(ModelRetry, match="Command failed"):
            await run_shell_command(ctx, "sleep 10", timeout=300)
    finally:
        sandbox.cleanup()


# --- Shell features (sh -c wrapping) ---


@pytest.mark.asyncio
async def test_shell_pipe():
    """Pipes work via sh -c wrapping."""
    sandbox = DockerSandbox(container_name="co-test-shell-pipe")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "echo hello world | wc -w")
        assert result.strip() == "2"
    finally:
        sandbox.cleanup()


# --- Hardening verification ---


@pytest.mark.asyncio
async def test_shell_runs_as_non_root():
    """Container runs as non-root user (uid 1000)."""
    sandbox = DockerSandbox(container_name="co-test-shell-user")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "id -u")
        assert result.strip() == "1000"
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_network_disabled():
    """Network is disabled by default (network_mode=none)."""
    sandbox = DockerSandbox(container_name="co-test-shell-net")
    ctx = _make_ctx(sandbox)

    try:
        # ping/curl should fail with no network
        with pytest.raises(ModelRetry, match="Command failed"):
            await run_shell_command(ctx, "ping -c1 -W1 127.0.0.1", timeout=5)
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_capabilities_dropped():
    """cap_drop=ALL results in zero effective capabilities."""
    sandbox = DockerSandbox(container_name="co-test-shell-cap")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "grep CapEff /proc/self/status")
        # CapEff should be all zeros when all capabilities are dropped
        cap_hex = result.split(":")[1].strip()
        assert int(cap_hex, 16) == 0
    finally:
        sandbox.cleanup()


# --- Calling patterns: shell features ---


@pytest.mark.asyncio
async def test_shell_redirect_write_and_read():
    """Redirect > creates a file, cat reads it back."""
    sandbox = DockerSandbox(container_name="co-test-shell-redir")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(ctx, "echo 'redirect test' > /workspace/_redir.txt")
        result = await run_shell_command(ctx, "cat /workspace/_redir.txt")
        assert "redirect test" in result
        await run_shell_command(ctx, "rm /workspace/_redir.txt")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_variable_expansion():
    """Shell variable expansion works inside sh -c."""
    sandbox = DockerSandbox(container_name="co-test-shell-var")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "X=42 && echo \"val=$X\"")
        assert "val=42" in result
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_subshell():
    """Subshell $() works."""
    sandbox = DockerSandbox(container_name="co-test-shell-sub")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "echo \"files: $(ls / | wc -l)\"")
        count = int(result.split("files:")[1].strip())
        assert count > 0
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_heredoc():
    """Here-document via cat <<EOF."""
    sandbox = DockerSandbox(container_name="co-test-shell-heredoc")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(
            ctx, "cat <<'EOF'\nline1\nline2\nEOF"
        )
        assert "line1" in result
        assert "line2" in result
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_stderr_merged():
    """stderr is merged into stdout — LLM sees all output."""
    sandbox = DockerSandbox(container_name="co-test-shell-stderr")
    ctx = _make_ctx(sandbox)

    try:
        # echo to stderr via >&2, but command exits 0
        result = await run_shell_command(ctx, "echo 'err msg' >&2; echo 'ok'")
        assert "err msg" in result
        assert "ok" in result
    finally:
        sandbox.cleanup()


# --- Calling patterns: image tools ---


@pytest.mark.asyncio
async def test_shell_python_script_create_and_run():
    """Write a Python script via redirect, execute it."""
    sandbox = DockerSandbox(container_name="co-test-shell-pyscript")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(
            ctx,
            "cat > /workspace/_test.py <<'PY'\n"
            "import json\n"
            "print(json.dumps({'status': 'ok'}))\n"
            "PY",
        )
        result = await run_shell_command(ctx, "python3 /workspace/_test.py")
        assert '"status": "ok"' in result
        await run_shell_command(ctx, "rm /workspace/_test.py")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_jq_json_processing():
    """jq is available and processes JSON."""
    sandbox = DockerSandbox(container_name="co-test-shell-jq")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(
            ctx, "echo '{\"a\":1,\"b\":2}' | jq '.b'"
        )
        assert result.strip() == "2"
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_git_available():
    """git is installed and runnable."""
    sandbox = DockerSandbox(container_name="co-test-shell-git")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "git --version")
        assert "git version" in result
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_tree_output():
    """tree produces directory listing."""
    sandbox = DockerSandbox(container_name="co-test-shell-tree")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(ctx, "mkdir -p /workspace/_treetest/sub && touch /workspace/_treetest/sub/f.txt")
        result = await run_shell_command(ctx, "tree /workspace/_treetest")
        assert "sub" in result
        assert "f.txt" in result
        await run_shell_command(ctx, "rm -rf /workspace/_treetest")
    finally:
        sandbox.cleanup()


# --- Python coding & test workflows ---


@pytest.mark.asyncio
async def test_python_traceback_surfaces():
    """Python tracebacks are visible in output so the LLM can self-correct."""
    sandbox = DockerSandbox(container_name="co-test-py-traceback")
    ctx = _make_ctx(sandbox)

    try:
        with pytest.raises(ModelRetry, match="Command failed") as exc_info:
            await run_shell_command(
                ctx, "python3 -c \"raise ValueError('bad input')\""
            )
        assert "ValueError" in str(exc_info.value)
        assert "bad input" in str(exc_info.value)
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_python_file_io_round_trip():
    """Python writes a file, reads it back — full I/O cycle."""
    sandbox = DockerSandbox(container_name="co-test-py-fileio")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(
            ctx,
            "python3 -c \""
            "from pathlib import Path; "
            "Path('/workspace/_iotest.txt').write_text('round trip'); "
            "print(Path('/workspace/_iotest.txt').read_text())\"",
        )
        # Verify via shell too — proves Python wrote to the mounted volume
        result = await run_shell_command(ctx, "cat /workspace/_iotest.txt")
        assert "round trip" in result
        await run_shell_command(ctx, "rm /workspace/_iotest.txt")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_python_multifile_project():
    """LLM creates a module + script, imports across files."""
    sandbox = DockerSandbox(container_name="co-test-py-multifile")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(ctx, "mkdir -p /workspace/_proj")
        await run_shell_command(
            ctx,
            "cat > /workspace/_proj/lib.py <<'PY'\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "PY",
        )
        await run_shell_command(
            ctx,
            "cat > /workspace/_proj/main.py <<'PY'\n"
            "from lib import add\n"
            "print(f'result={add(3, 4)}')\n"
            "PY",
        )
        result = await run_shell_command(
            ctx, "cd /workspace/_proj && python3 main.py"
        )
        assert "result=7" in result
        await run_shell_command(ctx, "rm -rf /workspace/_proj")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_python_unittest_run():
    """unittest (stdlib) can run a test file the LLM wrote."""
    sandbox = DockerSandbox(container_name="co-test-py-unittest")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(
            ctx,
            "cat > /workspace/_test_sample.py <<'PY'\n"
            "import unittest\n"
            "class TestMath(unittest.TestCase):\n"
            "    def test_addition(self):\n"
            "        self.assertEqual(1 + 1, 2)\n"
            "    def test_string(self):\n"
            "        self.assertEqual('hello'.upper(), 'HELLO')\n"
            "PY",
        )
        result = await run_shell_command(
            ctx, "python3 -m unittest /workspace/_test_sample.py -v"
        )
        assert "test_addition" in result
        assert "test_string" in result
        assert "ok" in result.lower()
        await run_shell_command(ctx, "rm /workspace/_test_sample.py")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_python_unittest_failure_output():
    """unittest failure output (assertion details) surfaces for LLM self-correction."""
    sandbox = DockerSandbox(container_name="co-test-py-unittest-fail")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(
            ctx,
            "cat > /workspace/_test_fail.py <<'PY'\n"
            "import unittest\n"
            "class TestFail(unittest.TestCase):\n"
            "    def test_will_fail(self):\n"
            "        self.assertEqual(1, 2, 'math is broken')\n"
            "PY",
        )
        with pytest.raises(ModelRetry, match="Command failed") as exc_info:
            await run_shell_command(
                ctx, "python3 -m unittest /workspace/_test_fail.py -v"
            )
        error_msg = str(exc_info.value)
        assert "FAIL" in error_msg
        assert "math is broken" in error_msg
        await run_shell_command(ctx, "rm /workspace/_test_fail.py")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_python_edit_and_rerun():
    """Simulate LLM fix cycle: write buggy code, see error, fix, rerun."""
    sandbox = DockerSandbox(container_name="co-test-py-editloop")
    ctx = _make_ctx(sandbox)

    try:
        # Step 1: LLM writes buggy code
        await run_shell_command(
            ctx,
            "cat > /workspace/_buggy.py <<'PY'\n"
            "def greet(name):\n"
            "    return 'Hello ' + nam  # typo\n"
            "print(greet('World'))\n"
            "PY",
        )
        # Step 2: Run fails — LLM sees NameError
        with pytest.raises(ModelRetry, match="Command failed") as exc_info:
            await run_shell_command(ctx, "python3 /workspace/_buggy.py")
        assert "NameError" in str(exc_info.value)

        # Step 3: LLM fixes the code
        await run_shell_command(
            ctx,
            "cat > /workspace/_buggy.py <<'PY'\n"
            "def greet(name):\n"
            "    return 'Hello ' + name\n"
            "print(greet('World'))\n"
            "PY",
        )
        # Step 4: Rerun succeeds
        result = await run_shell_command(ctx, "python3 /workspace/_buggy.py")
        assert "Hello World" in result
        await run_shell_command(ctx, "rm /workspace/_buggy.py")
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_python_partial_output_on_timeout():
    """PYTHONUNBUFFERED ensures partial output is captured before timeout kill."""
    sandbox = DockerSandbox(container_name="co-test-py-partial")
    ctx = _make_ctx(sandbox)

    try:
        await run_shell_command(
            ctx,
            "cat > /workspace/_slow.py <<'PY'\n"
            "import time\n"
            "print('started')\n"
            "time.sleep(30)\n"
            "print('never')\n"
            "PY",
        )
        with pytest.raises(ModelRetry, match="Command failed") as exc_info:
            await run_shell_command(ctx, "python3 /workspace/_slow.py", timeout=3)
        # 'started' was flushed before kill thanks to PYTHONUNBUFFERED
        assert "started" in str(exc_info.value)
        assert "never" not in str(exc_info.value)
        await run_shell_command(ctx, "rm /workspace/_slow.py")
    finally:
        sandbox.cleanup()


# --- Edge cases ---


@pytest.mark.asyncio
async def test_shell_special_chars_in_command():
    """Quotes, dollars, backticks survive shlex.quote wrapping."""
    sandbox = DockerSandbox(container_name="co-test-shell-special")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "echo \"hello 'world' $HOME\"")
        assert "hello" in result
        assert "world" in result
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_large_output():
    """Large output (>64KB) is returned without truncation."""
    sandbox = DockerSandbox(container_name="co-test-shell-large")
    ctx = _make_ctx(sandbox)

    try:
        # Generate ~100KB of output
        result = await run_shell_command(ctx, "seq 1 10000")
        lines = result.strip().split("\n")
        assert lines[0] == "1"
        assert lines[-1] == "10000"
        assert len(lines) == 10000
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_empty_output():
    """Command that produces no stdout returns empty string."""
    sandbox = DockerSandbox(container_name="co-test-shell-empty")
    ctx = _make_ctx(sandbox)

    try:
        result = await run_shell_command(ctx, "true")
        assert result.strip() == ""
    finally:
        sandbox.cleanup()


@pytest.mark.asyncio
async def test_shell_workspace_is_host_cwd():
    """Files in host CWD are visible at /workspace."""
    sandbox = DockerSandbox(container_name="co-test-shell-mount")
    ctx = _make_ctx(sandbox)

    try:
        # pyproject.toml exists in repo root (our CWD)
        result = await run_shell_command(ctx, "test -f /workspace/pyproject.toml && echo exists")
        assert "exists" in result
    finally:
        sandbox.cleanup()


# =============================================================================
# Subprocess backend tests (no Docker required)
# =============================================================================


@pytest.mark.asyncio
async def test_subprocess_basic_exec():
    """SubprocessBackend runs a command and returns output."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "echo hello")
    assert "hello" in result


@pytest.mark.asyncio
async def test_subprocess_nonzero_exit():
    """Non-zero exit code raises ModelRetry."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    with pytest.raises(ModelRetry, match="Command failed"):
        await run_shell_command(ctx, "ls /nonexistent_path_xyz_subprocess")


@pytest.mark.asyncio
async def test_subprocess_timeout():
    """Command exceeding timeout raises ModelRetry with timeout message."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    with pytest.raises(ModelRetry, match="Command failed.*timed out"):
        await run_shell_command(ctx, "sleep 30", timeout=2)


@pytest.mark.asyncio
async def test_subprocess_timeout_clamped():
    """Tool clamps timeout to sandbox_max_timeout ceiling."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend, sandbox_max_timeout=2)

    with pytest.raises(ModelRetry, match="Command failed"):
        await run_shell_command(ctx, "sleep 30", timeout=300)


@pytest.mark.asyncio
async def test_subprocess_pipe():
    """Pipes work in subprocess backend."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "echo hello world | wc -w")
    assert result.strip() == "2"


@pytest.mark.asyncio
async def test_subprocess_env_sanitized():
    """Subprocess backend sanitizes environment — dangerous vars are stripped."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

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
async def test_subprocess_dangerous_env_blocked():
    """Dangerous env vars from host do NOT propagate to subprocess."""
    # Temporarily set a dangerous var in our process
    old = os.environ.get("LD_PRELOAD")
    os.environ["LD_PRELOAD"] = "/tmp/evil.so"
    try:
        backend = SubprocessBackend()
        ctx = _make_ctx(backend)

        result = await run_shell_command(ctx, "echo ${LD_PRELOAD:-unset}")
        assert result.strip() == "unset"
    finally:
        if old is None:
            os.environ.pop("LD_PRELOAD", None)
        else:
            os.environ["LD_PRELOAD"] = old


@pytest.mark.asyncio
async def test_subprocess_stderr_merged():
    """stderr is merged into stdout in subprocess backend."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "echo 'err msg' >&2; echo 'ok'")
    assert "err msg" in result
    assert "ok" in result


@pytest.mark.asyncio
async def test_subprocess_cwd_is_host_cwd():
    """SubprocessBackend runs in the host working directory."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "test -f pyproject.toml && echo exists")
    assert "exists" in result


@pytest.mark.asyncio
async def test_subprocess_isolation_level():
    """SubprocessBackend reports isolation_level='none'."""
    backend = SubprocessBackend()
    assert backend.isolation_level == "none"


@pytest.mark.asyncio
async def test_docker_isolation_level():
    """DockerSandbox reports isolation_level='full'."""
    sandbox = DockerSandbox(container_name="co-test-isolation")
    assert sandbox.isolation_level == "full"


# =============================================================================
# Sandbox protocol conformance
# =============================================================================


def test_docker_sandbox_satisfies_protocol():
    """DockerSandbox is a runtime instance of SandboxProtocol."""
    sandbox = DockerSandbox(container_name="co-test-proto")
    assert isinstance(sandbox, SandboxProtocol)


def test_subprocess_backend_satisfies_protocol():
    """SubprocessBackend is a runtime instance of SandboxProtocol."""
    backend = SubprocessBackend()
    assert isinstance(backend, SandboxProtocol)


# =============================================================================
# Auto-detection and config tests
# =============================================================================


def test_sandbox_backend_config_field():
    """sandbox_backend config field exists and defaults to 'auto'."""
    from co_cli.config import Settings
    s = Settings.model_validate({})
    assert s.sandbox_backend == "auto"


def test_sandbox_backend_env_override():
    """CO_CLI_SANDBOX_BACKEND env var overrides the config default."""
    old = os.environ.get("CO_CLI_SANDBOX_BACKEND")
    os.environ["CO_CLI_SANDBOX_BACKEND"] = "subprocess"
    try:
        from co_cli.config import Settings
        s = Settings.model_validate({})
        assert s.sandbox_backend == "subprocess"
    finally:
        if old is None:
            os.environ.pop("CO_CLI_SANDBOX_BACKEND", None)
        else:
            os.environ["CO_CLI_SANDBOX_BACKEND"] = old


def test_create_sandbox_subprocess_explicit():
    """_create_sandbox with backend=subprocess always returns SubprocessBackend."""
    from co_cli.config import settings
    original = settings.sandbox_backend
    settings.sandbox_backend = "subprocess"
    try:
        from co_cli.main import _create_sandbox
        sandbox = _create_sandbox("test-session")
        assert isinstance(sandbox, SubprocessBackend)
        assert sandbox.isolation_level == "none"
    finally:
        settings.sandbox_backend = original


def test_subprocess_cleanup_is_noop():
    """SubprocessBackend.cleanup() is a no-op and doesn't raise."""
    backend = SubprocessBackend()
    backend.cleanup()  # should not raise


@pytest.mark.asyncio
async def test_subprocess_empty_output():
    """Command with no output returns empty string."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "true")
    assert result.strip() == ""


@pytest.mark.asyncio
async def test_subprocess_variable_expansion():
    """Shell variable expansion works in subprocess backend."""
    backend = SubprocessBackend()
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "X=42 && echo val=$X")
    assert "val=42" in result


@pytest.mark.asyncio
async def test_subprocess_workspace_dir_param():
    """SubprocessBackend respects custom workspace_dir."""
    backend = SubprocessBackend(workspace_dir="/tmp")
    ctx = _make_ctx(backend)

    result = await run_shell_command(ctx, "pwd")
    # /tmp may resolve to /private/tmp on macOS
    assert "tmp" in result
