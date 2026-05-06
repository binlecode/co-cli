"""Tests for per-call tool result spill threshold."""

from pathlib import Path

from co_cli.tools.tool_io import (
    PERSISTED_OUTPUT_TAG,
    SPILL_THRESHOLD_CHARS,
    TOOL_RESULT_PREVIEW_CHARS,
    spill_if_oversized,
)


def test_constants_pinned():
    """SPILL_THRESHOLD_CHARS and TOOL_RESULT_PREVIEW_CHARS must match the documented contract."""
    assert SPILL_THRESHOLD_CHARS == 4_000
    assert TOOL_RESULT_PREVIEW_CHARS == 1_500


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
    """Content of 10_000 chars must spill regardless — well above threshold."""
    content = "y" * 10_000
    result = spill_if_oversized(content, tmp_path / "tool_results", "file_read")
    assert PERSISTED_OUTPUT_TAG in result


def test_stub_contains_opening_line(tmp_path: Path):
    """Spilled stub must contain the 'This tool result was too large' opening line."""
    content = "z" * 5_000
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
    assert "This tool result was too large" in result


def test_stub_references_file_read(tmp_path: Path):
    """Spilled stub must reference 'file_read' (not 'read_file') for navigation."""
    content = "a" * 5_000
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
    assert "file_read" in result


def test_stub_contains_navigation_hint(tmp_path: Path):
    """Spilled stub must contain start_line/end_line navigation hint."""
    content = "b" * 5_000
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
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
