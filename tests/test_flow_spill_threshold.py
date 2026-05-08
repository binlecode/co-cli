"""Tests for per-call tool result spill threshold."""

from pathlib import Path

from co_cli.tools.tool_io import (
    PERSISTED_OUTPUT_TAG,
    spill_if_oversized,
)


def test_no_spill_below_threshold(tmp_path: Path):
    """Content of 3_999 chars must be returned unchanged — no spill, no PERSISTED_OUTPUT_TAG."""
    content = "x" * 3_999
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_spill_at_threshold(tmp_path: Path):
    """Content of 4_001 chars must trigger a spill and return a stub with PERSISTED_OUTPUT_TAG."""
    content = "x" * 4_001
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
    assert PERSISTED_OUTPUT_TAG in result


def test_spill_large_content(tmp_path: Path):
    """Oversized content spills, returns a stub, and writes the original to disk verbatim."""
    content = "y" * 10_000
    tool_results_dir = tmp_path / "tool_results"
    result = spill_if_oversized(content, tool_results_dir, "file_read")
    assert PERSISTED_OUTPUT_TAG in result
    assert len(result) < len(content), "stub must be smaller than the original"
    spilled_files = list(tool_results_dir.glob("*.txt"))
    assert len(spilled_files) == 1, f"expected one persisted file, found: {spilled_files}"
    assert spilled_files[0].read_text(encoding="utf-8") == content


def test_stub_shape(tmp_path: Path):
    """Spilled stub carries the size preamble, the file_read retrieval hint, and start/end nav."""
    content = "z" * 5_000
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
    assert "This tool result was too large" in result
    assert "file_read" in result, "stub must name the retrieval tool (not 'read_file')"
    assert "start_line" in result
    assert "end_line" in result


def test_force_spill_tiny_content_unchanged(tmp_path: Path):
    """force=True with 200 chars (< TOOL_RESULT_PREVIEW_CHARS=1_500) returns content unchanged."""
    content = "x" * 200
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell", force=True)
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_force_spill_at_preview_size_unchanged(tmp_path: Path):
    """force=True at exactly TOOL_RESULT_PREVIEW_CHARS=1_500 chars returns content unchanged.

    The guard 'len(content) <= TOOL_RESULT_PREVIEW_CHARS' prevents spill when the
    resulting stub would be no smaller than the original content.
    """
    content = "x" * 1_500
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell", force=True)
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_force_spill_above_preview_size_spills(tmp_path: Path):
    """force=True with 1_501 chars (just above TOOL_RESULT_PREVIEW_CHARS) must spill."""
    content = "x" * 1_501
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell", force=True)
    assert PERSISTED_OUTPUT_TAG in result
