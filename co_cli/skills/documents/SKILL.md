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
- **The single line `[no-text-layer: likely scanned]`** → the PDF has no extractable text layer (it is a scanned image). Do **not** answer from a blank extraction. Go to **Step 5** to read the pages as images.
- **A non-zero exit with an error line** (`File not found:`, `Not a PDF`, `PDF is password-protected:`, `Could not open PDF`) → relay the cause plainly. For a password-protected PDF, tell the user the password must be removed before it can be read.

## Step 5 — Scanned / image-only PDFs (read the pages as images)

Reached only when Step 3 returned `[no-text-layer: likely scanned]`. There is no text to extract, but the pages can be read as images **if your model can see them**.

**If you do not have the `image_view` tool**, your model cannot read images — it is hidden on text-only models. Do not attempt a read and never answer from the blank extraction. Tell the user the PDF is scanned/image-only and that your current model cannot read images, then offer a workaround: if the source was a URL, `web_fetch` may have a text version; otherwise the file needs converting to a text-layer PDF (an OCR step) before this skill can read it.

**If you have `image_view`**, render the pages to images and read them one at a time:

1. **Render** the pages with `shell_exec`:

   ```
   co-extract-pdf --render <path-to-pdf>
   ```

   This rasterizes the pages (150 DPI, at most 10 pages) into a temporary directory and writes, on stdout, one `<page-number>⇥<png-path>` line per rendered page (TAB-separated), then a final `total_pages=M` line. Add `--pages 0-4` to restrict the range. **Check for truncation:** if the count of rendered lines is fewer than `M`, only the first N of M pages were rendered — you must say so in your answer. Never let a capped read look complete.

2. **Read each page in order** with `image_view`, and **write down what the page shows as text before moving to the next page** — only the most recent page's image stays in view, so the running text notes are what you reason over, not the images:

   ```
   image_view(<png-path>, prompt="<the user's question> — this is page N of M of <file>. Transcribe the relevant content and describe what this page shows.")
   ```

3. **Synthesize** the answer from your accumulated per-page notes, citing **page N** for each fact (the same page-number grounding a text PDF gives via `## Page N`). If pages were capped in step 1, state that you read only the first N of M pages.

4. **Clean up:** when finished (or if you stop early), delete the temporary render directory — it is the parent folder of the PNG paths from step 1 — using `shell_exec`. Cleanup is best-effort; if it fails, the OS reclaims the temp files.

## Scope

Local PDF reading. Born-digital PDFs are read via text extraction (Steps 3–4); scanned/image-only PDFs are read via the model's own vision, page by page (Step 5) — there is no bundled OCR engine. No Office formats (see the `office` skill), no writing or modifying the source file.
