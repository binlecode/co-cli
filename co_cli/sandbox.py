import os
import docker
from docker.errors import NotFound, APIError

DEFAULT_DOCKER_IMAGE = "python:3.12-slim"


class Sandbox:
    def __init__(self, image: str | None = None, container_name: str = "co-runner"):
        self._client = None
        self.image = image or DEFAULT_DOCKER_IMAGE
        self.container_name = container_name
        self.workspace_dir = os.getcwd()

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
                detach=True,
                tty=True,
                command="sh"
            )
        except APIError as e:
            raise RuntimeError(f"Failed to ensure Docker container: {e}")

    def run_command(self, cmd: str) -> str:
        """
        Execute a command inside the container and return output.
        """
        try:
            container = self.ensure_container()
            exit_code, output = container.exec_run(cmd, workdir="/workspace")
            return output.decode("utf-8")
        except Exception as e:
            return f"Sandbox Error: {e}"

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