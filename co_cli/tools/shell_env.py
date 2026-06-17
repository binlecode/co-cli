"""Shell process management: cancellation-safe process-group teardown."""

import asyncio
import os
import signal


def terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    """Synchronously SIGTERM a process group — cancellation-safe cleanup.

    Used when a turn is cancelled mid-command (Esc): the async ``kill_process_tree``
    cannot reliably run to completion while the awaiting task is itself being
    cancelled (its ``await asyncio.sleep`` re-raises CancelledError), so this sends
    one immediate SIGTERM with no await. Without it, ``start_new_session=True``
    leaves the child orphaned in its own process group and it keeps running after
    the prompt returns.
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill process and all children via process group.

    Sends SIGTERM first, waits 200ms, then SIGKILL if still alive.
    Matches Gemini CLI's killProcessGroup pattern.
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    await asyncio.sleep(0.2)
    if proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
