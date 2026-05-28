# Tool Gap Batch 2 — `document_extract` (local PDF → markdown)

Task type: code

## Context

Batch 2 of the ROI-ordered tool-parity gaps
(`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md` §5/§2.1). This is the
**tool half** of the absorbed `2026-05-27-150940-tool-document-handling.md`
(now deleted) — the highest-*value* gap: a personal-assistant CLI that can't
read a local PDF. The **skill half** is the standalone, hard-gated
`2026-05-27-171910-documents-skill.md`.

**Mission + convergence.** Local document handling is the highest-convergence
capability co lacked — 2 of 3 surveyed peers ship it
(`RESEARCH-skills-peers-tiers.md` §2.5 T2-E4: hermes `ocr-and-documents`,
openclaw `nano-pdf`). co reads remote Drive files (`google_drive_read`) and URLs
(`web_fetch`) but has no path to a **local** PDF on disk.

### Hermes parity reference (grounded, not copied)

- Hermes does **not** ship a dedicated extraction *tool*. Its
  `skills/productivity/ocr-and-documents/` skill drives `pymupdf` /
  `pymupdf4llm` through `execute_code` (a sandboxed Python runner). The key
  call is `pymupdf4llm.to_markdown(path, pages=pages)`
  (`scripts/extract_pymupdf.py:26`); deps `pip install pymupdf pymupdf4llm`.
- **co diverges deliberately: a first-class read-only tool, not skill+exec.**
  Grounding: co's `code_execute` is `DEFERRED` and not approval-friendly for a
  routine doc read; a dedicated `is_read_only=True` tool gets co's spill,
  workspace path-guard, and no-approval ergonomics for free, and is callable
  without loading a skill. We adopt hermes's *library and the `to_markdown`
  call*, not its delivery mechanism. (For URLs, the existing `web_fetch` already
  covers the "PDF has a URL" path hermes's skill recommends first.)

### Verified current state (2026-05-27)

- No `pdf`/`ocr`/`pymupdf`/`markitdown` dependency in `pyproject.toml`.
- `file_read` (`co_cli/tools/files/read.py`) rejects binary
  (`UnicodeDecodeError` → "Binary file — cannot display as text"); a PDF is
  unreadable today.
- `enforce_workspace_boundary(path, workspace_dir)`
  (`co_cli/tools/files/fs_guards.py`) confines paths to `workspace_dir` and
  raises `ValueError: Path escapes workspace` otherwise — it is a sandbox, not
  an allowed-roots check.
- Registration is `import … # noqa: F401` in `co_cli/agent/toolset.py`
  (decorator self-registers into `TOOL_REGISTRY`). `PATH_NORMALIZATION_TOOLS` +
  `FILE_TOOLS` in `co_cli/tools/categories.py` gate lifecycle path-resolution
  for `path`-taking tools.
- `tool_output()` applies default spill; `file_read` opts out via
  `spill_threshold_chars=math.inf` — **`document_extract` must NOT copy that**
  (it has no per-line cap to self-bound), so default spill applies.

## Problem & Outcome

**Problem.** A user with a PDF/report/slide deck on disk has no way to get its
text into a turn.

**Outcome.**
1. `document_extract(path, max_pages=None)` tool — local PDF → markdown,
   read-only, no approval, default spill.
2. One lean dependency (`pymupdf` + `pymupdf4llm`) pinned in `pyproject.toml`.
3. Behavioral tests against a committed fixture PDF.
4. Spec entry.

This task being green **unblocks** `2026-05-27-171910-documents-skill.md`.

## Scope

### In scope
- `pyproject.toml` (+ `uv.lock`) — add `pymupdf` + `pymupdf4llm`.
- `co_cli/tools/files/document.py` (new) — `document_extract`.
- `co_cli/agent/toolset.py` — `import … # noqa: F401`.
- `co_cli/tools/categories.py` — add `document_extract` to
  `PATH_NORMALIZATION_TOOLS` + `FILE_TOOLS`.
- `docs/specs/tools.md` — `document_extract` entry.
- Tests: `tests/test_flow_document_extract.py` (new) + committed fixture PDF.

### Out of scope
- The `documents` skill — separate plan.
- OCR of scanned/image-only PDFs (needs tesseract bin) — Deferred.
- docx/pptx/xlsx breadth — Deferred.
- Document *writing*/generation.
- Remote fetch (stays with `google_drive_read`/`web_fetch`).

## Behavioural Constraints
1. **Read-only, no approval** — follows `file_read`, not `file_write`
   (`is_read_only=True`, no `approval`).
2. **Path containment** — `enforce_workspace_boundary(path, workspace_dir)`;
   in-workspace-only for v1; out-of-workspace → `tool_error`/`ValueError`
   surfaced cleanly. (A `~/Downloads` PDF is rejected; user copies it in.
   Broadening = substrate decision, deferred — Open Q3.)
3. **Bounded output** — default `tool_output()` spill; **do not** set
   `spill_threshold_chars=math.inf`.
4. **Graceful failure** — missing file, non-PDF suffix, encrypted/corrupt PDF →
   `tool_error()` with a clear message, never a stack trace.
5. **Lean dependency** — `pymupdf`(+`pymupdf4llm`) only; no PyTorch/marker-pdf
   heavy OCR stack (that's the deferred OCR follow-up).

## High-Level Design

### `document_extract`
```python
@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True)
async def document_extract(ctx, path: str, max_pages: int | None = None) -> ToolReturn:
    p = enforce_workspace_boundary(Path(path), Path(ctx.deps.shell.workspace_dir))   # raises -> tool_error
    if p.suffix.lower() != ".pdf":
        return tool_error("document_extract supports .pdf only (v1). For text/markdown use file_read.", ctx=ctx)
    if not p.exists():
        return tool_error(f"No such file: {path}", ctx=ctx)
    try:
        import pymupdf4llm
        pages = list(range(max_pages)) if max_pages else None
        md = await asyncio.to_thread(pymupdf4llm.to_markdown, str(p), pages=pages)   # CPU-bound -> thread
    except Exception as e:                          # encrypted / corrupt
        return tool_error(f"Could not extract '{path}': {e}", ctx=ctx)
    return tool_output(md, ctx=ctx, path=str(p), pages=(max_pages or "all"))
```
- `pymupdf4llm.to_markdown` is the hermes-parity call (markdown w/ page breaks).
- `asyncio.to_thread` keeps the event loop unblocked for large PDFs.
- No `requires_config` (dependency is a hard install). OCR follow-up adds a
  `check_fn` for the tesseract bin.

### Categories wiring
- `document_extract` ∈ `PATH_NORMALIZATION_TOOLS` (lifecycle pre-resolves
  `path` to absolute before exec) **and** `FILE_TOOLS`.
- `is_read_only` is set on the decorator, not in `categories.py`.

## Tasks

### TODO — TASK-1 — Add extraction dependency
Files: `pyproject.toml` (+ `uv.lock`).
Impl: add `pymupdf` and `pymupdf4llm` to `[project].dependencies`; `uv sync`.
**done_when:**
- `uv sync` clean; `uv run python -c "import pymupdf4llm"` succeeds.
- No PyTorch / marker-pdf / heavy ML transitive deps pulled (`uv pip list`
  diff is just pymupdf + pymupdf4llm + their light deps).

### TODO — TASK-2 — `document_extract` tool
Files: `co_cli/tools/files/document.py` (new); `co_cli/agent/toolset.py`
(`import … # noqa: F401`); `co_cli/tools/categories.py` (add to both sets).
Impl: as High-Level Design.
**done_when:**
- Tool appears in the `TOOL_REGISTRY` dump (37 → 38 tools).
- `document_extract(<fixture>.pdf)` returns `tool_output` markdown containing the
  fixture's known text.
- Non-`.pdf` path → `tool_error` (not a raise); missing path → `tool_error`;
  an encrypted/corrupt fixture → `tool_error`, no stack trace.
- Out-of-workspace path → `enforce_workspace_boundary` reject surfaced cleanly.
- Decorator is `is_read_only=True`, no `approval`, **no**
  `spill_threshold_chars=math.inf`; a large PDF spills via the default path.
- First docstring line is a crisp one-line schema description.

### TODO — TASK-3 — Tests
Files: `tests/test_flow_document_extract.py` (new) + a small committed fixture
PDF **inside the test's `workspace_dir`**.
**done_when:**
- Extracts expected text from the fixture (real data, no mocks).
- Asserts: non-PDF error, missing-path error, out-of-workspace reject.
- (If a corrupt-PDF fixture is cheap to commit) asserts clean error on it.

### TODO — TASK-4 — Spec + gate
Files: `docs/specs/tools.md`.
**done_when:** entry documents args (`path`, `max_pages`), read-only status,
supported format (PDF v1), in-workspace boundary; `scripts/quality-gate.sh
full` clean.

## Testing
- `tests/test_flow_document_extract.py` — real `document_extract` against a
  committed fixture PDF (eval/test "real data" rule). The fixture **must** live
  inside the test `workspace_dir` or it hits the boundary reject — the
  out-of-workspace reject is itself an asserted case.

## Open Questions
1. **`pymupdf4llm` vs raw `pymupdf` text** — `pymupdf4llm.to_markdown` gives
   structured markdown (headings/tables) but is a second dep; raw
   `doc[i].get_text()` is leaner but plain text. **Rec:** `pymupdf4llm` for
   markdown fidelity (matches hermes + co's markdown-everywhere convention);
   the extra dep is light.
2. **Tool home** — `files/document.py` vs a new `co_cli/tools/documents/`
   group. **Rec:** `files/` for the single v1 tool; promote to `documents/`
   only if OCR + multi-format land.
3. **Path containment** — in-workspace-only (rejects `~/Downloads`/Drive-sync)
   vs allowed-roots. **Rec:** in-workspace-only for v1; broadening is a
   substrate decision, defer.
4. **`max_pages` semantics** — first-N pages vs a range. **Rec:** first-N
   (`pages=list(range(max_pages))`); rely on spill for large docs rather than a
   hard cap.

## Deferred items
- **OCR (scanned/image PDFs)** — needs tesseract bin; gate via `check_fn` +
  call-time fallback. Follow-up plan (hermes's heavier `marker-pdf` path).
- **docx/pptx/xlsx** — format breadth after PDF proves the shape (a
  `markitdown` swap is the alternative).
- **Document generation** — separate capability.

## Shipping order
Strictly sequential: TASK-1 → TASK-2 → TASK-3 (tool exists + verified) →
TASK-4 gate → **ship the tool**. Landing TASK-2 green is the hard dependency
that unblocks `2026-05-27-171910-documents-skill.md`.
