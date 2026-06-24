---
description: Read, summarize, search, or answer questions about a local Word, PowerPoint, or Excel file on disk (.docx, .pptx, .xlsx). Extracts the document's text — slide by slide, sheet by sheet, or with its headings — so you can quote or summarize it with citations. Use whenever the user points at a .docx/.pptx/.xlsx file or asks what a deck, document, or spreadsheet says or contains. Office formats only — for a PDF (.pdf) use the documents skill instead; for a web page URL use web_fetch.
user-invocable: false
---

# Office — read a local Word / PowerPoint / Excel file

Extract and reason over a **local Office file** so you can summarize it, answer questions about it, or quote it with citations. Office formats only (`.docx`, `.pptx`, `.xlsx`): a PDF belongs to the `documents` skill, and a web URL is `web_fetch`.

## When this fires

The user asks you to read, summarize, search, or answer questions about a Word document, PowerPoint deck, or Excel spreadsheet that lives on disk — e.g. "summarize this deck.pptx", "what does the contract.docx say about termination?", "what's in budget.xlsx?".

## Step 1 — Locate the file

- If the user gave a path, use it.
- Otherwise find it with `file_search` (glob by name or extension, e.g. `*.pptx`). If several match, confirm which one with the user before extracting.
- If Google Drive is configured, `google_drive_search` is an optional extra route. A Drive file already synced to disk is handled here; a raw remote file stays with `google_drive_read` or `web_fetch`.

## Step 2 — Route by source and type

- **A web URL** (http/https), not a local file → use `web_fetch`; this skill's extractor is for local files only.
- **A local `.docx`, `.pptx`, or `.xlsx`** → extract it (Step 3).
- **A local `.pdf`** → this is a PDF, not an Office format. Hand off to the `documents` skill. If that skill is not loaded, tell the user that PDF support lives in the `documents` skill and that this skill handles Office formats only — do **not** try to read the binary with `file_read`.
- **A legacy `.doc`, `.ppt`, or `.xls`** (pre-2007 binary format) → this extractor reads only the modern OOXML formats. Ask the user to re-save the file as `.docx`/`.pptx`/`.xlsx` and then retry.
- **Any other local text file** (`.txt`, `.md`, …) → just use `file_read`; no extraction needed.

## Step 3 — Extract the text

Run the bundled extractor through `shell_exec`:

```
co-extract-office <path-to-file>
```

The command writes markdown to stdout. The structure markers depend on the format — keep them, they are how you cite:

- **PowerPoint (`.pptx`)** → a `## Slide N` marker before each slide (N is the 1-based slide number).
- **Excel (`.xlsx`)** → a `## Sheet <name>` marker before each worksheet, with the sheet rendered as a markdown table. A large sheet is capped: if the output ends with a `[truncated: showing rows 1–N of M]` line, only the first N of M rows were read — say so in your answer and never present a capped read as complete. To raise the cap, pass `--max-rows` (e.g. `co-extract-office <path> --max-rows 5000`).
- **Word (`.docx`)** → the document's own markdown headings (`#`, `##`, …) where the file uses Word heading styles. A document with no heading styles extracts as flat prose with no headings — that is expected; cite by section name or quoted phrase instead.

**Approval:** the first run prompts you to approve running the extractor on the user's file. This is expected — co is about to run code over a local file — and approving once auto-approves the same request for the rest of the session. A user may add `co-extract-office` to `shell.safe_commands` to pre-approve the bare command, but a file outside the workspace root (any path containing `/`, `~`, or `..`) still prompts; that path guard is deliberate, so do not tell the user the opt-in removes the prompt for typical paths.

## Step 4 — Handle the result

- **Normal output** (markdown with the markers above) → answer the user's question and cite the **slide N**, the **sheet name**, or the docx **heading / quoted phrase** the fact came from. Ground every claim in the extracted text — never answer from memory or from a partial / capped extraction; if the extraction is incomplete (e.g. a truncated sheet), say so rather than filling the gap.
- **A non-zero exit with an error line** → relay the cause plainly:
  - `File not found:` → the path is wrong; re-check with `file_search`.
  - `Unsupported file type for office extraction` → not a `.docx`/`.pptx`/`.xlsx`; route per Step 2.
  - `Not an Office file — use the documents skill for PDF` → hand off to the `documents` skill.
  - `Office file is password-protected or encrypted:` → the file is locked; tell the user the password must be removed (re-save without encryption) before it can be read.
  - `Could not open Office file (corrupt or unreadable):` → the file is damaged or not a real OOXML file; tell the user plainly.

## Scope

Local Office reading (`.docx`/`.pptx`/`.xlsx`) via text extraction. No PDF (see the `documents` skill), no legacy `.doc`/`.ppt`/`.xls` (ask the user to re-save as the modern format), no OCR of scanned images embedded in the file, no charts / embedded images / pivot semantics (text and tables only), and no writing or modifying the source file.
