"""Functional tests for TaskStorage and TaskRunner description field.

Validates that TaskStorage.create() writes description as a top-level key and
that TaskRunner.list_tasks() surfaces it in the returned dicts. Old tasks on
disk without a top-level description degrade gracefully to empty string.
"""

import pytest
from pathlib import Path

from co_cli.tools._background import TaskStorage, TaskRunner


def test_task_storage_create_writes_description(tmp_path: Path) -> None:
    storage = TaskStorage(base_dir=tmp_path)
    approval_record = {"description": "run linter", "command": "pytest"}
    meta = storage.create("task_test_desc", "pytest", "/tmp", approval_record)

    assert meta["description"] == "run linter"

    tasks = storage.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["description"] == "run linter"


def test_task_storage_create_no_approval_record_defaults_to_empty(tmp_path: Path) -> None:
    storage = TaskStorage(base_dir=tmp_path)
    meta = storage.create("task_test_no_desc", "echo hi", "/tmp", None)

    assert meta["description"] == ""

    tasks = storage.list_tasks()
    assert tasks[0].get("description", "") == ""


def test_task_storage_create_approval_record_without_description_defaults_to_empty(tmp_path: Path) -> None:
    storage = TaskStorage(base_dir=tmp_path)
    approval_record = {"command": "pytest"}
    meta = storage.create("task_test_no_desc_key", "pytest", "/tmp", approval_record)

    assert meta["description"] == ""


def test_task_runner_list_tasks_includes_description(tmp_path: Path) -> None:
    storage = TaskStorage(base_dir=tmp_path)
    runner = TaskRunner(storage, auto_cleanup=False)
    approval_record = {"description": "run linter", "command": "pytest"}
    storage.create("task_test_runner_desc", "pytest", "/tmp", approval_record)

    tasks = runner.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["description"] == "run linter"
