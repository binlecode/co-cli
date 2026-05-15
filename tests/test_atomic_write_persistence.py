"""Behavioral tests for atomic_write_text / atomic_write_bytes — co_cli.persistence.atomic.

Verifies: success path replaces file atomically, no .tmp orphans after success,
no .tmp orphans after exception mid-write, parent dirs are created on demand,
errors="replace" survives non-encodable codepoints, and the bytes variant
matches the text contract. Exceptions are induced by real UnicodeEncodeError
(non-ASCII content + ascii encoding) — no fakes.
"""

from pathlib import Path

import pytest

from co_cli.persistence.atomic import atomic_write_bytes, atomic_write_text


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

    The exception is a real UnicodeEncodeError from encoding non-ASCII content
    as ASCII inside tmp.write — no fakes, no monkeypatch.
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


def test_atomic_write_creates_missing_parent_dirs(tmp_path: Path) -> None:
    """atomic_write_text must mkdir(parents=True, exist_ok=True) before writing."""
    target = tmp_path / "deep" / "nested" / "path" / "file.txt"
    assert not target.parent.exists()

    atomic_write_text(target, "deep content")

    assert target.parent.exists()
    assert target.read_text() == "deep content"


def test_atomic_write_text_errors_replace_handles_invalid_codepoints(tmp_path: Path) -> None:
    """errors='replace' lets non-encodable codepoints through with replacement chars.

    Models the tool_io.py spill case: arbitrary subprocess bytes that wouldn't
    survive strict UTF-8. We force a stricter codec (ascii) so a non-ASCII char
    triggers the replacement path without raising.
    """
    target = tmp_path / "lossy.txt"
    content = "ascii-prefix café suffix"

    atomic_write_text(target, content, encoding="ascii", errors="replace")

    written = target.read_text(encoding="ascii")
    assert written.startswith("ascii-prefix ")
    assert written.endswith(" suffix")
    assert "?" in written
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_atomic_write_bytes_happy_path(tmp_path: Path) -> None:
    """Binary variant writes exact bytes and creates missing parent dirs."""
    target = tmp_path / "deep" / "binary.bin"
    payload = bytes(range(256))

    atomic_write_bytes(target, payload)

    assert target.parent.exists()
    assert target.read_bytes() == payload
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
