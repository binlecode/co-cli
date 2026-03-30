"""Functional tests for background task execution.

Tests exercise real code paths: storage I/O, process spawning, cancellation,
orphan recovery, retention cleanup, and slash command dispatch.
All tests spawn real subprocesses (no mocks).
"""

from __future__ import annotations

import asyncio
import json

from tests._timeouts import SUBPROCESS_TIMEOUT_SECS, SUBPROCESS_START_TIMEOUT_SECS
import time
from pathlib import Path

import pytest

from co_cli.tools._background import TaskRunner, TaskStorage, TaskStatusEnum


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_tasks_dir(tmp_path: Path) -> Path:
    return tmp_path / "tasks"


@pytest.fixture
def storage(tmp_tasks_dir: Path) -> TaskStorage:
    return TaskStorage(tmp_tasks_dir)


@pytest.fixture
def runner(tmp_tasks_dir: Path) -> TaskRunner:
    return TaskRunner(
        storage=TaskStorage(tmp_tasks_dir),
        max_concurrent=5,
        inactivity_timeout=0,
        auto_cleanup=False,
    )


# ---------------------------------------------------------------------------
# TaskStorage
# ---------------------------------------------------------------------------

def test_storage_create_and_read(storage: TaskStorage):
    meta = storage.create("task_test_1", "echo hello", "/tmp")
    assert meta["task_id"] == "task_test_1"
    assert meta["status"] == TaskStatusEnum.pending.value
    assert meta["command"] == "echo hello"

    read_back = storage.read("task_test_1")
    assert read_back["task_id"] == "task_test_1"


def test_storage_update(storage: TaskStorage):
    storage.create("task_update", "sleep 1", "/tmp")
    storage.update("task_update", status=TaskStatusEnum.running.value, pid=12345)
    meta = storage.read("task_update")
    assert meta["status"] == TaskStatusEnum.running.value
    assert meta["pid"] == 12345


def test_storage_list_tasks(storage: TaskStorage):
    storage.create("task_a", "echo a", "/tmp")
    storage.create("task_b", "echo b", "/tmp")
    storage.update("task_b", status=TaskStatusEnum.completed.value)

    all_tasks = storage.list_tasks()
    assert len(all_tasks) == 2

    completed = storage.list_tasks(status_filter=TaskStatusEnum.completed.value)
    assert len(completed) == 1
    assert completed[0]["task_id"] == "task_b"


def test_storage_write_and_read_result(storage: TaskStorage):
    storage.create("task_result", "echo done", "/tmp")
    storage.write_result("task_result", 0, 1.5, "done")
    result = json.loads(storage.result_path("task_result").read_text())
    assert result["exit_code"] == 0
    assert result["duration"] == 1.5
    assert result["summary"] == "done"


def test_storage_tail_output(storage: TaskStorage, tmp_tasks_dir: Path):
    storage.create("task_tail", "echo x", "/tmp")
    out = storage.output_path("task_tail")
    out.write_text("line1\nline2\nline3\nline4\nline5\n")
    lines = storage.tail_output("task_tail", n=3)
    assert lines == ["line3", "line4", "line5"]


def test_storage_sniff_binary_text(storage: TaskStorage):
    storage.create("task_text", "echo x", "/tmp")
    storage.output_path("task_text").write_bytes(b"hello world\nmore text\n")
    assert storage.sniff_binary("task_text") is False


def test_storage_sniff_binary_null_byte(storage: TaskStorage):
    storage.create("task_bin", "echo x", "/tmp")
    storage.output_path("task_bin").write_bytes(b"binary\x00data")
    assert storage.sniff_binary("task_bin") is True


def test_storage_cleanup_old(storage: TaskStorage):
    from datetime import datetime, timezone, timedelta

    storage.create("task_old", "echo x", "/tmp")
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    storage.update("task_old", status=TaskStatusEnum.completed.value, completed_at=old_time)

    storage.create("task_recent", "echo y", "/tmp")
    storage.update("task_recent", status=TaskStatusEnum.completed.value)

    deleted = storage.cleanup_old(retention_days=7)
    assert deleted == 1
    assert not storage.task_dir("task_old").exists()
    assert storage.task_dir("task_recent").exists()


def test_storage_cleanup_skips_running(storage: TaskStorage):
    """Running tasks are never deleted by cleanup."""
    from datetime import datetime, timezone, timedelta

    storage.create("task_run", "sleep 999", "/tmp")
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    storage.update("task_run", status=TaskStatusEnum.running.value, started_at=old_time)

    deleted = storage.cleanup_old(retention_days=1)
    assert deleted == 0
    assert storage.task_dir("task_run").exists()


# ---------------------------------------------------------------------------
# TaskRunner — process spawning and lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_start_and_complete(runner: TaskRunner, storage: TaskStorage):
    """Start a real echo command, wait for completion."""
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        task_id = await runner.start_task("echo hello_world", str(Path.cwd()))
    assert task_id.startswith("task_")

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(50):
            await asyncio.sleep(0.1)
            meta = runner.get_task(task_id)
            if meta and meta["status"] in (TaskStatusEnum.completed.value, TaskStatusEnum.failed.value):
                break

    meta = runner.get_task(task_id)
    assert meta is not None
    assert meta["status"] == TaskStatusEnum.completed.value
    assert meta["exit_code"] == 0

    # Output log should contain "hello_world"
    output_lines = storage.tail_output(task_id, n=20)
    assert any("hello_world" in line for line in output_lines)


@pytest.mark.asyncio
async def test_runner_failed_command(runner: TaskRunner):
    """Non-zero exit sets status=failed."""
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        task_id = await runner.start_task("false", str(Path.cwd()))

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(30):
            await asyncio.sleep(0.1)
            meta = runner.get_task(task_id)
            if meta and meta["status"] == TaskStatusEnum.failed.value:
                break

    meta = runner.get_task(task_id)
    assert meta["status"] == TaskStatusEnum.failed.value
    assert meta["exit_code"] != 0


@pytest.mark.asyncio
async def test_runner_cancel_running_task(runner: TaskRunner):
    """Cancel a running sleep — status becomes cancelled."""
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        task_id = await runner.start_task("sleep 60", str(Path.cwd()))

    # Give process time to start
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        for _ in range(20):
            await asyncio.sleep(0.1)
            meta = runner.get_task(task_id)
            if meta and meta["status"] == TaskStatusEnum.running.value:
                break

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        cancelled = await runner.cancel_task(task_id)
    assert cancelled is True

    meta = runner.get_task(task_id)
    assert meta["status"] == TaskStatusEnum.cancelled.value


@pytest.mark.asyncio
async def test_runner_cancel_nonexistent(runner: TaskRunner):
    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        cancelled = await runner.cancel_task("task_nonexistent_12345")
    assert cancelled is False


@pytest.mark.asyncio
async def test_runner_shutdown_kills_running(tmp_tasks_dir: Path):
    """Shutdown kills live tasks and marks them cancelled."""
    r = TaskRunner(
        storage=TaskStorage(tmp_tasks_dir),
        max_concurrent=5,
        auto_cleanup=False,
    )
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        task_id = await r.start_task("sleep 60", str(Path.cwd()))

    # Give process time to start
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        for _ in range(20):
            await asyncio.sleep(0.1)
            meta = r.get_task(task_id)
            if meta and meta["status"] == TaskStatusEnum.running.value:
                break

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await r.shutdown()

    meta = r.get_task(task_id)
    assert meta["status"] == TaskStatusEnum.cancelled.value


@pytest.mark.asyncio
async def test_runner_concurrency_limit(tmp_tasks_dir: Path):
    """When max_concurrent=1, second task is queued as pending."""
    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        r = TaskRunner(
            storage=TaskStorage(tmp_tasks_dir),
            max_concurrent=1,
            auto_cleanup=False,
        )
        tid1 = await r.start_task("sleep 10", str(Path.cwd()))
        tid2 = await r.start_task("echo queued", str(Path.cwd()))

        # tid1 should be running, tid2 should be pending
        for _ in range(20):
            await asyncio.sleep(0.1)
            meta1 = r.get_task(tid1)
            if meta1 and meta1["status"] == TaskStatusEnum.running.value:
                break

        meta2 = r.get_task(tid2)
        assert meta2["status"] == TaskStatusEnum.pending.value

        await r.shutdown()


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------

def test_orphan_recovery(tmp_tasks_dir: Path):
    """Orphaned running tasks are marked failed on TaskRunner init."""
    s = TaskStorage(tmp_tasks_dir)
    s.create("task_orphan_xyz", "sleep 999", "/tmp")
    s.update("task_orphan_xyz", status=TaskStatusEnum.running.value, pid=99999999)

    # New runner discovers orphan and marks it failed
    TaskRunner(storage=s, auto_cleanup=False)
    meta = s.read("task_orphan_xyz")
    assert meta["status"] == TaskStatusEnum.failed.value
    assert meta["exit_code"] == -1
    result = json.loads(s.result_path("task_orphan_xyz").read_text())
    assert "crash orphan" in result["summary"]


# ---------------------------------------------------------------------------
# Slash command integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slash_background_command(tmp_tasks_dir: Path):
    """Slash /background spawns a task and prints task_id."""
    from co_cli.commands._commands import CommandContext, BUILTIN_COMMANDS
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        runner = TaskRunner(
            storage=TaskStorage(tmp_tasks_dir),
            max_concurrent=5,
            auto_cleanup=False,
        )
        deps = CoDeps(services=CoServices(shell=ShellBackend(), task_runner=runner), config=CoConfig())
        ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

        # Run /background echo test
        await BUILTIN_COMMANDS["background"].handler(ctx, "echo slash_test")

        # At least one task should now exist
        tasks = runner.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["command"] == "echo slash_test"
        await runner.shutdown()


@pytest.mark.asyncio
async def test_slash_tasks_command(tmp_tasks_dir: Path):
    """Slash /tasks lists tasks; filtering by status works."""
    from co_cli.commands._commands import CommandContext, BUILTIN_COMMANDS
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend

    s = TaskStorage(tmp_tasks_dir)
    runner = TaskRunner(storage=s, max_concurrent=5, auto_cleanup=False)
    s.create("task_done_x", "echo done", "/tmp")
    s.update("task_done_x", status=TaskStatusEnum.completed.value)

    deps = CoDeps(services=CoServices(shell=ShellBackend(), task_runner=runner), config=CoConfig())
    ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

    # Should not raise
    await BUILTIN_COMMANDS["tasks"].handler(ctx, "")
    await BUILTIN_COMMANDS["tasks"].handler(ctx, "completed")


@pytest.mark.asyncio
async def test_slash_cancel_command(tmp_tasks_dir: Path):
    """Slash /cancel cancels a running task."""
    from co_cli.commands._commands import CommandContext, BUILTIN_COMMANDS
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        runner = TaskRunner(
            storage=TaskStorage(tmp_tasks_dir),
            max_concurrent=5,
            auto_cleanup=False,
        )
        task_id = await runner.start_task("sleep 60", str(Path.cwd()))

        for _ in range(20):
            await asyncio.sleep(0.1)
            meta = runner.get_task(task_id)
            if meta and meta["status"] == TaskStatusEnum.running.value:
                break

        deps = CoDeps(services=CoServices(shell=ShellBackend(), task_runner=runner), config=CoConfig())
        ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

        await BUILTIN_COMMANDS["cancel"].handler(ctx, task_id)

        meta = runner.get_task(task_id)
        assert meta["status"] == TaskStatusEnum.cancelled.value
        await runner.shutdown()


@pytest.mark.asyncio
async def test_slash_status_with_task_id(tmp_tasks_dir: Path):
    """Slash /status <task_id> displays task metadata without error."""
    from co_cli.commands._commands import CommandContext, BUILTIN_COMMANDS
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend

    s = TaskStorage(tmp_tasks_dir)
    runner = TaskRunner(storage=s, max_concurrent=5, auto_cleanup=False)
    s.create("task_status_test", "echo x", "/tmp")
    s.update("task_status_test", status=TaskStatusEnum.completed.value)

    deps = CoDeps(services=CoServices(shell=ShellBackend(), task_runner=runner), config=CoConfig())
    ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

    # Should not raise
    await BUILTIN_COMMANDS["status"].handler(ctx, "task_status_test")
