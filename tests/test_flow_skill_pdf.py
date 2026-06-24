"""Behavioral tests for the shipped ``co-extract-pdf`` console entry point.

Invokes the REAL installed command via subprocess against committed PDF fixtures,
proving the entry-point wiring (pyproject [project.scripts]) end to end. No mocks:
real pymupdf4llm extraction, real subprocess. All assertions observe stdout/stderr
and the exit code only — mirroring TASK-2's done_when contract.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from co_cli.skills.pdf.scripts.extract_pdf import SCANNED_SENTINEL

_FIXTURES = Path(__file__).parent / "skills" / "fixtures"
_TEXT_PDF = _FIXTURES / "text.pdf"
_SCANNED_PDF = _FIXTURES / "scanned.pdf"
_PROTECTED_PDF = _FIXTURES / "protected.pdf"

_PAGE_MARKER = re.compile(r"^## Page \d+$", re.MULTILINE)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the installed co-extract-pdf command with the given arguments."""
    return subprocess.run(
        ["co-extract-pdf", *args],
        capture_output=True,
        text=True,
    )


def test_text_pdf_extracts_with_page_markers() -> None:
    """A 2-page text PDF yields both pages' body text and an anchored ## Page N marker, exit 0."""
    result = _run(str(_TEXT_PDF))
    assert result.returncode == 0, result.stderr
    assert "Acme quarterly revenue summary" in result.stdout
    assert "Beta division operating expenses" in result.stdout
    assert _PAGE_MARKER.search(result.stdout)


def test_happy_path_stdout_is_clean_page_markdown() -> None:
    """Happy-path stdout begins with the page marker — nothing precedes the content.

    Extraction (pymupdf4llm/pymupdf-layout) emits parser notices to the C-level
    fd 1; the script silences fd 1 so stdout is page-marked markdown only. If that
    silencing regressed, the chatter would print as a preamble before the first
    marker, so asserting stdout opens with ``## Page 1`` is the functional guard
    (Constraint 7 — nothing pollutes stdout ahead of the content).
    """
    result = _run(str(_TEXT_PDF))
    assert result.returncode == 0, result.stderr
    assert result.stdout.lstrip().startswith("## Page 1")


def test_pages_range_selects_both_pages() -> None:
    """--pages 0-1 on a 2-page doc emits exactly the markers ## Page 1 and ## Page 2, exit 0."""
    result = _run(str(_TEXT_PDF), "--pages", "0-1")
    assert result.returncode == 0, result.stderr
    markers = _PAGE_MARKER.findall(result.stdout)
    assert markers == ["## Page 1", "## Page 2"]


def test_single_page_selection_omits_other_pages() -> None:
    """--pages 0 selects only page 1; the out-of-selection ## Page 2 marker is absent, exit 0."""
    result = _run(str(_TEXT_PDF), "--pages", "0")
    assert result.returncode == 0, result.stderr
    assert re.search(r"^## Page 1$", result.stdout, re.MULTILINE)
    assert not re.search(r"^## Page 2$", result.stdout, re.MULTILINE)


def test_missing_path_fails() -> None:
    """A non-existent path exits non-zero with a 'File not found' stderr line."""
    result = _run(str(_FIXTURES / "does-not-exist.pdf"))
    assert result.returncode != 0
    assert "File not found" in result.stderr


def test_non_pdf_extension_fails(tmp_path: Path) -> None:
    """An existing file with a non-.pdf suffix exits non-zero with a 'Not a PDF' stderr line."""
    not_pdf = tmp_path / "notes.txt"
    not_pdf.write_text("this is plainly not a pdf\n")
    result = _run(str(not_pdf))
    assert result.returncode != 0
    assert "Not a PDF" in result.stderr


def test_corrupt_pdf_fails(tmp_path: Path) -> None:
    """A .pdf file whose bytes are not a valid PDF exits non-zero with 'Could not open PDF'."""
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4 this is not a valid pdf body")
    result = _run(str(corrupt))
    assert result.returncode != 0
    assert "Could not open PDF" in result.stderr


def test_invalid_pages_value_fails() -> None:
    """An out-of-range --pages value exits non-zero with an 'Invalid --pages value' line."""
    result = _run(str(_TEXT_PDF), "--pages", "99")
    assert result.returncode != 0
    assert "Invalid --pages value" in result.stderr


def test_password_protected_pdf_fails() -> None:
    """A password-protected PDF exits non-zero with a 'PDF is password-protected' stderr line."""
    result = _run(str(_PROTECTED_PDF))
    assert result.returncode != 0
    assert "PDF is password-protected" in result.stderr


def test_scanned_pdf_emits_sentinel_and_exits_zero() -> None:
    """An image-only PDF exits 0 with stdout being exactly the scanned sentinel, not blank."""
    result = _run(str(_SCANNED_PDF))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == SCANNED_SENTINEL
    assert result.stdout.strip()
