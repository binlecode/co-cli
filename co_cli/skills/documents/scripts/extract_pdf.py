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
import tempfile
from pathlib import Path

SCANNED_SENTINEL = "[no-text-layer: likely scanned]"
MIN_CHARS_PER_PAGE = 10

# Raster render defaults for the scanned-PDF (tier-2) path. 150 DPI is the measured
# cost/quality knee on the configured vision model (reads 8pt cleanly at half the
# token cost of 200). The long-edge clamp matches the model's ~4 MP downsample
# ceiling, so a large-format page can never waste tokens or breach image_view's cap.
# The page cap bounds sequential per-page vision latency; the truncation notice
# (reported via total_pages) is the real safeguard, not the number.
RENDER_DPI = 150
RENDER_MAX_LONG_EDGE_PX = 2000
RENDER_DEFAULT_MAX_PAGES = 10


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


def _effective_dpi(page) -> int:
    """DPI to render this page at, clamped so the long edge stays under the px cap.

    pymupdf renders at ``points * dpi / 72`` px. A normal page renders at RENDER_DPI;
    a large-format page is scaled down so the long edge never exceeds
    RENDER_MAX_LONG_EDGE_PX (the model's ~4 MP downsample ceiling) — rendering above
    that wastes tokens and risks image_view's size cap.
    """
    long_edge_points = max(page.rect.width, page.rect.height)
    if long_edge_points <= 0:
        return RENDER_DPI
    dpi_cap = RENDER_MAX_LONG_EDGE_PX * 72.0 / long_edge_points
    return min(RENDER_DPI, round(dpi_cap))


def _render_pages(path: Path, pages: list[int], outdir: str | None, max_pages: int) -> int:
    """Rasterize the selected pages to PNGs and emit the page->path map on stdout.

    Writes at most ``max_pages`` PNGs into ``outdir`` (an OS tempdir is created when
    None — script-owned, USER_DIR-independent). Stdout contract: one
    ``<1-based-page>\\t<absolute-png-path>`` line per rendered page, then a final
    ``total_pages=M`` line where M is the count of selected pages *before* the cap, so
    a caller detects truncation when the rendered-line count is below M.
    """
    import pymupdf

    target_dir = Path(outdir) if outdir else Path(tempfile.mkdtemp(prefix="co-extract-pdf-"))
    target_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(path)
    try:
        lines: list[str] = []
        for page_index in pages[:max_pages]:
            page = doc[page_index]
            pixmap = page.get_pixmap(dpi=_effective_dpi(page))
            page_number = page_index + 1
            png_path = target_dir / f"page-{page_number:03d}.png"
            pixmap.save(str(png_path))
            lines.append(f"{page_number}\t{png_path.resolve()}")
    finally:
        doc.close()

    body = "".join(f"{line}\n" for line in lines)
    sys.stdout.write(body + f"total_pages={len(pages)}\n")
    return 0


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
    parser.add_argument(
        "--render",
        action="store_true",
        help="rasterize pages to PNGs for the scanned/vision path instead of extracting "
        "text; writes a page->path map to stdout",
    )
    parser.add_argument(
        "--outdir",
        default=None,
        metavar="DIR",
        help="render mode: directory to write PNGs into; an OS tempdir is created when omitted",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=RENDER_DEFAULT_MAX_PAGES,
        help=f"render mode: cap rendered pages (default {RENDER_DEFAULT_MAX_PAGES})",
    )
    args = parser.parse_args()

    path = Path(args.path)
    invalid = _validate_source(path, args.path)
    if invalid:
        return invalid

    if args.render:
        import pymupdf

        doc = pymupdf.open(path)
        try:
            pages = (
                _parse_pages(args.pages, doc.page_count)
                if args.pages is not None
                else list(range(doc.page_count))
            )
        except ValueError as error:
            return _fail(f"Invalid --pages value {args.pages!r}: {error}")
        finally:
            doc.close()
        return _render_pages(path, pages, args.outdir, args.max_pages)

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
