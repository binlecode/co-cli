# RESEARCH: Document/Office Skill Parity — Prompt + Tooling Layers

Code-first peer comparison of co-cli's **`documents`** and **`office`** skills (the
W4.B subjects) against the equivalent document-handling skills in **hermes-agent**,
**openclaw**, and the **Anthropic-licensed office toolkit** (vendored in hermes).
Scope: the skill *body* (prompt layer) and the *tools backing it* (impl layer) for
reading/handling PDF and Office files — NOT catalog breadth (that is
`RESEARCH-skills-peers-tiers.md`).

**Sources (all file:line-grounded):**
- co-cli: `co_cli/skills/{documents,office}/SKILL.md`, `.../scripts/extract_pdf.py`,
  `.../scripts/extract_office.py`, `co_cli/skills/manifest.py`, `co_cli/skills/lint.py`.
- hermes-agent: `skills/productivity/{ocr-and-documents,powerpoint,nano-pdf}/`,
  `optional-skills/finance/excel-author/`.
- Anthropic: `hermes-agent/skills/productivity/powerpoint/scripts/office/` (vendored,
  `LICENSE.txt:1` © Anthropic PBC — proprietary; reference only).
- openclaw: `skills/nano-pdf/`, `src/agents/tools/pdf-tool.ts`,
  `extensions/document-extract/document-extractor.ts`.

---

## 1. co-cli current state (the baseline)

Both skills are **read-only by deliberate design** (`documents/SKILL.md:75`,
`office/SKILL.md:56`). Architecture: standalone console-script extractors invoked via
`shell_exec` behind an approval gate — never imported into the agent process.

| Skill | Reads | Backend | Structure markers | Notable |
|---|---|---|---|---|
| `documents` | local `.pdf` | pymupdf4llm (`extract_pdf.py`) | `## Page N` | scanned-detection *in the extractor* (`extract_pdf.py:251`), password detection (`:84`), model-vision fallback render path (`--render`, 150 DPI, DPI-clamped, page-cap + `total_pages` truncation contract) |
| `office` | local `.docx/.pptx/.xlsx` | mammoth / python-pptx / openpyxl | `## Slide N` / `## Sheet <name>` (md table) / docx headings | CFB-magic password sniff (`extract_office.py:58`), per-sheet row cap + explicit truncation line (`:130`) |

**Read-axis verdict: co is at or ahead of every peer.** openclaw has *no* Office
extraction (`document-extractor.ts:110` is PDF-only); hermes/Anthropic read PDFs the
same way co does (pymupdf4llm / markitdown). co's error typing (distinct
password/corrupt/not-a-PDF messages) and truncation contracts are more disciplined
than openclaw's extractor.

---

## 2. Peer borrowing points (raw findings)

### hermes-agent
- **`ocr-and-documents`** — two-tier extractor routing with a capability matrix
  (`SKILL.md:35-52`); a real **OCR engine** (marker-pdf, `extract_marker.py`) for
  scanned/equation/form pages; disk-budget guard with a ready-to-speak user message
  (`:54-55`); inline split/merge/search pymupdf recipes (`:126-160`); flagged
  extractor `extract_pymupdf.py` with `--tables/--images/--metadata/--pages`.
- **`powerpoint`** — full create/**edit** via OOXML unpack→edit-XML→clean→pack
  (`editing.md:1-42`); mandatory **adversarial visual-QA loop** with a fresh-eyes
  subagent + 12-point checklist + placeholder-leak grep gate (`SKILL.md:146-208`);
  design-quality rubric incl. "AI-slide tells to avoid" (`:57-143`).
- **`excel-author`** (optional) — openpyxl authoring; the **`recalc.py` trap**
  (`scripts/recalc.py:1-40`): openpyxl writes formula *strings* but never computes
  them, so a LibreOffice-headless recalc must run before delivery; produced-artifact
  contract (`./out/<name>`, return path); "layout-before-formulas" discipline; an
  explicit **"when NOT to use"** section (`SKILL.md:236-240`).
- **`nano-pdf`** — NL PDF text edit via an external LLM-backed CLI + API key.

### Anthropic office toolkit (vendored in hermes, proprietary)
- `scripts/office/pack.py:24-105` — pack unpacked OOXML → file with **XSD schema
  validation + auto-repair**; bundled ISO-IEC-29500/ECMA/Microsoft XSD set;
  `_condense_xml` whitespace normalizer; markitdown as the universal read front door;
  LibreOffice-headless → `pdftoppm` render-for-QA; pptxgenjs for create-from-scratch.
  Word redlining helpers (`simplify_redlines.py`, `merge_runs.py`) — tracked-changes
  editing primitives.

### openclaw
- `pdf-tool.ts:204-247` — **capability-aware routing**: native-provider PDF ingest
  (raw bytes to Anthropic/Gemini) vs extract-fallback, decided per model capability;
  text-vs-image downgrade as a deterministic code branch.
- `document-extractor.ts:82-100` — text/image threshold *in the extractor* with
  `forms:true` (render form-field overlays); `PDF_MIN_TEXT_CHARS=200`.
- multi-PDF batching with a cap + labeled `[PDF N]` context (`pdf-tool.ts:60,342-352`);
  tiered byte/page limits (`file-extraction-limits.ts:16-19`).
- `nano-pdf` — same external NL-edit CLI as hermes.

---

## 3. Tiered assessment (after grounding against latest co code)

### Tier A — "borrow now, in read-only scope" — MOSTLY ALREADY COVERED

The headline finding from grounding the borrow against current code:

- **Negative-scope / mutual-exclusivity cue (peers' "when NOT to use" block) is
  already implemented in co — twice.** Skill *selection* (the W4.B concern) reads
  ONLY the `description` field (`manifest.py:31-35`); the body is loaded later via
  `skill_view`. Both descriptions already carry the exclusion cue
  (`documents/SKILL.md:2` "PDF only — for Word/PowerPoint/Excel use the office skill…
  for a web page URL use web_fetch"; mirrored in `office/SKILL.md:2`). In-turn
  mis-routing is already handled by the body's Step-2 routing table
  (`documents/SKILL.md:20-26`). **A body-level "when NOT to use" block would be
  redundant prose, not a hardening win** — dropped on surgical-change grounds.
- **Extraction modes (`--tables/--metadata`)** — low value: pymupdf4llm already
  inlines tables as markdown; `--metadata` is marginal. Dropped.
- **Split/merge recipes** — split/merge *produce files* → crosses the read-only scope
  boundary; belongs in Tier B if at all. Dropped.

**The one genuine, in-scope, read-side gap that survives:** **interactive PDF
form-field (AcroForm) values.** `extract_pdf.py` has no widget handling (grep: zero
`widget`/`field_value`/`acroform`), and pymupdf4llm 1.27's `to_markdown` emits
text/table/image layout, not filled-field values. A filled form (tax form,
application, contract with fields) extracts with its answers **missing** — a real
task-execution failure. This is the Tier-A deliverable (see the exec-plan).

### Tier B — capability expansion (deliberate scope decision)

Document **authoring/editing** is the substantive delta and a genuine product call
for co's knowledge-work positioning — not a parity defect against the read-only
skills. If pursued, the proven architecture is fully mapped: OOXML
unpack→edit→clean→pack with XSD validation (⚠ Anthropic-proprietary code — reference,
don't copy), openpyxl + the recalc-before-delivery trap, pptxgenjs, and the
adversarial QA + fix-verify loop (which mirrors co's own deliberation doctrine). Held
as a separate scoping decision; not planned here.

### Tier C — rejected-by-design / doctrine-conflicting (do not borrow)

- **marker-pdf OCR engine** (3-5GB ML stack). co deliberately chose model-vision over
  a bundled OCR engine (`documents/SKILL.md:75`; `--render` vision path). A pinned
  multi-GB secondary model conflicts with the local-mission disk budget and the
  "agent model's own capability or nothing" vision doctrine. Reconsider only if the
  configured vision model proves weak on dense multi-column/equation pages — a
  doctrine change to surface explicitly, not a quiet add.
- **nano-pdf NL-edit** (hermes + openclaw) — external LLM-backed CLI + API key;
  conflicts with local-first posture.
- **Native-provider PDF ingest** (openclaw) — N/A for co's local Ollama path.

---

## 4. Recommendation

- **Read axis:** co is peer-leading. The only genuine in-scope improvement is
  **PDF form-field extraction** → see
  `docs/exec-plans/active/2026-06-24-101727-skills-pdf-form-fields.md`.
- **Prompt layer:** already strong — selection cues (description) + routing (Step 2)
  already provide what peers' negative-scope blocks give. No change.
- **Strategic:** the real opportunity is **Tier B document authoring** — a deliberate
  scope decision, held for a separate PO-led scoping plan.
