"""Per-resource fail-fast locking for mutation tools.

Prevents concurrent read-modify-write corruption when pydantic-ai dispatches
multiple tool calls in parallel or when parent + delegation agents touch the same resource.

Usage in tools:
    async with ctx.deps.resource_locks.try_acquire(str(resolved_path)):
        content = path.read_text()
        path.write_text(content.replace(old, new))
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ResourceBusyError(Exception):
    """Raised when a resource lock cannot be acquired (already held)."""


class ResourceLockStore:
    """In-process async lock store keyed by resource identifier.

    Locks are lazily created per key and never cleaned up — keys are bounded
    by file count + memory count in a single session, so no leak risk.

    Shared by reference between parent and delegation agents via CoDeps.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def try_acquire(self, key: str) -> AsyncIterator[None]:
        """Non-blocking lock acquisition. Raises ResourceBusyError if held."""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock

        if lock.locked():
            raise ResourceBusyError(
                f"Resource '{key}' is being modified by another tool call — retry next turn"
            )
        async with lock:
            yield
