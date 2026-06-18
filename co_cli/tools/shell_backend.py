"""Shell backend for command execution.

Approval-gated subprocess with env-sanitized execution.
"""

import asyncio
import os
import pty
from dataclasses import dataclass

from co_cli.proc.env import build_subprocess_env
from co_cli.tools.background import _close_process_transport
from co_cli.tools.shell_env import (
    kill_process_tree,
    terminate_process_group,
)


@dataclass
class YieldedProcess:
    """A foreground command still alive after the yield window.

    Handed back to shell_exec for adoption into a background task instead of
    being killed. Carries the live process and `prefix_bytes` — the output the
    foreground read loop already consumed before yield — to seed the background
    log. The foreground read loop has fully stopped before this is returned, so
    the adopter is the sole reader of `process.stdout`.
    """

    process: asyncio.subprocess.Process
    prefix_bytes: bytes


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
        timeout_seconds: int = 120,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        pty: bool = False,
        yield_window_seconds: int = 0,
    ) -> tuple[int, str] | YieldedProcess:
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
        ``pty=True`` is exempt from auto-yield (its master-fd drain has no
        ``proc.stdout`` to hand off); it keeps the plain hard-timeout behaviour.

        When ``yield_window_seconds`` > 0 and below ``timeout_seconds`` (non-pty
        only), a command still alive after ``yield_window_seconds`` seconds is
        returned as a ``YieldedProcess`` carrying the live process and the bytes
        already read, rather than blocking the turn to the hard timeout.
        ``yield_window_seconds`` == 0 disables auto-yield.
        """
        if pty:
            return await self._run_command_pty(cmd, timeout_seconds, cwd, extra_env)
        env = build_subprocess_env(extra_env=extra_env)
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        assert proc.stdout is not None
        # Accumulate into an outer buffer so a cancelled/timed-out read still
        # retains the bytes consumed so far (for partial output or hand-off).
        collected = bytearray()

        async def _drain_until_eof() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    return
                collected.extend(chunk)

        # The first race window is the yield window when auto-yield is armed,
        # else the full hard timeout. A yield-armed command that does not exit
        # within the window is handed off; otherwise the window IS the timeout.
        do_yield = 0 < yield_window_seconds < timeout_seconds
        window = yield_window_seconds if do_yield else timeout_seconds
        drain_task = asyncio.ensure_future(_drain_until_eof())
        try:
            await asyncio.wait_for(drain_task, timeout=window)
        except asyncio.CancelledError:
            # Turn aborted mid-command: wait_for has already cancelled drain_task.
            _kill_process_group_on_cancel(proc)
            raise
        except TimeoutError:
            # wait_for cancelled drain_task, so the foreground loop is fully
            # stopped — collected holds the consumed prefix and the StreamReader
            # buffer is intact for the adopter (sole-reader hand-off).
            if do_yield and proc.returncode is None:
                return YieldedProcess(proc, bytes(collected))
            await kill_process_tree(proc)
            partial_str = bytes(collected).decode("utf-8", errors="replace").strip()
            msg = f"Command timed out after {timeout_seconds}s: {cmd}"
            if partial_str:
                msg += f"\nPartial output:\n{partial_str}"
            raise RuntimeError(msg) from None
        # EOF reached within the window → the process has exited.
        await proc.wait()
        _close_process_transport(proc)
        decoded = bytes(collected).decode("utf-8", errors="replace")
        return proc.returncode, decoded or "(no output)"

    async def _run_command_pty(
        self,
        cmd: str,
        timeout_seconds: int,
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
                    await asyncio.wait_for(eof.wait(), timeout=timeout_seconds)
                except TimeoutError:
                    await kill_process_tree(proc)
                    partial_str = bytes(buffer).decode("utf-8", errors="replace").strip()
                    msg = f"Command timed out after {timeout_seconds}s: {cmd}"
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
