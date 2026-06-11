---
description: Read, summarize, search, or answer questions about a local PDF file on disk. Extracts the PDF's text with page numbers so you can quote or summarize it with citations. Use whenever the user points at a .pdf file or asks what a PDF says or contains. PDF only — for Word/PowerPoint/Excel (.docx/.pptx/.xlsx) use the office skill instead; for a web page URL use web_fetch.
user-invocable: false
---

# Documents — read a local PDF

Extract and reason over a **local PDF** so you can summarize it, answer questions about it, or quote it with page citations. PDF only: Word/PowerPoint/Excel belong to the `office` skill, and a web URL is `web_fetch`.

## When this fires

The user asks you to read, summarize, search, or answer questions about a PDF that lives on disk — e.g. "summarize this report.pdf", "what does the contract say about termination?", "pull the totals from invoice.pdf".

## Step 1 — Locate the file

- If the user gave a path, use it.
- Otherwise find it with `file_search` (glob by name or extension, e.g. `*.pdf`). If several match, confirm which one with the user before extracting.
- If Google Drive is configured, `google_drive_search` is an optional extra route. A Drive file already synced to disk is handled here; a raw remote file stays with `google_drive_read` or `web_fetch`.

## Step 2 — Route by source and type

- **A web URL** (http/https), not a local file → use `web_fetch`; this skill's extractor is for local files only.
- **A local `.pdf`** → extract it (Step 3).
- **A local `.docx`, `.pptx`, or `.xlsx`** → this is an Office format, not a PDF. Hand off to the `office` skill. If that skill is not loaded, tell the user that Office support lives in the `office` skill and that this skill handles PDFs only — do **not** try to read the binary with `file_read`.
- **Any other local text file** (`.txt`, `.md`, …) → just use `file_read`; no extraction needed.

## Step 3 — Extract the PDF text

Run the bundled extractor through `shell_exec`:

```
co-extract-pdf <path-to-pdf>
```

To limit to specific pages, pass a 0-based range with `--pages` — `co-extract-pdf <path> --pages 0-4` (also accepts `2` or `0-1,3`).

The command writes markdown to stdout with a `## Page N` marker before each page (N is the 1-based page number). Keep those markers — they are how you cite pages in your answer.

**Approval:** the first run prompts you to approve running the extractor on the user's file. This is expected — co is about to run code over a local file — and approving once auto-approves the same request for the rest of the session. A user may add `co-extract-pdf` to `shell.safe_commands` to pre-approve the bare command, but a PDF outside the workspace root (any path containing `/`, `~`, or `..`) still prompts; that path guard is deliberate, so do not tell the user the opt-in removes the prompt for typical paths.

## Step 4 — Handle the result

- **Normal output** (markdown with `## Page N` markers) → answer the user's question and cite **page N** using the markers.
- **The single line `[no-text-layer: likely scanned]`** → the PDF has no extractable text layer (it is a scanned image). Do **not** answer from a blank extraction. Tell the user the PDF appears to be scanned/image-only and that text extraction found nothing. If the source was a URL, `web_fetch` may offer a text version. Reading scanned pages as images is a separate capability not available in this skill.
- **A non-zero exit with an error line** (`File not found:`, `Not a PDF`, `PDF is password-protected:`, `Could not open PDF`) → relay the cause plainly. For a password-protected PDF, tell the user the password must be removed before it can be read.

## Scope

PDF text extraction only. No OCR of scanned pages, no Office formats (see the `office` skill), no writing or modifying files.
