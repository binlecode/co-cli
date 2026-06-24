# Tier B — Document Authoring: go/no-go scoping decision

A **decision document**, not an implementation plan. It frames whether co should adopt
document *authoring/editing* (writing `.docx/.xlsx/.pptx`) — the Tier B capability the
document-parity audit identified as the substantive product delta — and at what scope, so
the PO can return a grounded go/no-go at Gate 1. **No source changes; no task touches
`co_cli/`.** If Gate 1 returns GO, the chosen scope is handed to a fresh
`/orchestrate-plan` implementation plan.

## Context

The document-parity audit (`docs/reference/RESEARCH-skills-document-parity.md` §3 Tier B,
§4) closed the **read** axis (co is peer-leading; the one real read gap — PDF form fields
— is its own Tier-A plan, `2026-06-24-101727-skills-pdf-form-fields.md`). The audit's
explicit strategic finding: *"the real opportunity is Tier B document authoring — a
deliberate scope decision, held for a separate PO-led scoping plan"* (`§4`, line 138-139),
and *"Held as a separate scoping decision; not planned here"* (`§3`, line 115).

Current state both skills depend on:
- `documents` and `office` are **read-only by deliberate design** —
  `documents/SKILL.md:75` ("no writing or modifying the source file"),
  `office/SKILL.md:56` (per research §1, line 24). Authoring is a doctrine change, not an
  extension of either skill.
- Both read paths are **standalone console-script extractors invoked via `shell_exec`
  behind an approval gate — never imported into the agent process** (research §1, line
  25-26). Any authoring path inherits this same out-of-process, approval-gated contract.

The proven architecture is fully mapped in the research (so this plan does not re-discover
it), but is **⚠ Anthropic-proprietary** where vendored (`hermes-agent/.../office/`,
`LICENSE.txt:1` © Anthropic PBC — reference, never copy).

## Problem & Outcome

**Problem:** co can *read* every common office format but cannot *produce* one. A
knowledge-work user who asks co to "draft this as a Word doc", "build a spreadsheet with
these figures", or "make slides from this outline" hits a hard capability wall — co can
only emit markdown/text, never a deliverable file in the format the work actually lives in.
Whether this gap is worth co's local-first, vision-only, read-only posture is an unmade
product call.

**Outcome:** A grounded go/no-go. The PO leaves Gate 1 with (a) a clear recommendation,
(b) the doctrine and dependency costs quantified, and (c) if GO, the exact MVP scope to
hand to an implementation `/orchestrate-plan`. If NO-GO, a recorded rationale and a
re-raise trigger so it is not re-litigated.

**Failure cost:** Without a deliberate decision, authoring either (a) never happens because
nobody owns the doctrine call, leaving co read-only by inertia rather than by choice, or
(b) gets bolted onto `office`/`documents` ad hoc in a future PR — silently breaking the
read-only contract those skills' selection cues and bodies are built around, with no
dependency-budget or QA-loop discipline. Either way the *decision* is the artifact that is
missing.

## Scope

**In scope (this plan = analysis + recommendation only):**
- Frame the authoring value case for co's knowledge-work positioning.
- Lay out the candidate MVP scopes (format × create-vs-edit) with their dependency and
  doctrine costs, grounded in the research's architecture map.
- Produce a PO recommendation and the precise go/no-go question(s) for Gate 1.

**Out of scope (explicitly not this plan):**
- **Any implementation.** No new skill, script, dependency, or test. No file under
  `co_cli/` is touched. If GO, implementation is a *separate* `/orchestrate-plan`.
- **Re-discovering the architecture.** The OOXML unpack→edit→clean→pack flow, the openpyxl
  `recalc.py` trap, XSD validation, pptxgenjs, and the adversarial visual-QA loop are
  already mapped in research §2; this plan references, does not redo, them.
- **Copying proprietary code.** The Anthropic office toolkit is reference-only.
- **The Tier-A read gap** (PDF form fields) — its own plan, already at Gate 1.
- **Tier C doctrine-conflicting borrows** (marker-pdf OCR, nano-pdf NL-edit) — separate
  decision document.

## Behavioral Constraints

- **Decision-only.** This plan must not pre-commit implementation. Its sole deliverable is
  a recommendation + go/no-go; a Gate-1 GO does not authorize coding — it authorizes a
  follow-on implementation plan at the chosen scope.
- **Doctrine honesty.** Any authoring capability is a *documented doctrine change* to the
  read-only posture (`documents/SKILL.md:75`, `office/SKILL.md:56`), surfaced explicitly —
  never a quiet add to an existing read-only skill.
- **Local-first dependency budget.** The recommendation must weigh each scope's dependency
  footprint against the same local-disk-budget / local-mission posture that rejected
  Tier C's marker-pdf (research §3 Tier C). Heavyweight deps (LibreOffice-headless for the
  xlsx recalc trap) are a cost to name, not a default to accept.
- **Inherited execution contract.** Any future authoring path is assumed out-of-process
  (`shell_exec`, approval-gated, never imported into the agent) and write-isolated to a
  produced-artifact location — the same contract the read extractors honor (research §1).
  This constrains the architecture the go/no-go is evaluated against; it is not built here.

## High-Level Design

This is a decision framework, not a build. The PO weighs three candidate MVP scopes
against two axes (doctrine break severity, dependency footprint), grounded in the research
architecture map.

**Candidate scopes (increasing cost):**

| Scope | What ships | New deps (incremental over today) | Doctrine / risk |
|---|---|---|---|
| **S1 — narrow: one format, create-only** (`.pptx` or `.docx` from scratch) | a new write-capable skill + one author script; produced-artifact contract (illustrative `./out/<name>`, return path — the follow-on plan owns the actual path) | **pptx-first:** `python-pptx` — already a runtime dep (verified installed), so *zero* new packages. **docx-first:** `python-docx` (lxml-only pure-Python wheel; verified *not* installed — one new package). Either is pure-Python, no system/native dep. | Smallest doctrine break (new skill, read-only skills untouched). No recalc/QA-loop needed for create-only. Lowest dep cost — pptx-first adds *nothing* (see Peer use-case grounding for the format call). |
| **S2 — mid: docx + xlsx create** | adds openpyxl authoring | **openpyxl/python-pptx add nothing** — already runtime deps backing the read extractors (`pyproject.toml`; research §1 line 31). Genuine new cost = **LibreOffice-headless** (the `recalc.py` trap: openpyxl writes formula *strings* but never computes them — a headless recalc must run before delivery, research §2 line 53-57) | LibreOffice-headless is a heavyweight *system* dep — the same disk-budget tension that rejected Tier C's marker-pdf. The decisive cost; must be named, not assumed. |
| **S3 — full: docx+xlsx+pptx, create + edit** | full OOXML unpack→edit→clean→pack, XSD validation + auto-repair, pptxgenjs, adversarial visual-QA subagent loop (research §2 line 49-64) | + XSD validation stack (proprietary-XSD reference), render-for-QA (LibreOffice→pdftoppm). **pptxgenjs is a JavaScript library** (research §2 line 64) → a **Node.js runtime departure** on top of the multi-GB stack (python-pptx-only create avoids it) | Largest doctrine break and dep footprint; the visual-QA loop mirrors co's deliberation doctrine (a fit), but the multi-GB dep stack *plus a Node.js runtime* directly tensions local-mission. |

**Decision axes:**
- **Value vs frequency.** Authoring is high-value-per-instance for a knowledge worker
  ("give me the deliverable, not a transcript of it") but unproven frequency in co's actual
  use. The narrow S1 tests demand at the lowest cost.
- **Doctrine.** All scopes break read-only, but via a *new write-capable skill* — the
  existing read-only skills and their selection cues stay intact. The break is additive and
  containable, unlike Tier C which would mutate the vision doctrine of an existing path.
- **Dependency budget.** S1 is light (pure-Python; pptx-first adds nothing, docx-first
  adds one package). S2/S3 require LibreOffice-headless — the decisive cost, the same axis
  on which Tier C's marker-pdf was rejected.

**Peer use-case grounding (`hermes-agent` authoring skills, code-first survey):**
- Peers split authoring on one axis — **drive a live Office app via an MCP vs. produce a
  headless file on disk** (`excel-author/SKILL.md:238`, `pptx-author/SKILL.md:16,167`). co
  has no live-Office MCP and is a local CLI, so it can *only* be the headless file-artifact
  path — exactly S1's shape, and a coherent standalone use case ("deliver the file, not a
  transcript").
- The peers' most-proven authoring patterns are **pptx decks** (`powerpoint` skill) and
  **xlsx finance models** (`excel-author`/`pptx-author`, both under
  `optional-skills/finance/`, adapted from Anthropic's financial-services plugins — value
  prop is *auditability*, not "make a spreadsheet"). **No peer ships a `docx-author`
  skill.** Plain tabular/doc export is where peers say *don't* reach for a heavy authoring
  skill at all (`excel-author/SKILL.md:240` → `csv`/`pandas`).
- **Cost and proven-use-case point at different formats.** S1's original "e.g. `.docx`"
  led with Word only because python-docx is the one *new* pure-Python package — but `.docx`
  has the least peer use-case precedent. **`.pptx` create-only via `python-pptx` (already a
  runtime dep, verified installed) adds *zero* new packages, has the best-proven use case
  (decks), and does not hit the LibreOffice recalc trap (that is xlsx-formula-only).** So a
  pptx-first S1 plausibly dominates docx-first on *both* axes — cheaper and better-validated.

**PO recommendation (for Gate 1, not binding):** If GO, **start at S1** — a narrow,
create-only, pure-Python authoring proof behind a new write-capable skill: lowest doctrine
and dependency cost, read-only skills untouched, LibreOffice-headless deferred. But **pick
S1's format deliberately, not by dependency reflex:** the survey makes **`.pptx`
create-only (python-pptx, zero new deps, proven deck use case)** a stronger opening move
than `.docx` (one new package, no peer use-case template). Final format should track the
demand co actually observes (a deck request vs. a doc request). S2/S3 are expansion plans
gated on S1's observed value, not the opening move.

## Tasks

**None — this is a decision document.** A scoping plan's deliverable is the Gate-1 go/no-go
itself, captured in Open Questions below, not a code change. There is no `done_when` to run
because nothing is built. If Gate 1 returns GO at a chosen scope, that scope is handed to a
fresh `/orchestrate-plan` whose tasks *do* touch `co_cli/` under the normal dev workflow.

## Testing

No tests — no source changes. The decision's "verification" is the PO's Gate-1 review of
whether the value case, doctrine cost, and dependency budget are argued correctly and
grounded in the research. The follow-on implementation plan (if GO) owns all functional
tests for whatever it builds.

## Open Questions

These are the go/no-go decisions for the PO at Gate 1 — the actual deliverable of this
plan:

- **GO / NO-GO: does co adopt document authoring at all?** A genuine product call on
  whether knowledge-work authoring demand justifies breaking the read-only doctrine. Not
  derivable from code. **Re-raise trigger if NO-GO:** ≥3 distinct authoring (produce, not
  read) requests across separate sessions, surfaced via `session_search` (e.g. "write/draft
  this as a .docx", "build a spreadsheet"). The threshold makes a NO-GO durable rather than
  re-opened on a single ad-hoc request.
- **If GO, which MVP scope — S1 / S2 / S3, and S1 in which format?** Recommendation: S1
  (narrow, create-only, new write-capable skill). **Format is a real sub-question:** the
  peer survey makes `.pptx` create-only (python-pptx, already installed — zero new deps,
  proven deck use case) a stronger candidate than `.docx` (one new pure-Python package, no
  peer use-case template); pick the format to match observed demand. S1 **defers the
  LibreOffice-headless decision by construction** — that dep is only forced by S2/S3's xlsx
  recalc, and on the same local-disk-budget axis that rejected Tier C's marker-pdf it
  should not be taken on until S1 proves the demand. (So the LibreOffice-headless call is a
  *consequence* of the scope choice, not a separate free question.)
- **If GO, does the new capability live in a new write-capable skill, leaving `documents`
  and `office` read-only?** Recommendation: yes — selection reads only the `description`
  (`manifest.py:31`), so a separate skill never disturbs the read-only skills' cues; the
  doctrine break stays additive and containable.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-m-1 | adopt | openpyxl/python-pptx are already runtime deps backing the read extractors (research §1 line 31); only LibreOffice-headless + XSD stack are genuinely new — sharpens the "start at S1" case since S1's python-docx is the only new package. | High-Level Design scope table: "New deps" column now reads "incremental over today" and notes openpyxl/python-pptx add nothing; S1 flagged as the only brand-new package. |
| CD-m-2 | adopt | pptxgenjs is a JavaScript library (research §2 line 64) — adopting it adds a Node.js runtime departure on top of the multi-GB stack, compounding S3's local-mission tension. | High-Level Design S3 row: flagged pptxgenjs as JS/Node.js runtime cost; noted python-pptx-only create avoids it. |
| CD-m-3 | adopt | The dependency-claim grounding bar requires a cited basis; python-docx is pure-Python (lxml-only) and verified not currently installed. | High-Level Design S1 row: grounded python-docx as lxml-only pure-Python wheels, not currently installed. |
| PO-m-1 | adopt | An unquantified NO-GO trigger risks silent re-litigation on a single request. | Open Questions Q1: set the re-raise threshold at ≥3 distinct cross-session authoring requests via `session_search`. |
| PO-m-2 | adopt | The LibreOffice-headless call is pre-answered by the S1 recommendation + local-disk-budget doctrine — a consequence, not a co-equal free product question. | Open Questions Q2: demoted LibreOffice-headless to an explicit consequence of S1 ("defers it by construction"); kept the three questions strictly user-only-answerable. |
| PO-m-3 | adopt | `./out/<name>` is borrowed from hermes excel-author and edges toward pre-committing an implementation detail the follow-on plan should own. | High-Level Design S1 row: marked `./out/<name>` illustrative/non-binding. |
| TL-1 | adopt | Code-first peer survey (`hermes-agent` authoring skills) clarified the write-path use cases: peers split on live-session-vs-headless-file (co is headless-only); the most-proven patterns are pptx decks + xlsx finance models; no peer ships a `docx-author`; `.pptx` create-only via the already-installed `python-pptx` adds zero new deps and has a proven use case, so it plausibly dominates the docx-first S1 on both cost and validation. | High-Level Design: added a "Peer use-case grounding" block, broadened the S1 row to pptx-or-docx with the dep delta, and reframed the PO recommendation + Open Questions Q2 so S1's *format* is a deliberate Gate-1 sub-question (pptx-first candidate), not a dependency-reflex default to `.docx`. |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> This is a **decision document** — a Gate-1 GO does not authorize coding. It authorizes a
> follow-on implementation `/orchestrate-plan` at the chosen scope (S1 recommended).
> If NO-GO, the rationale + re-raise trigger are recorded above.
