# Tier-2 — Scanned / image-only PDF reading (render → `image_view` → vision model)

Task type: code + skill content

> **Both dependencies now shipped — plan is buildable (G1 re-review 2026-06-11).** Depends on
> **(a)** the vision plan `2026-05-28-150239-vision-input.md` (`image_view` tool — reviewed PASS,
> shipping) and **(b)** the documents plan `2026-06-09-093734-skill-documents.md` (shipped v0.8.340;
> `extract_pdf.py` + its scanned-detection seam, Constraint 7 — verified in code below). Both
> interfaces are now concrete source, not planned.
>
> **⚠ Vision shipped with a simplified design — reconcile before building.** vision-input dropped the
> describe-fallback and the `vision_model` config: vision is now the **agent model's own capability or
> nothing** (`deps.agent_vision_capable`; `image_view` self-hides via `check_fn=_vision_available`,
> `co_cli/tools/vision/view.py:43-56`). Every "text-description fallback" / "no `vision_model`"
> reference in this plan has been corrected to "text-only agent model"; the honest-degradation gate
> (Constraint 2) is unchanged in effect — it simply rides `image_view`'s single capability bool, with
> no second escape hatch.

## Context

co's document stack is three tiers, cheapest-first:
- **Tier 1 — born-digital PDF text layer** (`documents` skill, pymupdf4llm → markdown with
  `## Page N` citations). Most PDFs hit here. Cheap, exact, no vision tokens.
- **Tier 2 — scanned / image-only PDF** (this plan): no text layer to extract, so render each
  page to an image and let a **vision-capable model** read the pixels.
- **(Rejected) heavy local OCR** (marker-pdf / tesseract, ~3–5 GB PyTorch) — co's strategy is
  render→vision (openclaw's pattern), not a bundled OCR engine (hermes ships marker opt-in; co
  declines). Peer-verified this session.

Tier 1 already surfaces the handoff: `extract_pdf.py` signals a scanned PDF as **exit 0 + the
sentinel stdout line `[no-text-layer: likely scanned]`** (documents plan Constraint 7's pinned
contract) instead of returning blank stdout — deliberately exit 0 so it is distinguishable from
the non-zero error exits this plan reuses for corrupt/encrypted files. Tier 2 keys off that
sentinel. Vision input already has a home: `image_view(path, prompt)` (vision plan)
reads a local image and returns real pixels to the vision-capable agent model via
`ToolReturn.content` (the sole path — no describe-fallback in the shipped tool); vision
Constraint 5 names this tier as the downstream consumer (tier 2 renders pages to PNGs and feeds
them **through** `image_view`; `image_view` stays image-only).

This plan is the thin glue between those two seams: **render + route**, no new tool, no new
skill, no new model-visible surface. Tier 2 lives **in the `documents` skill** — it is the same
user intent ("read this PDF") routed off the tier-1 scanned signal, not a separate capability;
making it a third skill would fragment one PDF-reading capability and duplicate the
locate/answer scaffolding. (The `office` sibling is a different *backend* for different
*formats*; tier 2 is a different *path* for the *same* format the documents skill already owns.)

### Verified current state (source-read 2026-06-10; re-verified against shipped code 2026-06-11)

| Claim | Verified | Cite |
|---|---|---|
| Configured default Ollama model `qwen3.6:35b-a3b-agentic` reports `vision` capability | ✓ | `ollama show` / `/api/show` → `['completion','vision','tools','thinking']` (this session) |
| `image_view(path, prompt)` is local-image-only; returns pixels via `ToolReturn.content`; PDFs rejected → documents skill | ✓ (shipped) | `co_cli/tools/vision/view.py:86-112` (media-type allowlist; raw `ToolReturn(content=[prompt, BinaryContent])`; PDF → "use the documents skill") |
| `image_view` is DEFERRED + capability-gated (`check_fn=_vision_available`); honest gate on blind models; **no describe-fallback / no `vision_model`** (vision = `deps.agent_vision_capable` or nothing) | ✓ (shipped) | `view.py:43-56`; gate-on-reveal `tools/system/tool_view.py:91-97`; flag `deps.py:302` |
| `extract_pdf.py` signals a scanned PDF as **exit 0 + the sentinel stdout line `[no-text-layer: likely scanned]`** (distinct from the non-zero error codes this plan reuses) on empty text layer | ✓ (shipped) | `co_cli/skills/documents/scripts/extract_pdf.py:20` (`SCANNED_SENTINEL`) |
| `pymupdf` is already a dependency (via the documents plan) and is imported directly there | ✓ (shipped) | `pyproject.toml:28` |
| pymupdf renders pages to raster via `page.get_pixmap(dpi=…).save(png)` | ✓ | pymupdf API (Page.get_pixmap / Pixmap.save) |
| Rendering writes image files — `image_view` reads a local path, so pages must be materialized | ✓ | vision plan Constraint 4/5 (local-path read) |
| **Raster `--render` mode is NOT yet built** — `extract_pdf.py`'s existing `_render` is text→markdown (`## Page N`), not page-raster; TASK-1 is greenfield, no conflict | ✓ (shipped) | `extract_pdf.py:123-133` (`_render(chunks, pages)` text builder); no `get_pixmap` present |
| The script is invoked as the `co-extract-pdf` console entry point (`[project.scripts]`), **not** `uv run python -m` — `shell_exec`'s allowlist spawn env (`PATH` only, no `VIRTUAL_ENV`/`PYTHONPATH`) + user-`cwd` cannot resolve `co_cli` via `uv run`; `--render` is a flag on that same command | ✓ (shipped) | `pyproject.toml:33` (`co-extract-pdf = "...extract_pdf:main"`) |

## Problem & Outcome

**Problem.** A scanned PDF (photographed contract, receipt, handout — no text layer) is
unreadable today. Tier 1 correctly detects "no extractable text" but has nowhere to route it;
the user hits a dead end even though the configured model can see images.

**Failure cost:** the assistant detects a scanned PDF, knows it can't extract text, and — absent
this tier — either answers from a blank extraction (worst: silent wrong answer) or flatly gives
up, despite a vision-capable model sitting right there. The capability gap is purely the missing
render-and-route glue.

**Outcome.** When tier 1 reports a scanned PDF and the resolved model is vision-capable, the
`documents` skill renders the (capped) pages to local PNGs and reads them through `image_view`
**one page at a time**, extracting each page's relevant content as **text** and carrying that
running text answer forward, with **page-level grounding** ("page 2 shows…"). The per-page read
is incremental by necessity (see Constraint 9): the vision plan's history processor keeps only
the most-recent page's pixels in view, so the persistent state across pages is the accumulated
*text*, not the images. When no vision-capable model is available, it degrades honestly — names
the cause and suggests `web_fetch` (if a URL) or converting the file — and never fakes a read.

## Scope

### In scope
- `co_cli/skills/documents/scripts/extract_pdf.py` — add a `--render` mode (reuses the pymupdf
  handle it already opens for `needs_pass`/`page_count`): rasterize the selected pages to PNGs in
  a temp dir at a bounded DPI, write the page→path map to stdout, honor a page cap. Invoked as
  `co-extract-pdf --render` (the documents-plan console entry point), never `uv run python -m`.
- `co_cli/skills/documents/SKILL.md` — extend the body with the **scanned branch**: on the
  tier-1 scanned signal, render → per-page `image_view` (capped) → synthesize with page grounding;
  honest degradation when `image_view` is unavailable.
- `tests/test_flow_scanned_pdf.py` (new) + a committed **image-only** PDF fixture (a rasterized
  page, no text layer).

### Out of scope
- **Any change to `image_view`** — it stays image-only (vision plan Constraint 5). Tier 2 feeds it
  PNGs; it does not learn about PDFs.
- **A new tool / new skill / `categories.py` change** — rides `shell_exec` (render) + `image_view`.
- **marker-pdf / tesseract / any local OCR engine** — rejected tier (~3–5 GB); co uses render→vision.
- **Office-format scanned content** (`office` skill's problem) and **remote/URL PDFs** (`web_fetch`).
- **A vision sub-agent / multi-image batch tool** — per-page `image_view` calls only, against its
  current single-path interface.
- **Cross-page *visual* reasoning** (e.g. "compare the chart on page 2 with the table on page 7")
  — only one page's pixels are ever in view (Constraint 9). Cross-page answers must come from the
  accumulated per-page *text*, not simultaneous image comparison. A multi-image `image_view` that
  would lift this is a vision-line optimization, not in scope here.
- **Spec entry** — `docs/specs/` updated by `sync-doc` post-delivery.

## Behavioral Constraints
1. **Dependency-gated — now satisfied.** Both the vision plan (`image_view`) and the documents
   plan (`extract_pdf.py` + scanned seam) have shipped; both interfaces are live source. Tasks
   build directly against them.
2. **Honest capability gate (inherited).** If `image_view` is unavailable (text-only agent model —
   there is no `vision_model` escape hatch in the shipped tool), the scanned branch does **not**
   attempt a read — it reports the cause and a workaround. Never render-and-pretend. This rides
   `image_view`'s own single capability gate (`_vision_available` → `deps.agent_vision_capable`),
   not a second one.
3. **Image-only `image_view`.** All PDF parsing/rendering stays in tier 2; `image_view` receives
   only PNG paths.
4. **Bounded cost — explicit, never silent (load-bearing).** Page count and DPI are capped
   (defaults are an Open Question). High-res page images are token-expensive, so a multi-page scan
   must cap pages and **state when it truncated** ("read first N of M pages"). This is the single
   path to a *silent wrong answer* (a 30-page contract capped without saying so) — the truncation
   notice is non-negotiable, not advisory.
5. **Page-level grounding.** Each page image is labeled with its page number when handed to
   `image_view` so the synthesis can cite "page N", mirroring tier-1's `## Page N` contract.
6. **Temp-file lifecycle — script owns the dir, body cleanup is best-effort.** `--render` writes
   PNGs into an **OS tempdir** (`tempfile.mkdtemp`, `USER_DIR`-independent). The skill body
   attempts deletion after `image_view` has read them, but a prompt-driven body cannot guarantee a
   `finally`; on a mid-loop error the dir is left to OS temp reclamation. Residue is bounded and
   non-sensitive (rendered copies of a file the user already has). No co cache dir, no prune
   machinery.
7. **No new hard dependency.** Rendering uses the `pymupdf` already pulled by the documents plan.
8. **Lean, mission-proportionate.** Low-frequency capability — no gold-plating (no batch tool, no
   OCR fallback, no deskew/preprocessing pipeline).
9. **Incremental, tail-page-only visibility (mechanical constraint).** The vision plan's A3
   history processor elides multimodal content from every non-tail `UserPromptPart` on replay, so
   across a per-page loop **only the most-recent page's pixels are ever in the model's view.** The
   scanned branch is therefore an *incremental* read: each page's relevant content is extracted as
   **text** and the running text answer is what persists; pixels do not accumulate. Cross-page
   *visual* reasoning is out of scope (see Scope). This is a hard property of the shared history
   processor, not a tuning choice — the design must not assume the model sees all pages at once.

## High-Level Design

The whole tier is **render + route**, orchestrated by the existing `documents` skill body; the
only new executable code is a render mode on the existing script.

1. **Render mode (`co-extract-pdf --render`)** — `extract_pdf.py` already opens the PDF with
   pymupdf (for `needs_pass` and `page_count`). The `--render <outdir>` flag (on the same
   `co-extract-pdf` console entry point the documents plan establishes — never `uv run python
   -m`) reuses that handle: for each selected page (subject to the page cap),
   `page.get_pixmap(dpi=<DPI>)` → `.save(outdir/page-NNN.png)`, then writes a machine-readable
   page→path map to stdout via `sys.stdout.write` (e.g. `1\t/tmp/.../page-001.png`; not `print`
   — ruff T20, documents Constraint 11). It honors `--pages RANGE` (same parser as text mode)
   and a `--max-pages` cap. Errors (corrupt/encrypted) reuse the existing distinct-message exit
   codes. Still a `shell_exec` subprocess, never imported into the agent.

2. **Skill routing (`documents/SKILL.md` scanned branch)** — the body's Extract step, when tier-1
   extraction returns the **scanned signal**:
   - If `image_view` is unavailable → emit the honest-degradation message (Constraint 2) and stop.
   - Else → run `co-extract-pdf --render <outdir>` to materialize page PNGs (capped), then loop pages:
     `image_view(page_png, prompt="<task> — page N of M of <file>; report what this page shows")`,
     **recording each page's answer as text before moving to the next page.** Only the current
     page's pixels are in view (Constraint 9), so the body accumulates a per-page *text* digest and
     synthesizes the final answer from that digest with page-N attribution. If pages were capped,
     it states so (Constraint 4).

3. **Cost, grounding & the incremental contract** — per-page calls give natural page attribution
   and let the model stop early once the answer is found, but (a) each call ships a full-page image
   (token-heavy → page cap + moderate DPI are the cost levers, OQ1) and (b) pixels do not persist
   across the loop (Constraint 9), so the persistent state is the accumulated per-page text. This
   makes the branch a text-accumulating scan, not a hold-all-pages-in-view read.

No `categories.py`/`toolset.py` change; no new tool in the manifest. The model already has
`image_view` (DEFERRED) and `shell_exec` (ALWAYS); this plan only teaches the `documents` skill
body to chain them on the scanned branch, plus the render mode they chain through.

## Tasks

### ✓ DONE TASK-1 — `--render` mode on `extract_pdf.py` (+ image-only fixture)
- **files:** `co_cli/skills/documents/scripts/extract_pdf.py`, `tests/skills/fixtures/<scanned-sample>.pdf` (new image-only fixture — a rasterized page, no text layer; may be generated programmatically via pymupdf `page.insert_image` and committed)
- **prerequisites:** documents plan shipped (`extract_pdf.py` exists) — met; OQ1 resolved (150 DPI · ≤2,000 px long-edge clamp · 10-page cap · PNG · 200 DPI single-page escalation — measured 2026-06-11, see Open Questions)
- **done_when:** `co-extract-pdf --render <outdir> <fixture>.pdf` writes one PNG per rendered page into `<outdir>` and emits (via `sys.stdout.write`) a **pinned stdout contract** — one `page<TAB>absolute-path` line per rendered page plus a final `total_pages=M` line (so TASK-2 can parse grounding and detect truncation); `--max-pages N` renders at most N while still reporting `total_pages=M`; `--pages 0-1` renders only those pages; a corrupt / password-protected PDF reuses the existing distinct non-zero exit + one-line stderr (no traceback). Verified by invoking the `co-extract-pdf` console command via `subprocess.run` against the committed image-only fixture this task creates (real pymupdf, no mocks).
- **success_signal:** Pointing `--render` at a scanned PDF yields legible per-page PNGs on disk.

### ✓ DONE TASK-2 — `documents` skill scanned branch (render → `image_view` → answer)
- **files:** `co_cli/skills/documents/SKILL.md`
- **prerequisites:** TASK-1; vision plan shipped (`image_view` available)
- **done_when:** the body's Extract step branches on the tier-1 scanned signal to: (a) when
  `image_view` is unavailable, emit the honest-degradation message and stop; (b) when available,
  drive `--render` then loop pages through `image_view` **recording each page's answer as text**
  (per Constraint 9 — not assuming all pages stay in view), capped, and synthesize with page-N
  attribution, stating any page truncation. Verified at the integration boundary: with a
  vision-capable host, asking co to read the committed scanned fixture produces an answer
  referencing the fixture's known on-page content and a page number; the body loads + passes lint
  (B1 clean, security scan empty) and is < 8000 chars. (Positive E2E behind the text-only-host skip
  guard; the always-run check is the unavailable-vision honest-degradation assertion — see TASK-3.)
- **success_signal:** A user points co at a scanned receipt and gets a correct, page-grounded answer.

### ✓ DONE TASK-3 — Tests (render + skill E2E + degradation)
- **files:** `tests/test_flow_scanned_pdf.py` (new) — reuses the image-only fixture created in TASK-1
- **prerequisites:** TASK-1, TASK-2
- **done_when:** `uv run pytest -x tests/test_flow_scanned_pdf.py` passes — render-mode test asserts N PNGs produced + `total_pages=M` truncation reported (real pymupdf, no mocks); the **always-run** check is the unavailable-vision path asserting the honest-degradation message (deterministic, no model); a vision E2E asserts a page-grounded answer, skipped cleanly on text-only hosts per vision-plan test policy. Assertions are behavioral (files produced, answer references known content, exit codes), not structural.
- **success_signal:** N/A (test authoring).

## Testing
Real pymupdf render against a committed image-only PDF fixture (a page rasterized to an image,
no text layer — distinct from the tier-1 born-digital fixture). Vision E2E reuses the vision
plan's pattern: real model, clean **skip** on hosts whose agent model is text-only (gated on
`deps.agent_vision_capable` — no `vision_model` concept); `CO_HOME` temp override;
`noreason_model_settings()`; warmup outside `asyncio.timeout`. Negative/degradation cases are deterministic (no model). Run scoped, fail-fast,
tee'd: `uv run pytest -x tests/test_flow_scanned_pdf.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-scanned-pdf.log`.

## Open Questions
*(All resolved — none blocking. OQ1 closed with a real measurement; OQ2–OQ4 settled in the body.)*

1. **DPI + page-cap defaults — RESOLVED (measured 2026-06-11 on `qwen3.6:35b-a3b-agentic`).**
   A US-Letter text page was rendered at 100/150/200/300 DPI and sent to the live model, reading
   `prompt_eval_count`:

   | DPI | Pixels | Image tokens | Notes |
   |----|--------|-------------|-------|
   | 100 | 850×1100 (0.94 MP) | ~950 | 8pt line read correctly (clean render) |
   | 150 | 1275×1650 (2.10 MP) | ~2,100 | fully resolved, no downsample |
   | 200 | 1700×2200 (3.74 MP) | ~3,700 | at the ceiling edge |
   | 300 | 2550×3300 (8.41 MP) | ~4,060 | **downsampled — pixels wasted** |

   Two measured facts: **(a) the model's downsample ceiling is ~4 MP / ~4,000 image tokens** —
   tokens grow ~1,000 tok/MP up to 200 DPI then flatten 200→300, so Ollama clamps `max_pixels`
   ≈4 MP (~2,000 px long edge); rendering above that is pure waste. This is far above the
   transformers Qwen default (~1.0 MP), so the model does use resolution up to ~4 MP.
   **(b) Token cost is steep and linear** — 150 DPI ≈2,100 tok/page, 200 DPI ≈3,700 (~75% more).
   **Defaults (final):**
   - **DPI = 150** — the cost/quality knee (1650 px long edge, fully resolved, reads 8pt cleanly at
     half the token cost of 200). 200 DPI is an *escalation* for a single page that 150 fails to
     read (small/dense scans), never the default.
   - **Long-edge clamp = ~2,000 px (~4 MP)** — matches the measured ceiling; makes a stray
     `--pages`+DPI combo or a large-format page unable to waste tokens, and keeps each page far
     under `image_view`'s 20 MB cap. `extract_pdf.py --render` computes DPI but clamps the pixmap
     so the long edge never exceeds the cap.
   - **Page cap = 10** — at ~2,100 tok/page with `num_ctx=65536` and only the tail page's pixels
     persisting (Constraint 9), context is not the binding limit; sequential local-turn latency is.
     The truncation notice (Constraint 4) is the real safeguard, not the number.
   - **Format = PNG** (lossless — correct for text/line-art; matches the page-NNN.png contract).

   Caveat for TASK-1: the measurement used a clean vector render; real scans (noise/skew/JPEG
   artifacts) read harder, which is exactly why the default is 150 (not the also-passing 100) with
   a 200 DPI escalation as margin.
2. **Temp-file location + cleanup — RESOLVED.** OS tempdir (`tempfile.mkdtemp`, `USER_DIR`-independent;
   never hardcode `~/.co-cli`), script-owned dir + best-effort body cleanup; residue bounded by OS
   reclaim (Constraint 6). The body deletes the dir after the page loop completes or aborts.
3. **Render mode in `extract_pdf.py` vs a sibling — RESOLVED: reuse `extract_pdf.py`.** It already
   opens the pymupdf handle (`needs_pass`/`page_count`); `--render` reuses it (DRY, one console
   entry point). Scope/Design already commit to this — a sibling `render_pages.py` is rejected.
4. **Per-page `image_view` vs a multi-image interface — RESOLVED: per-page (forced).**
   Single-path `image_view` forces per-page calls, AND the A3 history processor means only the
   tail page's pixels persist (Constraint 9) — so per-page text-accumulation is the *only* correct
   shape today, not merely the cheap one. A batch/multi-image `image_view` (which would also enable
   cross-page visual reasoning) is a vision-line optimization, explicitly out of scope here.

## Final — Team Lead

Plan approved (C2 — both reviewers `Blocking: none`). Core Dev's load-bearing catch (CD-M-1: the
vision plan's A3 history processor leaves only the tail page's pixels in view) is now a
first-class Behavioral Constraint (9) threaded through Outcome / Design / Scope / TASK-2 — the
scanned branch is a per-page text-accumulating read, cross-page *visual* reasoning scoped out.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> **Gates cleared (2026-06-11):** `skill-documents` shipped (v0.8.340); `vision-input` reviewed
> PASS and shipped (v0.8.342). Plan reconciled against shipped code — describe-fallback /
> `vision_model` references corrected to the single `agent_vision_capable` gate; raster `--render`
> confirmed greenfield. **All Open Questions now resolved** — OQ1 (DPI + page cap) closed with a real
> token measurement on `qwen3.6:35b-a3b-agentic` (150 DPI · ≤2,000 px clamp · 10-page cap · PNG).
> Problem and scope unchanged and still correct. **No open prerequisites — ready for PO approval and
> `/orchestrate-dev scanned-pdf-vision`.**

## Gate 1 — PO + TL Approval (2026-06-11)

**APPROVED** (PO + TL). Right problem (scanned PDFs dead-end at the tier-1 sentinel; configured model
is vision-capable), correct scope (thin render+route glue; OCR engine / multi-image / cross-page
visual / new-tool all rejected), proportionate to a Low-frequency capability. Both dependencies
shipped (`skill-documents` v0.8.340, `vision-input` v0.8.342); verified-state table re-checked against
live source; all four Open Questions resolved (OQ1 closed with a measured DPI/token curve on
`qwen3.6:35b-a3b-agentic`). No open prerequisites.

**Watch-items for the dev cycle** (TASK-2 implementation details, covered by TASK-3 tests — not scope
blockers):
1. **Degradation trigger is tool-presence, not a runtime probe.** On a text-only host `image_view`
   self-hides (absent from the toolset), so the body must read "if you do not have `image_view`, emit
   the honest-degradation message" — not "call something to check." The always-run TASK-3 degradation
   assertion guards this; the SKILL.md wording is the risk.
2. **Per-page transcribe-then-advance.** Constraint 9 elides all but the tail page's pixels, so the
   body must make the model record each page's findings as text *before* the next `image_view` call.
   The per-page prompt ("report what this page shows — page N of M") must enforce this ordering.

> Next: `/orchestrate-dev scanned-pdf-vision`.

## Delivery Summary — 2026-06-11

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `co-extract-pdf --render` writes one PNG/page + `total_pages=M`; `--max-pages` caps while reporting M; `--pages` selects; errors reuse exit codes | ✓ pass |
| TASK-2 | `documents` SKILL.md Step 5 scanned branch: honest degradation when `image_view` absent, else render → per-page `image_view` (transcribe-before-advance) → page-N synthesis + truncation notice; body lints clean, < 8000 chars | ✓ pass |
| TASK-3 | `pytest -x tests/test_flow_scanned_pdf.py` green — render+truncation (no mocks), always-run degradation, vision E2E skipped on text-only hosts | ✓ pass |

**Tests:** scoped — 27 passed, 0 failed (`test_flow_scanned_pdf.py` 5 + `test_flow_skill_documents.py` + `test_flow_skill_manifest.py` + `test_flow_skill_lint.py`). Vision E2E ran (host vision-capable): page-3 PNG → model reads "540.00 USD".
**Doc Sync:** fixed — `docs/specs/skills.md` bundled-asset description extended with the `--render` raster mode + scanned/vision tier-2 path. Index in `01-system.md` intact.

**Overall: DELIVERED**
All three tasks pass `done_when`; lint clean; scoped tests green; doc sync clean.

**Implementation notes (for review):**
- **CLI shape deviation (TASK-1):** implemented as `co-extract-pdf --render [--outdir DIR]` (boolean flag + optional dir) rather than the plan's literal `--render <outdir>`. `argparse` `nargs='?'` is genuinely ambiguous with the trailing positional `path` (it eats the PDF path as the dir value), and Constraint 6 requires a no-dir invocation where the **script** owns the `tempfile.mkdtemp` dir. Both needs are met cleanly: body invokes `--render` (script mkdtemp); tests pass `--outdir` for a deterministic location. Capability identical to the plan.
- **New fixture:** `tests/skills/fixtures/scanned_invoice.pdf` — a 3-page image-only PDF (text rasterized and re-inserted as page images; `get_text()` → 0 chars, so it routes via the tier-1 sentinel). Page 3 carries the known total "540.00 USD" for the vision E2E. The pre-existing empty `scanned.pdf` (no image) is untouched.
- **Vision-path cold-start (TASK-3):** the first image read pays a one-time projector/mmproj load (~15–17s) that `ensure_ollama_warm` (text path) does not cover; warm latency is sub-second. Per timeout policy, the warmup `image_view`+`llm_call` runs **outside** the `asyncio.timeout`, so the asserted call stays within `LLM_NON_REASONING_TIMEOUT_SECS` (ran at 0.4s). No timeout constants changed.
- **Minor test overlap:** the always-run degradation test asserts the same `_vision_available`/`tool_view` capability gate that `test_flow_vision` covers, but framed around the documents scanned-branch contract (render works, view is gated) per the plan's explicit TASK-3 requirement and PO watch-item 1.

**Next step:** `/review-impl scanned-pdf-vision`.
