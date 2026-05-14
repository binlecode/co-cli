"""Behavioral tests for atomic_write_text — the canonical full-overwrite primitive.

Verifies: success path replaces file atomically, no .tmp orphans after success,
no .tmp orphans after exception mid-write (the bug fix piggybacked onto the
promotion from _mutator.atomic_write). Exceptions are induced by real
UnicodeEncodeError (non-ASCII content + ascii encoding) — no fakes.
"""

from pathlib import Path

import pytest

from co_cli.memory.mutator import atomic_write_text


def test_writes_content_atomically_for_new_file(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"

    atomic_write_text(target, "hello")

    assert target.read_text() == "hello"


def test_overwrites_existing_file_with_new_content(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old content")

    atomic_write_text(target, "new content")

    assert target.read_text() == "new content"


def test_cleans_up_temp_file_after_successful_write(tmp_path: Path) -> None:
    target = tmp_path / "ok.txt"

    atomic_write_text(target, "content")

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_cleans_up_temp_file_when_write_raises(tmp_path: Path) -> None:
    """When tmp.write() raises mid-flight, the tempfile must be unlinked.

    The previous _mutator.atomic_write did NOT clean up in this case — a partial
    .tmp file remained on disk. This test guards the fix piggybacked onto the
    promotion to mutator.atomic_write_text. The exception is a real
    UnicodeEncodeError from encoding non-ASCII content as ASCII inside tmp.write.
    """
    target = tmp_path / "fails.txt"

    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "café", encoding="ascii")

    assert not target.exists()
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Orphan tempfiles remain after exception: {tmp_files}"


def test_existing_file_unchanged_when_write_fails(tmp_path: Path) -> None:
    """On exception mid-write, the existing target file remains untouched."""
    target = tmp_path / "preserved.txt"
    target.write_text("original")

    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "new café", encoding="ascii")

    assert target.read_text() == "original"
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
