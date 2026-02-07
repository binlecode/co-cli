"""Sandbox backends for shell command execution.

Provides a protocol abstraction with two implementations:
- DockerSandbox: full isolation via Docker container (primary)
- SubprocessBackend: no isolation, env-sanitized subprocess (fallback)
"""

import asyncio
import os
import shlex
from typing import Protocol, runtime_checkable

from co_cli._sandbox_env import kill_process_tree, restricted_env

DEFAULT_DOCKER_IMAGE = "co-cli-sandbox"


@runtime_checkable
class SandboxProtocol(Protocol):
    """Execution environment for shell commands."""

    isolation_level: str  # "full" | "none"

    async def run_command(self, cmd: str, timeout: int = 120) -> str: ...
    def cleanup(self) -> None: ...


class DockerSandbox:
    """Docker-based sandbox with full container isolation."""

    isolation_level: str = "full"

    def __init__(
        self,
        image: str | None = None,
        container_name: str = "co-runner",
        network_mode: str = "none",
        mem_limit: str = "1g",
        cpus: int = 1,
    ):
        self._client = None
        self.image = image or DEFAULT_DOCKER_IMAGE
        self.container_name = container_name
        self.workspace_dir = os.getcwd()
        self.network_mode = network_mode
        self.mem_limit = mem_limit
        self.nano_cpus = cpus * 1_000_000_000

    @property
    def client(self):
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except Exception as e:
                raise RuntimeError(f"Docker is not available: {e}")
        return self._client

    def ensure_container(self):
        """Check for a running co-runner container, else start a new one."""
        import docker
        from docker.errors import NotFound, APIError

        try:
            container = self.client.containers.get(self.container_name)
            if container.status != "running":
                container.start()
            return container
        except NotFound:
            return self.client.containers.run(
                self.image,
                name=self.container_name,
                volumes={self.workspace_dir: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                user="1000:1000",
                network_mode=self.network_mode,
                mem_limit=self.mem_limit,
                nano_cpus=self.nano_cpus,
                pids_limit=256,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                detach=True,
                tty=True,
                command="sh",
            )
        except APIError as e:
            raise RuntimeError(f"Failed to ensure Docker container: {e}")

    async def run_command(self, cmd: str, timeout: int = 120) -> str:
        """Execute a command inside the container and return output.

        Uses two timeout layers:
        1. coreutils ``timeout`` inside the container (kills the process).
        2. ``asyncio.wait_for`` on the Python side (keeps the event loop free).

        The Python-level deadline is timeout + 5s grace so the in-container
        kill fires first under normal conditions.

        Raises RuntimeError on non-zero exit code, timeout, or Docker errors.
        """
        container = self.ensure_container()
        wrapped = f"timeout {timeout} sh -c {shlex.quote(cmd)}"
        try:
            exit_code, output = await asyncio.wait_for(
                asyncio.to_thread(
                    container.exec_run, wrapped,
                    workdir="/workspace",
                    environment={"PYTHONUNBUFFERED": "1"},
                ),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(f"Command timed out after {timeout}s: {cmd}")
        decoded = output.decode("utf-8")
        if exit_code == 124:
            raise RuntimeError(
                f"Command timed out after {timeout}s: {cmd}\n"
                f"Partial output:\n{decoded.strip()}"
            )
        if exit_code != 0:
            raise RuntimeError(f"exit code {exit_code}: {decoded.strip()}")
        return decoded

    def cleanup(self):
        """Stop and remove the container."""
        if self._client is None:
            return

        from docker.errors import NotFound

        try:
            container = self.client.containers.get(self.container_name)
            container.stop()
            container.remove()
        except (NotFound, Exception):
            pass


class SubprocessBackend:
    """Subprocess-based backend with no isolation (env-sanitized fallback)."""

    isolation_level: str = "none"

    def __init__(self, workspace_dir: str | None = None):
        self.workspace_dir = workspace_dir or os.getcwd()

    async def run_command(self, cmd: str, timeout: int = 120) -> str:
        """Execute a command as a subprocess with sanitized environment.

        Uses start_new_session=True for process group killing on timeout.
        Raises RuntimeError on non-zero exit code or timeout.
        """
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=restricted_env(),
            start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await kill_process_tree(proc)
            # Read any buffered output before raising
            partial = b""
            if proc.stdout:
                try:
                    partial = await asyncio.wait_for(proc.stdout.read(), timeout=1.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            partial_str = partial.decode("utf-8", errors="replace").strip()
            msg = f"Command timed out after {timeout}s: {cmd}"
            if partial_str:
                msg += f"\nPartial output:\n{partial_str}"
            raise RuntimeError(msg)
        decoded = stdout.decode("utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"exit code {proc.returncode}: {decoded.strip()}")
        return decoded

    def cleanup(self) -> None:
        """No-op — subprocess backend has no persistent resources."""
        pass


# Keep backward-compatible alias for existing imports
Sandbox = DockerSandbox
