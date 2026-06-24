# PDF Form-Field Extraction — surface filled AcroForm values in `documents`

Add interactive form-field (AcroForm widget) value extraction to `co-extract-pdf` so a
filled PDF form (tax form, application, contract-with-fields) no longer extracts with
its answers silently missing. The single genuine, in-scope, read-side gap surviving the
peer-parity audit (`docs/reference/RESEARCH-skills-document-parity.md`).

## Context

The document-parity audit compared co's `documents`/`office` skills against hermes,
openclaw, and the Anthropic office toolkit across the prompt and tooling layers. On the
read axis co is peer-leading, and the prompt-layer "negative-scope" borrow turned out
**already implemented** (selection cue lives in the `description`, `manifest.py:31-35` +
`documents/SKILL.md:2`; in-turn routing in the body's Step 2). The one borrow that
survived grounding against current code is a real tooling gap:

- `co_cli/skills/documents/scripts/extract_pdf.py` has **no widget/AcroForm handling**
  (grep: zero `widget`/`field_value`). The text path runs pymupdf4llm `to_markdown`
  (`_extract`, `extract_pdf.py:112-131`), which emits the page's text/table/image
  layout but **not** the values typed into interactive form fields.
- openclaw surfaces this deliberately (`document-extractor.ts:90-94`, `forms:true`); co
  does not. A user who asks "what did they enter for income on this filled return.pdf?"
  gets an extraction with the answer absent — a false-complete read, the same failure
  class the truncation contracts already guard against elsewhere.

## Problem & Outcome

**Problem:** A filled interactive PDF form extracts with its field values missing and no
signal that anything was dropped — the model answers from the static label text and
silently omits the user's entered data.

**Outcome:** When a PDF page carries filled AcroForm widgets, `co-extract-pdf` appends
their `field name: value` pairs under that page's `## Page N` section, so the model can
read and cite the entered data. A PDF with no form fields is byte-for-byte unchanged
(no new output, no behavior change for the common case). The `documents` skill body
tells the model filled fields appear this way so it grounds answers in them.

**Failure cost:** Without this, co confidently answers form questions from blank
templates — a silent wrong-answer on exactly the documents (forms, applications) where
the entered values are the whole point.

## Scope

**In scope:**
- Extract filled AcroForm widget values per page in the text path of `co-extract-pdf`
  and render them under the matching `## Page N` marker.
- A short `documents/SKILL.md` Step-4 note so the model knows filled-field lines appear
  and must be grounded/cited like page text.
- A functional test over a real filled-form PDF.

**Out of scope (rejected by design — do not add):**
- OCR engine / marker-pdf (Tier C — conflicts with the model-vision-only doctrine,
  `RESEARCH-skills-document-parity.md` §3).
- Form *filling* / writing field values — `documents` is read-only (`SKILL.md:75`).
- Office (`.docx/.xlsx/.pptx`) form controls — separate skill, separate extractor.
- Widget extraction on the scanned/`--render` vision path or the scanned short-circuit
  (`extract_pdf.py:251`) — an image-only form with widgets-but-no-text-layer is a rare
  edge; the text path is where filled forms live.
- Any negative-scope/body-routing prompt change — already covered (audit §3).

## Behavioral Constraints

- **Zero change for non-form PDFs.** A PDF with no *filled* widgets must produce identical
  output to today — the form block appears only when a page has at least one widget in a
  filled state. "Filled" is type-aware: a text/combo/list field with a non-empty string,
  or a checkbox/radio in its on-state. An unset button reports `""` or the literal `"Off"`
  (verified pymupdf 1.27.2.3) and is skipped — a blank template, including one full of
  unchecked boxes, emits no form block.
- **Field order mirrors the PDF.** Fields render in `page.widgets()` (file/widget) order,
  never sorted — sorting by name would scramble the visual correspondence a citing user
  expects on a multi-field form.
- **Same one-process, error-typed contract.** Widget collection runs inside the existing
  console-script path (never imported into the agent), reuses the existing pymupdf
  handle pattern, and must not introduce a new failure mode that breaks normal
  extraction — a widget-read error degrades to "no form block for that page", never a
  non-zero exit on an otherwise-readable PDF.
- **Citations stay page-anchored.** Field lines render under the page's existing
  `## Page N` marker so the model's existing "cite page N" instruction covers them — no
  new citation convention.
- **`--pages` honored.** Form fields are collected only for the selected pages.

## High-Level Design

- pymupdf exposes interactive fields via `page.widgets()` — each `Widget` carries
  `field_name`, `field_value`, and `field_type_string`. The text path already selects
  the page list (`_resolve_pages`) and renders per-page markdown (`_render`,
  `extract_pdf.py:134-143`).
- Add a `_page_form_fields(doc, page_index) -> list[tuple[str, str]]` helper returning the
  **filled** `(field_name, field_value)` pairs for widgets on that page. "Filled" is
  type-aware on `field_type_string`, which is therefore load-bearing, not decorative:
  - Text / ComboBox / ListBox → keep when `field_value` is a non-empty string.
  - CheckBox / RadioButton → an unset button reports `""` or the literal `"Off"` (verified
    pymupdf 1.27.2.3: unchecked box and unselected radio both yield `"Off"`); skip when the
    value is falsy or `"Off"`, render only the on-state (`"Yes"` / the export value).
  Without the type-aware rule a blank template full of checkboxes would emit `- agree: Off`
  lines and break the zero-change guarantee (constraint 1).
- **Error boundary is per-page and contained:** the `try/except` wraps the per-page
  `page.widgets()` iteration *inside* `_page_form_fields`, so a malformed widget yields `[]`
  for that page; the exception must never escape into `_resolve_pages`'s `text_chars`
  computation, on which the normal extraction depends.
- **Alignment keys on the 0-based document page index:** the field map is keyed by
  `pages[index]`. `_render`'s `chunk.metadata.page` is `None` in the installed pymupdf4llm
  1.27.2.3 (the layout path emits 1-based `page_number` instead), so the existing
  `pages[index]` fallback (`extract_pdf.py:139-141`) is already the effective page identity
  — look the field map up by that same key. Do **not** route alignment through
  `chunk.metadata.page`; it is inert in the pinned version.
- **Threading:** `_resolve_pages` returns the per-page field map alongside its current
  `(pages, text_chars)`; `main` passes it into `_render`, which gains a field-map parameter.
  `_extract` is untouched (it opens its own handle for the markdown pass).
- `_render` gains the per-page field map: when a page has fields, append a
  `### Form fields` subsection of `- name: value` lines after that page's text, inside
  the `## Page N` block, in `page.widgets()` order. No fields → no subsection
  (constraint 1).
- The skill body Step-4 "Normal output" bullet (`documents/SKILL.md:43`) gains one
  sentence: filled interactive form fields appear as a `### Form fields` list under their
  page; treat and cite them as page content.

## Tasks

### ✓ DONE TASK-1 — Extract and render filled AcroForm field values
- **files:** `co_cli/skills/documents/scripts/extract_pdf.py`
- **done_when:** a `_page_form_fields` helper collects the **filled** `(field_name,
  field_value)` widget pairs per selected 0-based page via `page.widgets()`, applying the
  type-aware filter (text/combo/list: non-empty string; checkbox/radio: skip falsy or
  `"Off"`, keep on-state) so an unchecked-box template yields no fields; `_resolve_pages`
  returns this field map (keyed by 0-based `pages[index]`) alongside `(pages, text_chars)`
  and `main` threads it into a new `_render` field-map parameter; `_render` looks the map
  up by `pages[index]` (not `chunk.metadata.page`) and appends a `### Form fields`
  subsection (`- name: value` lines, in `page.widgets()` order) inside the `## Page N`
  block only for pages with at least one filled field; a PDF with no filled widgets
  produces output identical to before (no subsection, no trailing blank); the per-page
  widget `try/except` lives inside `_page_form_fields` so a widget-read failure on a page
  yields no form block for that page (never a non-zero exit) and never aborts the
  `text_chars` computation. `--pages` restricts field collection to the selected pages.
- **success_signal:** extracting a filled-form PDF shows the entered values under their
  page; a normal text PDF is unchanged.

### ✓ DONE TASK-2 — Skill body note for filled form fields
- **files:** `co_cli/skills/documents/SKILL.md`
- **prerequisites:** TASK-1
- **done_when:** the Step-4 "Normal output" handling states that filled interactive form
  fields appear as a `### Form fields` list under their `## Page N` and must be grounded
  and cited like page text; the description (selection cue) and Step-2 routing are
  untouched; `uv run python -c "from co_cli.skills.lint import lint_skill;
  print(lint_skill(open('co_cli/skills/documents/SKILL.md').read()))"` returns `[]`
  (no R1-R4 finding — description still ≤1024, body still <8000, H1 intact) and
  `lint_bundled_extras` returns `[]` (no TODO marker).
- **success_signal:** the model, after loading `documents`, knows to read and cite the
  form-field lines.

### ✓ DONE TASK-3 — Functional test over a real filled-form PDF
- **files:** `tests/test_extract_pdf_form_fields.py`
- **prerequisites:** TASK-1
- **done_when:** a pytest builds a **real** PDF carrying a filled text-field widget
  (constructed with pymupdf — real data, no mock) on a page that **also carries ≥10
  characters of real body text** (`page.insert_text(...)`) so the text path runs rather
  than short-circuiting to `[no-text-layer]` (`MIN_CHARS_PER_PAGE`, `extract_pdf.py:251`),
  runs the `extract_pdf` text path, and asserts (a) the field's entered value appears in
  the output under the correct `## Page N`; (b) a PDF with no widgets extracts with no
  `### Form fields` subsection (the zero-change guarantee); and (c) a PDF whose only
  widget is an **unchecked checkbox** (`field_value == "Off"`) emits no `### Form fields`
  subsection (the type-aware filter, the failure CD-M-1 caught). `uv run pytest
  tests/test_extract_pdf_form_fields.py 2>&1 | tee
  .pytest-logs/$(date +%Y%m%d-%H%M%S)-task3.log` passes.
- **success_signal:** the entered value is captured; non-form PDFs are provably
  unchanged.

## Testing

- TASK-3 is the regression gate: deterministic, LLM-free, real filled-form PDF
  (pymupdf-constructed in the test — genuine widget objects, not a mock), asserting the
  observed extracted value and the zero-change guarantee for non-form PDFs (functional,
  per `testing.md`).
- No eval change. W4.B (skill *selection*) is description-driven and untouched; this is
  an extractor-output change behind the already-selected `documents` skill. A manual
  smoke (`co-extract-pdf <filled-form.pdf>` showing the values) is the behavioral
  confirmation; the chat path is non-gating.
- No floor-guard exposure (no `co_cli/context/rules/*.md` edits). The SKILL.md edit is a
  bundled-skill body, not an injected rule.

## Open Questions

Both prior open questions were resolved at Gate 1 (see Decisions):

- **Ship now vs defer behind Tier-B authoring → ship now.** This is a correctness-class
  fix (a confident silent wrong answer on filled forms), not a feature increment; lower
  frequency lowers expected harm but does not make the current behavior correct. The
  change is strictly additive and byte-for-byte inert on the common path (constraint 1,
  gated by TASK-3), so there is no common-path risk to weigh, and it is independent of the
  larger Tier-B document-authoring decision — no reason to couple it behind that gate.
- **Field ordering → widget order, promoted to a Behavioral Constraint.** Render in
  `page.widgets()` order, never sorted — fidelity to the source, not a shortcut.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Unchecked checkbox/unselected radio report non-empty `"Off"` (verified pymupdf 1.27.2.3); a bare "non-empty value" filter would emit `- agree: Off` on a blank template, breaking constraint 1. | High-Level Design: replaced the flat "skip non-empty" filter with a type-aware rule (text/combo/list non-empty; checkbox/radio skip falsy/`"Off"`, keep on-state), making `field_type_string` load-bearing. Behavioral Constraint 1 reworded for type-aware "filled". TASK-1 `done_when` and TASK-3 case (c) updated. |
| CD-M-2 | adopt | `chunk.metadata.page` is `None` in installed pymupdf4llm 1.27.2.3 (layout path uses 1-based `page_number`); `_render` always falls through to `pages[index]`, so the plan's stated alignment mechanism was dead code. | High-Level Design: pinned alignment to the 0-based `pages[index]` key and called out `chunk.metadata.page` as inert. TASK-1 `done_when` updated to key/look up by `pages[index]`. |
| CD-m-1 | adopt | A widget-only test page yields <10 chars and short-circuits to `[no-text-layer]` (`extract_pdf.py:251`), so the form block never renders and the test would fail for the wrong reason. | TASK-3 `done_when` now requires the test PDF page to carry ≥10 chars of real body text. |
| CD-m-2 | adopt | `page.widgets()` lazily parses each widget and can raise; an unguarded exception in `_resolve_pages` would abort the `text_chars` computation the normal path depends on. | High-Level Design + TASK-1 `done_when`: pinned the `try/except` inside `_page_form_fields` (per-page `[]`), never escaping into `_resolve_pages`. |
| CD-m-3 | adopt | `field_type_string` was listed but unused; CD-M-1's fix makes it the button-vs-text discriminator. | Folded into CD-M-1 — `field_type_string` is now load-bearing in the design. |
| CD-m-4 | adopt | The field map must be threaded `_resolve_pages` → `main` → `_render` (new return + new param); the plan implied but didn't pin the wiring. | High-Level Design "Threading" bullet + TASK-1 `done_when` name the new return value and `_render` parameter. |
| PO-m-1 | adopt | Ship-now: correctness-class fix, zero common-path risk, Tier-B-independent. | Open Questions: recorded ship-now resolution with rationale so it isn't re-litigated. |
| PO-m-2 | adopt | Widget order is fidelity to the source; sorting would scramble multi-field forms. | Promoted to a new Behavioral Constraint ("Field order mirrors the PDF") and stated in High-Level Design + TASK-1. |
| PO-m-3 | adopt | The scanned/`--render` exclusion is a genuine rare edge but should read as a documented known-edge, not a silent omission — already captured in Scope's out-of-scope bullet. | No new change; Scope already documents the exclusion explicitly. |

## Delivery Summary — 2026-06-24

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_page_form_fields` type-aware filter + `_resolve_pages` form map keyed by `pages[index]` + `_render` `### Form fields` only on filled pages; per-page error boundary; non-form PDF unchanged; `--pages` honored | ✓ pass |
| TASK-2 | Step-4 "Normal output" note added; description/Step-2 untouched; `lint_skill`/`lint_bundled_extras` return `[]` | ✓ pass |
| TASK-3 | real pymupdf PDFs: (a) filled value under correct `## Page N`, (b) no-widget → no subsection, (c) unchecked-checkbox → no subsection | ✓ pass |

**Tests:** scoped — 18 passed, 0 failed (3 new in `test_extract_pdf_form_fields.py` + 15 existing documents-flow tests, no regression). Behavioral smoke confirmed: filled form shows `- income: 50000` under `## Page 1`; unchecked checkbox omitted; plain PDF unchanged.
**Doc Sync:** fixed — `skills-document.md` Components / Tier-1 logic (new "Filled form fields" subsection) / Public Interface / Files / Test Gates updated; `01-system.md` index clean.

**Overall: DELIVERED**
All three tasks passed, lint clean, scoped tests green, doc sync complete. Both Core Dev blockers from Gate 1 (type-aware `"Off"` filter; `pages[index]` alignment over inert `chunk.metadata.page`) are implemented and test-covered.

## Implementation Review — 2026-06-24

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | type-aware filter; map keyed `pages[index]`; `_render` subsection on filled pages only; per-page error boundary; non-form unchanged; `--pages` honored | ✓ pass | `extract_pdf.py:94-119` `_page_form_fields` skips None/empty for all types then `"Off"` only for buttons (`_BUTTON_FIELD_TYPES:91`); `try/except` returns `[]`. `:142` map keyed by 0-based `page`, built after `text_chars:141`. `:183-190` lookup reuses the marker's `page_zero_based` (metadata.page inert per CD-M-2 → `pages[index]`); subsection only when `fields` truthy; widget order, no sort. `:296,309` threads map. `--pages` via comprehension over resolved `pages`. |
| TASK-2 | Step-4 note added; description/Step-2 untouched; both linters `[]` | ✓ pass | `SKILL.md:43` diff adds the form-field sentence to the Normal-output bullet only; description + Step-2 lines unchanged in diff. `lint_skill`/`lint_bundled_extras` both returned `[]`. |
| TASK-3 | real PDFs: (a) value under correct page, (b) no-widget → no subsection, (c) unchecked checkbox → no subsection | ✓ pass | `test_extract_pdf_form_fields.py:57/71/82` — three real pymupdf-built PDFs, body text ≥10 chars, full `_resolve_pages→_extract→_render` path; stub-litmus strong on (a) and (c) (filter regression fails them), (b) is the exclusion half of an inclusion/exclusion pair (honored exception). |

### Issues Found & Fixed
No issues found.

_Notes (non-blocking, no action): `_render:183` reads `chunk.metadata.page` first and falls back to `pages[index]`. CD-M-2 verified metadata.page is inert in pinned pymupdf4llm 1.27.2.3, so the effective key is `pages[index]` as the done_when requires; reusing the same `page_zero_based` the page marker uses is the correct alignment choice (form block tracks the marker, cannot diverge). `uv.lock` appears in the diff (version 0.8.456→0.8.482) — accreted version bump from prior ships, unrelated to this plan, not introduced by any task._

### Tests
- Command: `uv run pytest -v`
- Result: 848 passed, 0 failed
- Log: `.pytest-logs/20260624-112312-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `co-extract-pdf <filled-form.pdf>`: ✓ `success_signal` verified — a real PDF with a filled text field and an unchecked checkbox rendered `- income: 50000` under `## Page 1` in a `### Form fields` block; the unchecked `agree` checkbox was filtered out (type-aware "Off" filter holds on a save+reopen widget). TASK-2 success_signal is prompt-layer (model citing behavior) — the SKILL.md note is present and lints clean; chat path non-gating.

### Overall: PASS
All three `done_when` met with file:line evidence, full suite green, lint clean, behavioral smoke confirms entered values surface and blank fields stay inert. Ready for Gate 2 / `/ship`.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-06-24-101727-skills-pdf-form-fields`
