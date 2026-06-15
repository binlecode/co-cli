"""Shell backend for command execution.

Approval-gated subprocess with env-sanitized execution.
"""

import asyncio
import os
import pty

from co_cli.tools.shell_env import (
    build_subprocess_env,
    kill_process_tree,
    terminate_process_group,
)

# Retains in-flight SIGKILL-escalation tasks scheduled on cancel so the event loop
# does not GC them before they run; each discards itself on completion. Module-level
# (not on the backend) keeps ShellBackend stateless.
_pending_force_kills: set[asyncio.Task[None]] = set()


def _kill_process_group_on_cancel(proc: asyncio.subprocess.Process) -> None:
    """Cancellation-safe process-group teardown for a turn aborted mid-command.

    Sends an immediate synchronous SIGTERM (guaranteed even if the event loop is
    tearing down on app exit), then schedules ``kill_process_tree`` as a retained
    background task to escalate to SIGKILL if the group ignores SIGTERM. The async
    escalation cannot run inline — the awaiting task is itself being cancelled — so
    it rides an independent task, mirroring openclaw's unref'd timer and opencode's
    acquireRelease finalizer. Without escalation a SIGTERM-ignoring child orphans.
    """
    terminate_process_group(proc)
    task = asyncio.ensure_future(kill_process_tree(proc))
    _pending_force_kills.add(task)
    task.add_done_callback(_pending_force_kills.discard)


class ShellBackend:
    """Subprocess-based shell backend with env-sanitized execution.

    Stateless — the working directory is supplied per call via ``cwd`` (shell_exec
    anchors it to ``deps.workspace_dir``). No backend-held workspace anchor exists,
    so there is a single source of truth for the cwd.
    """

    async def run_command(
        self,
        cmd: str,
        timeout: int = 120,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        pty: bool = False,
    ) -> tuple[int, str]:
        """Execute a command as a subprocess with sanitized environment.

        Uses start_new_session=True for process group killing on timeout.
        Returns (exit_code, combined_output). Raises RuntimeError only on timeout.

        ``extra_env`` keys overlay the restricted base env (intended for the
        active skill's ``skill_env``). Keys that would shadow the host
        allowlist (PATH, HOME, etc.) are refused to keep the security
        boundary intact — a model-authored skill must not be able to
        redirect PATH or HOME.

        When ``pty=True`` the child's std fds are wired to a pseudo-terminal
        slave, so ``isatty()`` reports True and programs emit ANSI / line-buffer
        as on a real terminal. Output fidelity only — there is no stdin channel,
        so it does not interactively drive a program. Raw ANSI is preserved.
        """
        if pty:
            return await self._run_command_pty(cmd, timeout, cwd, extra_env)
        env = build_subprocess_env(extra_env=extra_env)
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            _kill_process_group_on_cancel(proc)
            raise
        except TimeoutError:
            await kill_process_tree(proc)
            # Read any buffered output before raising
            partial = b""
            if proc.stdout:
                try:
                    partial = await asyncio.wait_for(proc.stdout.read(), timeout=1.0)
                except (TimeoutError, Exception):
                    pass
            partial_str = partial.decode("utf-8", errors="replace").strip()
            msg = f"Command timed out after {timeout}s: {cmd}"
            if partial_str:
                msg += f"\nPartial output:\n{partial_str}"
            raise RuntimeError(msg) from None
        decoded = stdout.decode("utf-8")
        return proc.returncode, decoded or "(no output)"

    async def _run_command_pty(
        self,
        cmd: str,
        timeout: int,
        cwd: str | None,
        extra_env: dict[str, str] | None,
    ) -> tuple[int, str]:
        """PTY-backed variant of run_command — output fidelity, no stdin drive.

        The child's std fds are the pty slave, so ``proc.stdout`` is None and
        ``communicate()`` is unusable. The raw master fd is drained via
        ``loop.add_reader`` into a buffer; an EOF event bounds the drain under
        ``asyncio.wait_for``. Raw ANSI is preserved (not stripped).
        """
        env = build_subprocess_env(extra_env=extra_env)
        master, slave = pty.openpty()
        # master is closed in the outer finally on every path; slave is closed
        # the instant the spawn returns or raises. A spawn failure (e.g. a
        # non-existent cwd) would otherwise leak both fds, which the non-pty
        # path cannot do since it pre-opens nothing.
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sh",
                    "-c",
                    cmd,
                    cwd=cwd,
                    env=env,
                    start_new_session=True,
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                )
            finally:
                os.close(slave)
            loop = asyncio.get_running_loop()
            buffer = bytearray()
            eof = asyncio.Event()

            def _on_readable() -> None:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    # macOS (Darwin) raises OSError(EIO) on a master read after
                    # the child exits; Linux returns b"". Both mean clean EOF.
                    eof.set()
                    return
                if not chunk:
                    eof.set()
                    return
                buffer.extend(chunk)

            loop.add_reader(master, _on_readable)
            try:
                try:
                    await asyncio.wait_for(eof.wait(), timeout=timeout)
                except TimeoutError:
                    await kill_process_tree(proc)
                    partial_str = bytes(buffer).decode("utf-8", errors="replace").strip()
                    msg = f"Command timed out after {timeout}s: {cmd}"
                    if partial_str:
                        msg += f"\nPartial output:\n{partial_str}"
                    raise RuntimeError(msg) from None
                await proc.wait()
            except asyncio.CancelledError:
                _kill_process_group_on_cancel(proc)
                raise
            finally:
                loop.remove_reader(master)
        finally:
            os.close(master)
        decoded = bytes(buffer).decode("utf-8", errors="replace")
        return proc.returncode, decoded or "(no output)"

    def cleanup(self) -> None:
        """No-op — subprocess backend has no persistent resources."""
        pass
