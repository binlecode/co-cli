"""Extract a local PDF to markdown for the `documents` skill.

Reached only as the ``co-extract-pdf`` console entry point through ``shell_exec`` —
never imported into the agent process. Emits markdown with explicit ``## Page N``
markers on stdout for citation; on any error writes a single plain line to stderr
and exits non-zero (no traceback). An image-only/scanned PDF (no text layer) is not
an error: it exits 0 with the single sentinel line so a caller can route to OCR/vision.

Output goes through ``sys.stdout.write``/``sys.stderr.write`` (not ``print``) because
this module lives under ``co_cli/`` and ruff T20 forbids ``print`` there.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCANNED_SENTINEL = "[no-text-layer: likely scanned]"
MIN_CHARS_PER_PAGE = 10


def _parse_pages(spec: str, page_count: int) -> list[int]:
    """Parse a page spec like ``0-4``, ``2``, ``0-1,3`` into a sorted 0-based list.

    Raises ValueError on malformed syntax or an out-of-range page.
    """
    pages: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"range start after end: {part!r}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise ValueError("no pages specified")
    for page in sorted(pages):
        if page < 0 or page >= page_count:
            raise ValueError(f"page {page} out of range (document has {page_count} pages)")
    return sorted(pages)


def _fail(message: str) -> int:
    """Write a one-line message to stderr and return a non-zero exit code."""
    sys.stderr.write(message + "\n")
    return 1


def _validate_source(path: Path, raw: str) -> int:
    """Return 0 if the path is an existing readable unprotected PDF, else non-zero via _fail.

    Distinguishes missing file, non-PDF extension, corrupt/unreadable, and
    password-protected — each its own one-line message (Constraint 6).
    """
    import pymupdf

    if not path.exists():
        return _fail(f"File not found: {raw}")
    if path.suffix.lower() != ".pdf":
        return _fail(f"Not a PDF (expected a .pdf file): {raw}")
    try:
        doc = pymupdf.open(path)
    except Exception:
        return _fail(f"Could not open PDF (corrupt or unreadable): {raw}")
    try:
        if doc.needs_pass:
            return _fail(f"PDF is password-protected: {raw}")
    finally:
        doc.close()
    return 0


def _resolve_pages(path: Path, spec: str | None) -> tuple[list[int], int]:
    """Return the selected 0-based page list and the document's text-layer char count.

    The text-layer count comes from pymupdf's raw ``get_text`` over the selected
    pages — the actual embedded text, independent of pymupdf4llm's markdown image
    placeholders — so Constraint 7's scanned detection cannot be fooled by them.
    Raises ValueError on a malformed --pages spec.
    """
    import pymupdf

    doc = pymupdf.open(path)
    try:
        pages = (
            _parse_pages(spec, doc.page_count) if spec is not None else list(range(doc.page_count))
        )
        text_chars = sum(len(doc[page].get_text().strip()) for page in pages)
    finally:
        doc.close()
    return pages, text_chars


def _extract(path: Path, pages: list[int]) -> list[dict]:
    """Run pymupdf4llm with OS-level stdout silenced.

    pymupdf-layout writes parser notices ("Using Tesseract...") to the C-level
    file descriptor 1, which Python's redirect_stdout cannot capture — so we
    temporarily point fd 1 at os.devnull around the call to keep our own stdout clean.
    """
    import pymupdf4llm

    saved_fd = os.dup(1)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        sys.stdout.flush()
        os.dup2(devnull_fd, 1)
        return pymupdf4llm.to_markdown(str(path), pages=pages, page_chunks=True)
    finally:
        sys.stdout.flush()
        os.dup2(saved_fd, 1)
        os.close(devnull_fd)
        os.close(saved_fd)


def _render(chunks: list[dict], pages: list[int]) -> str:
    """Build page-marked markdown. ``## Page N`` is 1-based (what a PDF viewer shows)."""
    parts: list[str] = []
    for index, chunk in enumerate(chunks):
        text = chunk.get("text", "").strip()
        page_zero_based = chunk.get("metadata", {}).get("page")
        if page_zero_based is None:
            page_zero_based = pages[index] if index < len(pages) else index
        parts.append(f"## Page {page_zero_based + 1}\n\n{text}")
    return "\n\n".join(parts)


def main() -> int:
    """Console entry point (``co-extract-pdf``); returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="co-extract-pdf",
        description="Extract a local PDF to markdown with page markers.",
    )
    parser.add_argument("path", help="path to a local .pdf file")
    parser.add_argument(
        "--pages",
        default=None,
        help="page range, 0-based (e.g. '0-4', '2', '0-1,3'); default all pages",
    )
    args = parser.parse_args()

    path = Path(args.path)
    invalid = _validate_source(path, args.path)
    if invalid:
        return invalid

    try:
        pages, text_chars = _resolve_pages(path, args.pages)
    except ValueError as error:
        return _fail(f"Invalid --pages value {args.pages!r}: {error}")

    if not pages or text_chars < MIN_CHARS_PER_PAGE * len(pages):
        sys.stdout.write(SCANNED_SENTINEL + "\n")
        return 0

    try:
        chunks = _extract(path, pages)
    except Exception:
        return _fail(f"Could not extract text from PDF: {args.path}")

    sys.stdout.write(_render(chunks, pages) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
