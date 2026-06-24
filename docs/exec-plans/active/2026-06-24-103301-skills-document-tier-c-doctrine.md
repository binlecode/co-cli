# Tier C — Doctrine-revisit: keep-or-reconsider the rejected document borrows

A **decision document**, not an implementation plan. Tier C borrows are currently
**rejected-by-design** because they conflict with co's standing doctrine — they are not
deferred features waiting on demand. This plan exists to (a) record *why* each is rejected,
(b) define the explicit conditions under which the rejection should be reopened, and (c)
give the PO a Gate-1 verdict: **affirm the rejection** (default) or **flag one borrow for a
doctrine-change plan**. **No source changes; no task touches `co_cli/`.**

## Context

The document-parity audit (`docs/reference/RESEARCH-skills-document-parity.md` §3 Tier C,
lines 117-127) rejected three peer borrows as doctrine-conflicting:

- **marker-pdf OCR engine** (3-5 GB ML stack). co deliberately chose model-vision over a
  bundled OCR engine (`documents/SKILL.md:75`; the `--render` vision path,
  `extract_pdf.py` render mode). A pinned multi-GB secondary model conflicts with the
  local-mission disk budget and the **"agent model's own capability or nothing"** vision
  doctrine. Research's own re-open condition: *"Reconsider only if the configured vision
  model proves weak on dense multi-column/equation pages — a doctrine change to surface
  explicitly, not a quiet add"* (lines 122-124).
- **nano-pdf NL-edit** (hermes + openclaw) — an external LLM-backed CLI + API key;
  conflicts with the local-first posture (lines 125-126).
- **Native-provider PDF ingest** (openclaw — raw bytes to Anthropic/Gemini) — an
  optimization of a hosted multimodal backend, not co's *default* local path (line 127).
  Note Gemini is already a wired, config-selectable provider (`co_cli/config/llm.py:287`,
  `provider: Literal["ollama","gemini"]`; `:27` gemini-3-flash-preview; `:55-56` "gemini
  today" frontier reasoner) — so the accurate reason this borrow is rejected is *not* "no
  hosted backend exists" but "Ollama is the default/local-mission path and raw-bytes ingest
  is a per-backend optimization, not a standalone document-skill borrow".

Standing doctrine these collide with (the reason they are *rejected*, not *deferred*):
- **Vision = the agent model's own capability or nothing** — no pinned secondary vision
  model / describe-fallback (memory: `feedback_vision_agent_model_no_fallback`; the default
  Ollama model is itself vision-capable, `reference_configured_model_vision`; Gemini, the
  alternate provider, is natively multimodal — `co_cli/bootstrap/core.py:296`,
  `co_cli/deps.py:314`).
- **Local-first / local-disk-budget** (the *default* posture) — the same axis that gates
  Tier B's LibreOffice-headless and rejected marker-pdf's multi-GB stack. `DEFAULT_LLM_PROVIDER
  = "ollama"` (`co_cli/config/llm.py:16`).
- **No external paid API in the default path** for core skills — local mission. This is a
  default posture, not an absolute: Gemini (a paid external API) is a supported non-default
  provider (`co_cli/config/llm.py:145,287`).

## Problem & Outcome

**Problem:** "Rejected-by-design" decisions rot silently. Without a recorded rationale and
an explicit re-open condition, a rejected borrow gets either (a) quietly re-proposed in a
future PR with the doctrine conflict unexamined, or (b) left permanently un-revisited even
after the triggering condition (e.g. a measurably weak vision model) actually materializes.
The *decision and its trip-wire* are the missing artifacts.

**Outcome:** A Gate-1 verdict that, for each of the three borrows, either **affirms the
rejection** with a one-line rationale and a concrete re-open trigger, or **flags it for a
doctrine-change `/orchestrate-plan`** if the trigger has already fired. The default is
affirm — Tier C is doctrine-conflicting and the burden is on evidence that the doctrine
itself should change.

**Failure cost:** Without this, co either silently accretes a doctrine-violating dependency
(multi-GB OCR model, paid API key) through an unscrutinized PR, or never reconsiders OCR
even after the configured vision model demonstrably fails on the exact pages OCR exists to
handle — a capability gap nobody re-evaluates because the rejection was never made
conditional.

## Scope

**In scope (analysis + verdict only):**
- Record the doctrine rationale for each Tier C rejection, grounded in current code/config.
- Define a concrete, observable **re-open trigger** for each (the condition that would
  justify a doctrine-change plan).
- Produce a PO Gate-1 verdict per borrow: affirm-rejection (default) or flag-for-doctrine-plan.

**Out of scope (explicitly not this plan):**
- **Any implementation or dependency add.** No OCR engine, no NL-edit CLI, no API key, no
  source under `co_cli/` touched. A doctrine-change plan, if ever triggered, is a separate
  `/orchestrate-plan`.
- **Re-running a vision-model evaluation.** Whether the configured vision model is weak on
  dense/equation/multi-column pages is a measurement; this plan names it as the marker-pdf
  re-open trigger but does not perform it.
- **Tier A** (PDF form fields — its own plan, at Gate 1) and **Tier B** (document
  authoring — its own scoping plan, at Gate 1).

## Behavioral Constraints

- **Decision-only.** Deliverable is a per-borrow verdict + trigger; affirming a rejection
  authorizes nothing, and flagging one authorizes only a follow-on doctrine-change plan.
- **Default is affirm.** Tier C borrows conflict with standing doctrine; the burden of
  proof is on evidence that the *doctrine* should change, not on re-justifying the
  rejection. A borrow is flagged only if its re-open trigger has *already* fired with
  evidence.
- **Triggers must be observable.** Each re-open condition must be something that can be
  checked against runtime behavior, config, or logged sessions — never a vague "if it seems
  useful". A doctrine change is surfaced explicitly, never a quiet add (research §3 Tier C,
  line 124).

## High-Level Design

A per-borrow decision table. For each Tier C borrow: the doctrine it conflicts with, the
observable re-open trigger, and the PO's recommended Gate-1 verdict.

| Borrow | Doctrine conflict | Observable re-open trigger | Recommended verdict |
|---|---|---|---|
| **marker-pdf OCR engine** (3-5 GB) | vision-only ("agent model's own capability or nothing"); local-disk-budget | The **configured vision model mis-transcribes** the `--render` PNGs (`_render_pages` rasterizes via `get_pixmap`, `extract_pdf.py:210-239`; the model + `image_view` transcribe, `documents/SKILL.md:53-69`) on dense multi-column / equation / small-font scanned pages — a *measured* vision-eval over a representative sample, not anecdote. | **Affirm rejection.** Trigger has not fired (default Ollama model is vision-capable, `reference_configured_model_vision`; Gemini natively multimodal). Re-open only with a failing vision-eval in hand → then a doctrine-change plan. |
| **nano-pdf NL-edit** (external LLM CLI + API key) | local-first default; no external paid API in the default path for core skills | A decision to allow external paid LLM APIs in core skills as the *default* is taken at the platform level (a broader doctrine change than this one skill). | **Affirm rejection.** Would also be superseded for the document-*editing* use case by Tier B **if pursued** — but note Tier B is OOXML (docx/pptx/xlsx) authoring, not PDF editing, and is itself an un-approved scoping plan; so the supersession is partial and conditional. Re-open only if the platform adopts external-API skills generally. |
| **Native-provider PDF ingest** (raw bytes → Anthropic/Gemini) | default local Ollama path; no cloud-provider dependency in the default read path | The borrow only applies in a **Gemini-configured session** (`config.llm.provider == "gemini"`, `llm.py:287`); it becomes worth doing if that hosted path becomes a supported/primary mode where raw-bytes PDF ingest would beat the extract path. | **Affirm rejection.** A hosted multimodal backend (Gemini) already exists (`llm.py:27,145`), but Ollama is the default/local-mission path; native raw-bytes ingest is a per-backend optimization on the Gemini path's own plan, not a standalone document-skill borrow. |

**Cross-cutting observation:** all three rejections are downstream of two platform
doctrines — **vision-only/local-vision** and **local-first/no-external-API-by-default**.
None is a document-skill-specific call; each would be reopened by a *platform* doctrine
change, not by document-feature demand. That is precisely why they are Tier C
(doctrine-conflicting) and not Tier B (a deliberate feature scope decision).

**This plan is the document-skill view onto those platform doctrines — not their
authoritative record.** The reopen gate lives with the platform doctrine that owns
vision-only / local-first; this plan only captures the document-borrow-side verdict and
trigger so the parity audit's findings don't rot. A future reader should treat the platform
doctrine, not this artifact, as the canonical reopen authority.

## Tasks

**None — this is a decision document.** The deliverable is the Gate-1 per-borrow verdict
(captured in Open Questions), not a code change. There is no `done_when` because nothing is
built. If any borrow is flagged (trigger already fired), the follow-on is a *doctrine-change*
`/orchestrate-plan` — itself gated on the platform doctrine, not just a feature plan.

## Testing

No tests — no source changes. The "verification" is the PO's Gate-1 judgment that each
rejection rationale and re-open trigger is correct and grounded. The marker-pdf trigger
(vision-model weakness) would, if pursued, be validated by a *separate* vision-eval, not
here.

## Open Questions

The Gate-1 verdict — the deliverable — is one decision per borrow:

- **marker-pdf OCR: affirm rejection?** Recommendation: **affirm.** The re-open trigger (a
  measured weakness of the configured vision model on dense/equation/scanned pages) has not
  been demonstrated. Re-raise only with a failing vision-eval. *User/PO call:* is the
  current vision path trusted enough to keep OCR rejected, or is a vision-eval warranted
  now to test the trigger?
- **nano-pdf NL-edit: affirm rejection?** Recommendation: **affirm** — conflicts with the
  local-first default; would be superseded for document-*editing* by Tier B if pursued (but
  Tier B is OOXML authoring, not PDF editing, and is itself un-approved). Re-raise only if
  the platform decides to allow external paid-API skills as a default. *Largely
  doctrine-resolvable; surfaced for confirmation, not as an open product call.*
- **Native-provider PDF ingest: affirm rejection?** Recommendation: **affirm** — a hosted
  multimodal backend (Gemini) already exists (`llm.py:27,145,287`), but Ollama is the
  default/local-mission path; raw-bytes ingest is a per-backend optimization that belongs to
  the Gemini path's own plan, not a standalone document-skill borrow. Re-raise only if a
  Gemini-configured session becomes a supported/primary read mode. *Architecture-resolvable;
  confirmation only.*
- **Standing decision: should Tier C be revisited as a batch only when a platform doctrine
  (vision-only or local-first) is itself reconsidered?** Recommendation: yes — bundle any
  Tier C reconsideration into the platform doctrine plan that triggers it, rather than as a
  standalone document-skill borrow. **The one genuine open call:** commission the marker-pdf
  vision-eval now, or wait? Recommended default — **wait for an observed failure; do not
  pre-commission the eval.** A speculative eval with no observed vision weakness is itself
  the speculative work the burden-of-proof stance argues against.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Gemini is already a wired, config-selectable, natively-multimodal first-class provider (`llm.py:27,55-56,145,287`); the plan's "hosted backend is a future event / N/A today" premise was false to source. (Ollama remains the *default*, `llm.py:16`.) | Context native-provider bullet, High-Level Design native-provider row, and Open Questions Q3 rewritten: hosted backend exists; the accurate rejection reason is "Ollama is the default/local-mission path, raw-bytes ingest is a per-backend optimization"; trigger reframed to a Gemini-configured session becoming a primary read mode. |
| CD-m-1 | adopt | Gemini (a paid external API) is already supported as a non-default provider, so "no external paid API" is a default posture, not an absolute. | Context standing-doctrine bullets qualified to "default local-first posture / no external paid API in the default path", with `llm.py:16,145,287` citations. |
| CD-m-2 | adopt | `--render` only rasterizes to PNG; the transcription (and any failure) is the model + `image_view`, not the rasterizer. | High-Level Design marker-pdf trigger reworded to "the configured vision model mis-transcribes the `--render` PNGs", citing the `_render_pages` raster path (`extract_pdf.py:210-239`, `get_pixmap` `:229`) + `SKILL.md:53-69`. |
| CD-m-3 | adopt | Tier B is OOXML authoring, not PDF editing — the nano-pdf supersession is partial, and Tier B is itself unapproved. | nano-pdf row + Open Questions softened to "superseded for the document-editing use case if pursued", noting Tier B is OOXML not PDF and un-approved. |
| PO-m-1 | adopt | The verdict logically belongs to a platform-doctrine ledger; this artifact should not be read as the authoritative doctrine record. | High-Level Design: added a paragraph stating this is the document-skill view onto platform doctrines it does not own; the canonical reopen gate lives with the platform doctrine. |
| PO-m-2 | adopt | The cheap default (wait for an observed failure) must be explicit, else a speculative eval contradicts the burden-of-proof stance. | Open Questions standing-decision: made the default explicit — wait for an observed failure; do not pre-commission the vision-eval. |
| PO-m-3 | adopt | Tier B is an un-approved scoping plan; "superseded by Tier B" must read as conditional. | nano-pdf row + Open Questions: softened to "if pursued". (Folded with CD-m-3.) |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> This is a **doctrine-revisit decision document**. Default verdict is **affirm all three
> rejections** with the recorded triggers; the only genuine open call is whether to
> commission a marker-pdf vision-eval now (recommended: wait for an observed failure).
> Flagging any borrow authorizes only a follow-on *doctrine-change* `/orchestrate-plan`,
> gated on the platform doctrine — never a quiet dependency add.
