"""Functional tests for background task execution.

Tests exercise real code paths: storage I/O, process spawning, cancellation,
orphan recovery, retention cleanup, and slash command dispatch.
All tests spawn real subprocesses (no mocks).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from co_cli.background import TaskRunner, TaskStorage, TaskStatus, _make_task_id


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
# _make_task_id
# ---------------------------------------------------------------------------

def test_make_task_id_format():
    tid = _make_task_id("uv run pytest")
    assert tid.startswith("task_")
    parts = tid.split("_")
    # task_YYYYMMDD_HHMMSS_uv → 4 parts minimum
    assert len(parts) >= 4
    assert parts[-1] == "uv"


def test_make_task_id_unsafe_chars():
    tid = _make_task_id("/usr/bin/grep pattern")
    # basename of /usr/bin/grep → grep; no slashes in id
    assert "/" not in tid
    assert tid.endswith("grep")


# ---------------------------------------------------------------------------
# TaskStorage
# ---------------------------------------------------------------------------

def test_storage_create_and_read(storage: TaskStorage):
    meta = storage.create("task_test_1", "echo hello", "/tmp")
    assert meta["task_id"] == "task_test_1"
    assert meta["status"] == TaskStatus.pending.value
    assert meta["command"] == "echo hello"

    read_back = storage.read("task_test_1")
    assert read_back["task_id"] == "task_test_1"


def test_storage_update(storage: TaskStorage):
    storage.create("task_update", "sleep 1", "/tmp")
    storage.update("task_update", status=TaskStatus.running.value, pid=12345)
    meta = storage.read("task_update")
    assert meta["status"] == TaskStatus.running.value
    assert meta["pid"] == 12345


def test_storage_list_tasks(storage: TaskStorage):
    storage.create("task_a", "echo a", "/tmp")
    storage.create("task_b", "echo b", "/tmp")
    storage.update("task_b", status=TaskStatus.completed.value)

    all_tasks = storage.list_tasks()
    assert len(all_tasks) == 2

    completed = storage.list_tasks(status_filter=TaskStatus.completed.value)
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
    storage.update("task_old", status=TaskStatus.completed.value, completed_at=old_time)

    storage.create("task_recent", "echo y", "/tmp")
    storage.update("task_recent", status=TaskStatus.completed.value)

    deleted = storage.cleanup_old(retention_days=7)
    assert deleted == 1
    assert not storage.task_dir("task_old").exists()
    assert storage.task_dir("task_recent").exists()


def test_storage_cleanup_skips_running(storage: TaskStorage):
    """Running tasks are never deleted by cleanup."""
    from datetime import datetime, timezone, timedelta

    storage.create("task_run", "sleep 999", "/tmp")
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    storage.update("task_run", status=TaskStatus.running.value, started_at=old_time)

    deleted = storage.cleanup_old(retention_days=1)
    assert deleted == 0
    assert storage.task_dir("task_run").exists()


# ---------------------------------------------------------------------------
# TaskRunner — process spawning and lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_start_and_complete(runner: TaskRunner):
    """Start a real echo command, wait for completion."""
    task_id = await runner.start_task("echo hello_world", str(Path.cwd()))
    assert task_id.startswith("task_")

    # Poll until completed (max 5s)
    for _ in range(50):
        await asyncio.sleep(0.1)
        meta = runner.get_task(task_id)
        if meta and meta["status"] in (TaskStatus.completed.value, TaskStatus.failed.value):
            break

    meta = runner.get_task(task_id)
    assert meta is not None
    assert meta["status"] == TaskStatus.completed.value
    assert meta["exit_code"] == 0

    # Output log should contain "hello_world"
    output = runner._storage.output_path(task_id).read_text()
    assert "hello_world" in output


@pytest.mark.asyncio
async def test_runner_failed_command(runner: TaskRunner):
    """Non-zero exit sets status=failed."""
    task_id = await runner.start_task("false", str(Path.cwd()))

    for _ in range(30):
        await asyncio.sleep(0.1)
        meta = runner.get_task(task_id)
        if meta and meta["status"] == TaskStatus.failed.value:
            break

    meta = runner.get_task(task_id)
    assert meta["status"] == TaskStatus.failed.value
    assert meta["exit_code"] != 0


@pytest.mark.asyncio
async def test_runner_cancel_running_task(runner: TaskRunner):
    """Cancel a running sleep — status becomes cancelled."""
    task_id = await runner.start_task("sleep 60", str(Path.cwd()))

    # Give process time to start
    for _ in range(20):
        await asyncio.sleep(0.1)
        meta = runner.get_task(task_id)
        if meta and meta["status"] == TaskStatus.running.value:
            break

    cancelled = await runner.cancel_task(task_id)
    assert cancelled is True

    meta = runner.get_task(task_id)
    assert meta["status"] == TaskStatus.cancelled.value


@pytest.mark.asyncio
async def test_runner_cancel_nonexistent(runner: TaskRunner):
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
    task_id = await r.start_task("sleep 60", str(Path.cwd()))

    for _ in range(20):
        await asyncio.sleep(0.1)
        meta = r.get_task(task_id)
        if meta and meta["status"] == TaskStatus.running.value:
            break

    await r.shutdown()

    meta = r.get_task(task_id)
    assert meta["status"] == TaskStatus.cancelled.value


@pytest.mark.asyncio
async def test_runner_concurrency_limit(tmp_tasks_dir: Path):
    """When max_concurrent=1, second task is queued as pending."""
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
        if meta1 and meta1["status"] == TaskStatus.running.value:
            break

    meta2 = r.get_task(tid2)
    assert meta2["status"] == TaskStatus.pending.value

    await r.shutdown()


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------

def test_orphan_recovery(tmp_tasks_dir: Path):
    """Orphaned running tasks are marked failed on TaskRunner init."""
    s = TaskStorage(tmp_tasks_dir)
    s.create("task_orphan_xyz", "sleep 999", "/tmp")
    s.update("task_orphan_xyz", status=TaskStatus.running.value, pid=99999999)

    # New runner discovers orphan and marks it failed
    TaskRunner(storage=s, auto_cleanup=False)
    meta = s.read("task_orphan_xyz")
    assert meta["status"] == TaskStatus.failed.value
    assert meta["exit_code"] == -1
    result = json.loads(s.result_path("task_orphan_xyz").read_text())
    assert "crash orphan" in result["summary"]


# ---------------------------------------------------------------------------
# Slash command integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slash_background_command(tmp_tasks_dir: Path):
    """Slash /background spawns a task and prints task_id."""
    from co_cli._commands import CommandContext, COMMANDS
    from co_cli.deps import CoDeps
    from co_cli.shell_backend import ShellBackend

    runner = TaskRunner(
        storage=TaskStorage(tmp_tasks_dir),
        max_concurrent=5,
        auto_cleanup=False,
    )
    deps = CoDeps(shell=ShellBackend(), task_runner=runner)
    ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

    # Run /background echo test
    await COMMANDS["background"].handler(ctx, "echo slash_test")

    # At least one task should now exist
    tasks = runner.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["command"] == "echo slash_test"
    await runner.shutdown()


@pytest.mark.asyncio
async def test_slash_tasks_command(tmp_tasks_dir: Path):
    """Slash /tasks lists tasks; filtering by status works."""
    from co_cli._commands import CommandContext, COMMANDS
    from co_cli.deps import CoDeps
    from co_cli.shell_backend import ShellBackend

    s = TaskStorage(tmp_tasks_dir)
    runner = TaskRunner(storage=s, max_concurrent=5, auto_cleanup=False)
    s.create("task_done_x", "echo done", "/tmp")
    s.update("task_done_x", status=TaskStatus.completed.value)

    deps = CoDeps(shell=ShellBackend(), task_runner=runner)
    ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

    # Should not raise
    await COMMANDS["tasks"].handler(ctx, "")
    await COMMANDS["tasks"].handler(ctx, "completed")


@pytest.mark.asyncio
async def test_slash_cancel_command(tmp_tasks_dir: Path):
    """Slash /cancel cancels a running task."""
    from co_cli._commands import CommandContext, COMMANDS
    from co_cli.deps import CoDeps
    from co_cli.shell_backend import ShellBackend

    runner = TaskRunner(
        storage=TaskStorage(tmp_tasks_dir),
        max_concurrent=5,
        auto_cleanup=False,
    )
    task_id = await runner.start_task("sleep 60", str(Path.cwd()))

    for _ in range(20):
        await asyncio.sleep(0.1)
        meta = runner.get_task(task_id)
        if meta and meta["status"] == TaskStatus.running.value:
            break

    deps = CoDeps(shell=ShellBackend(), task_runner=runner)
    ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

    await COMMANDS["cancel"].handler(ctx, task_id)

    meta = runner.get_task(task_id)
    assert meta["status"] == TaskStatus.cancelled.value
    await runner.shutdown()


@pytest.mark.asyncio
async def test_slash_status_with_task_id(tmp_tasks_dir: Path):
    """Slash /status <task_id> displays task metadata without error."""
    from co_cli._commands import CommandContext, COMMANDS
    from co_cli.deps import CoDeps
    from co_cli.shell_backend import ShellBackend

    s = TaskStorage(tmp_tasks_dir)
    runner = TaskRunner(storage=s, max_concurrent=5, auto_cleanup=False)
    s.create("task_status_test", "echo x", "/tmp")
    s.update("task_status_test", status=TaskStatus.completed.value)

    deps = CoDeps(shell=ShellBackend(), task_runner=runner)
    ctx = CommandContext(message_history=[], deps=deps, agent=None, tool_names=[])

    # Should not raise
    await COMMANDS["status"].handler(ctx, "task_status_test")
