"""Shell backend for command execution.

Approval-gated subprocess with env-sanitized execution.
"""

import asyncio
import os

from co_cli._shell_env import kill_process_tree, restricted_env


class ShellBackend:
    """Subprocess-based shell backend with env-sanitized execution."""

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
