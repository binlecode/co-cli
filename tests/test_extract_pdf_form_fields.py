"""Behavioral tests for PDF form-field extraction in the pdf skill.

All no-mock: each test builds a real PDF on disk with pymupdf — genuine widget
objects that survive save+reopen — then runs the real ``extract_pdf`` text pipeline
(``_resolve_pages`` -> ``_extract`` -> ``_render``) and asserts on the rendered
markdown. Every page carries >=10 chars of real body text so the text path runs
instead of short-circuiting to the scanned sentinel.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from co_cli.skills.pdf.scripts.extract_pdf import _extract, _render, _resolve_pages

_BODY_TEXT = "Annual Income Tax Return - Section A"


def _build_pdf(path: Path, *, widgets: list[pymupdf.Widget]) -> None:
    """Write a one-page PDF carrying body text plus the given widgets to disk."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), _BODY_TEXT)
    for widget in widgets:
        page.add_widget(widget)
    doc.save(str(path))
    doc.close()


def _text_widget(name: str, value: str) -> pymupdf.Widget:
    widget = pymupdf.Widget()
    widget.field_name = name
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_value = value
    widget.rect = pymupdf.Rect(72, 100, 272, 120)
    return widget


def _unchecked_checkbox(name: str) -> pymupdf.Widget:
    widget = pymupdf.Widget()
    widget.field_name = name
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    widget.field_value = False
    widget.rect = pymupdf.Rect(72, 100, 92, 120)
    return widget


def _render_pdf(path: Path) -> str:
    """Run the real text pipeline end to end and return the rendered markdown."""
    pages, _text_chars, form_fields = _resolve_pages(path, None)
    chunks = _extract(path, pages)
    return _render(chunks, pages, form_fields)


def test_filled_text_field_appears_under_its_page(tmp_path: Path) -> None:
    """A filled text widget's value renders as a Form fields line under its page block."""
    pdf = tmp_path / "filled.pdf"
    _build_pdf(pdf, widgets=[_text_widget("income", "50000")])

    output = _render_pdf(pdf)

    assert "### Form fields" in output
    assert "- income: 50000" in output
    page_marker = output.index("## Page 1")
    form_marker = output.index("### Form fields")
    assert page_marker < form_marker


def test_no_widgets_emits_no_form_subsection(tmp_path: Path) -> None:
    """A PDF with no widgets renders unchanged — the zero-change guarantee."""
    pdf = tmp_path / "no_widgets.pdf"
    _build_pdf(pdf, widgets=[])

    output = _render_pdf(pdf)

    assert "### Form fields" not in output
    assert "## Page 1" in output


def test_unchecked_checkbox_emits_no_form_subsection(tmp_path: Path) -> None:
    """An unchecked checkbox (value "Off") is filtered out — no Form fields subsection."""
    pdf = tmp_path / "unchecked.pdf"
    _build_pdf(pdf, widgets=[_unchecked_checkbox("agree")])

    output = _render_pdf(pdf)

    assert "### Form fields" not in output
