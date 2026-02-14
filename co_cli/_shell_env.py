"""Shell infrastructure: environment sanitization and process management."""

import asyncio
import os
import signal


# Allowlist: only these host env vars propagate to subprocess execution.
# Tight by design — only the minimum needed for basic shell commands.
_SAFE_ENV_VARS = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
    "TERM", "SHELL", "TMPDIR", "XDG_RUNTIME_DIR",
}


def restricted_env() -> dict[str, str]:
    """Build a sanitized environment for subprocess execution.

    Uses an allowlist (not blocklist) to prevent pager/editor hijacking
    (CVE-2025-66032 vectors) and shared-library injection.
    """
    base = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_VARS}
    base["PYTHONUNBUFFERED"] = "1"
    # Force safe pagers to block arbitrary code execution via PAGER/GIT_PAGER
    base["PAGER"] = "cat"
    base["GIT_PAGER"] = "cat"
    return base


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
