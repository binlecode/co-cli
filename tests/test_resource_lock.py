"""Functional tests for per-resource fail-fast locking."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.files.write import file_patch
from co_cli.tools.resource_lock import ResourceBusyError, ResourceLockStore
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_ctx(tmp_path: Path) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        workspace_root=tmp_path,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# --- ResourceLockStore unit behavior ---


@pytest.mark.asyncio
async def test_lock_store_acquire_and_release():
    """Lock can be acquired, and after release another acquire succeeds."""
    store = ResourceLockStore()
    async with store.try_acquire("key-a"):
        pass
    # After release, re-acquire should succeed
    async with store.try_acquire("key-a"):
        pass


@pytest.mark.asyncio
async def test_lock_store_contention_raises():
    """Concurrent acquire on same key raises ResourceBusyError."""
    store = ResourceLockStore()
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with store.try_acquire("contested"):
            acquired.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await acquired.wait()

    with pytest.raises(ResourceBusyError):
        async with store.try_acquire("contested"):
            pass

    release.set()
    await task


@pytest.mark.asyncio
async def test_lock_store_different_keys_no_contention():
    """Different keys do not contend."""
    store = ResourceLockStore()
    results = []

    async def acquire_key(key: str):
        async with store.try_acquire(key):
            results.append(key)

    await asyncio.gather(acquire_key("a"), acquire_key("b"))
    assert set(results) == {"a", "b"}


# --- patch integration ---


@pytest.mark.asyncio
async def test_patch_same_path_contention(tmp_path: Path):
    """Two concurrent patch on the same path: first succeeds, second gets tool error."""
    ctx = _make_ctx(tmp_path)
    test_file = tmp_path / "target.txt"
    test_file.write_text("hello world", encoding="utf-8")
    # Register mtime so the read-before-write guard passes — this test exercises lock contention
    ctx.deps.file_read_mtimes[str(test_file)] = test_file.stat().st_mtime

    acquired = asyncio.Event()
    release = asyncio.Event()

    # Hold the lock on the file path manually
    async def hold_lock():
        async with ctx.deps.resource_locks.try_acquire(str(test_file)):
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await acquired.wait()

    # patch should fail with tool error (lock held)
    result = await file_patch(ctx, "target.txt", "hello", "goodbye")
    assert result.metadata.get("error") is True
    assert "being modified" in result.return_value

    # File should be unchanged (patch didn't happen)
    assert test_file.read_text() == "hello world"

    release.set()
    await task


@pytest.mark.asyncio
async def test_patch_different_paths_no_contention(tmp_path: Path):
    """Two concurrent patch on different paths: both succeed."""
    ctx = _make_ctx(tmp_path)
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.txt"
    file_a.write_text("aaa", encoding="utf-8")
    file_b.write_text("bbb", encoding="utf-8")
    # Register mtimes so the read-before-write staleness guard passes
    ctx.deps.file_read_mtimes[str(file_a)] = file_a.stat().st_mtime
    ctx.deps.file_read_mtimes[str(file_b)] = file_b.stat().st_mtime

    result_a, result_b = await asyncio.gather(
        file_patch(ctx, "a.txt", "aaa", "AAA"),
        file_patch(ctx, "b.txt", "bbb", "BBB"),
    )

    assert result_a.metadata.get("error") is not True
    assert result_b.metadata.get("error") is not True
    assert file_a.read_text() == "AAA"
    assert file_b.read_text() == "BBB"
