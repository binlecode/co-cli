"""Functional tests for workspace checkpoint and rewind."""
import json
import pytest
from pathlib import Path

from co_cli.bootstrap._checkpoint import (
    create_checkpoint,
    list_checkpoints,
    restore_checkpoint,
)


@pytest.fixture
def workspace(tmp_path):
    """A workspace without .git (uses filesystem backend)."""
    (tmp_path / ".co-cli").mkdir()
    return tmp_path


def test_create_checkpoint_filesystem(workspace):
    """create_checkpoint returns an 8-char hex ID with filesystem backend."""
    (workspace / "file.txt").write_text("hello")
    cid = create_checkpoint(workspace, label="test")
    assert len(cid) == 8
    # Index was written
    entries = list_checkpoints(workspace)
    assert any(e["id"] == cid for e in entries)
    assert entries[0]["backend"] == "filesystem"


def test_list_checkpoints_empty(workspace):
    assert list_checkpoints(workspace) == []


def test_list_checkpoints_order(workspace):
    """Most-recent checkpoint is first."""
    (workspace / "f1.txt").write_text("a")
    c1 = create_checkpoint(workspace, label="first")
    c2 = create_checkpoint(workspace, label="second")
    entries = list_checkpoints(workspace)
    assert entries[0]["id"] == c2
    assert entries[1]["id"] == c1


def test_restore_requires_confirmed(workspace):
    """restore_checkpoint raises RuntimeError if confirmed=False."""
    (workspace / "file.txt").write_text("hello")
    cid = create_checkpoint(workspace)
    with pytest.raises(RuntimeError, match="confirmed=True"):
        restore_checkpoint(workspace, cid, confirmed=False)


def test_restore_unknown_checkpoint(workspace):
    """restore_checkpoint raises RuntimeError for unknown checkpoint ID."""
    with pytest.raises(RuntimeError, match="not found"):
        restore_checkpoint(workspace, "deadbeef", confirmed=True)


def test_restore_filesystem(workspace):
    """Filesystem restore puts files back."""
    (workspace / "hello.txt").write_text("original")
    cid = create_checkpoint(workspace, label="snap")
    # Overwrite the file after checkpoint
    (workspace / "hello.txt").write_text("modified")
    restore_checkpoint(workspace, cid, confirmed=True)
    assert (workspace / "hello.txt").read_text() == "original"
