import asyncio
import os
import shlex

import docker
from docker.errors import NotFound, APIError

DEFAULT_DOCKER_IMAGE = "co-cli-sandbox"


class Sandbox:
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
                self._client = docker.from_env()
            except Exception as e:
                # We re-raise or handle, but for now let's just let it fail when accessed
                raise RuntimeError(f"Docker is not available: {e}")
        return self._client

    def ensure_container(self):
        """
        Check for a running co-runner container, else start a new one.
        """
        try:
            container = self.client.containers.get(self.container_name)
            if container.status != "running":
                container.start()
            return container
        except NotFound:
            # Start a new container
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
        """
        Stop and remove the container.
        """
        if self._client is None:
            return
            
        try:
            container = self.client.containers.get(self.container_name)
            container.stop()
            container.remove()
        except (NotFound, Exception):
            pass