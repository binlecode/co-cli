"""Behavioral tests for the shipped ``co-extract-office`` console entry point.

Invokes the REAL installed command via subprocess against committed Office fixtures
(.docx/.pptx/.xlsx), proving the entry-point wiring (pyproject [project.scripts]) end
to end. No mocks: real mammoth/python-pptx/openpyxl extraction, real subprocess. All
assertions observe stdout/stderr and the exit code only — mirroring the sibling
``test_flow_skill_documents.py`` contract.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_FIXTURES = Path(__file__).parent / "skills" / "fixtures"
_DOCX = _FIXTURES / "sample.docx"
_PPTX = _FIXTURES / "sample.pptx"
_XLSX = _FIXTURES / "sample.xlsx"
_TEXT_PDF = _FIXTURES / "text.pdf"

_HEADING_MARKER = re.compile(r"^# Service Agreement$", re.MULTILINE)
_SLIDE_MARKER = re.compile(r"^## Slide \d+$", re.MULTILINE)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the installed co-extract-office command with the given arguments."""
    return subprocess.run(
        ["co-extract-office", *args],
        capture_output=True,
        text=True,
    )


def test_docx_extracts_with_heading_and_body() -> None:
    """A docx yields the # Service Agreement heading marker and 'termination' body text, exit 0."""
    result = _run(str(_DOCX))
    assert result.returncode == 0, result.stderr
    assert _HEADING_MARKER.search(result.stdout)
    assert "termination" in result.stdout


def test_pptx_extracts_with_slide_markers() -> None:
    """A 2-slide pptx yields a ## Slide N marker and both slides' text, exit 0."""
    result = _run(str(_PPTX))
    assert result.returncode == 0, result.stderr
    assert _SLIDE_MARKER.search(result.stdout)
    assert "Quarterly Deck" in result.stdout
    assert "Risks" in result.stdout


def test_xlsx_extracts_with_sheet_table() -> None:
    """An xlsx yields the ## Sheet Summary marker, a table separator, and both data rows, exit 0."""
    result = _run(str(_XLSX))
    assert result.returncode == 0, result.stderr
    assert "## Sheet Summary" in result.stdout
    assert "| --- |" in result.stdout
    assert "North" in result.stdout
    assert "South" in result.stdout


def test_missing_path_fails() -> None:
    """A non-existent path exits non-zero with a 'File not found' stderr line."""
    result = _run(str(_FIXTURES / "does-not-exist.docx"))
    assert result.returncode != 0
    assert "File not found" in result.stderr


def test_pdf_routed_to_documents_skill() -> None:
    """An existing .pdf exits non-zero, steering the caller to the documents skill."""
    result = _run(str(_TEXT_PDF))
    assert result.returncode != 0
    assert "use the documents skill" in result.stderr


def test_unsupported_extension_fails(tmp_path: Path) -> None:
    """An existing file with an unsupported suffix exits non-zero with 'Unsupported file type'."""
    not_office = tmp_path / "notes.txt"
    not_office.write_text("this is plainly not an office file\n")
    result = _run(str(not_office))
    assert result.returncode != 0
    assert "Unsupported file type" in result.stderr
