# Plan Audit Log: Startup Flow Consolidation
_Slug: startup-flow-consolidation | Date: 2026-03-07_

---

# Audit Log

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C2 — Team Lead
Plan updated per C1 decisions + REVIEW-flow-bootstrap.md revision (2026-03-07). Key changes: TASK-1 expanded with doctor caller model strengthening (system-owned vs agent-owned); High-Level Design updated to reflect 3-tier target architecture from updated review; done criteria added. Submitting for C2 review.

## Cycle C2 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1 / PO-M-1 | apply | Added `DESIGN-flow-core-turn.md` explicitly to TASK-4 files block with concrete change instruction (replace See Also link → `#model-dependency-check`) |
| CD-M-2 / PO-M-2 | apply | Narrowed TASK-4 done_when grep and Testing table row to `--include="DESIGN-*.md" --include="TODO-*.md"`; REVIEW docs explicitly excluded from check |
| CD-M-3 (complete) | apply | Added `#model-dependency-check` anchor to TASK-3 DESIGN-core.md link instruction |
| CD-m-3 | adopt | Replaced prose-presence criterion (5) in TASK-1 done_when with grep for "System-owned" and "Agent-owned" strings |

## Cycle C2 — PO

**Assessment:** revise
**Blocking:** PO-M-1, PO-M-2
**Summary:** Both original C1 PO blocking items (PO-M-1, PO-M-2) are resolved in the plan text. The new TASK-1 caller model strengthening stays within doc-only scope and satisfies the fifth done criterion. However, two C1 Core Dev blocking items (CD-M-1, CD-M-2) were recorded as "adopt" in the decisions table but were not applied to TASK-4's plan text — the done_when grep still uses the broad form and `DESIGN-flow-core-turn.md` is still absent from the files block. These omissions will cause TASK-4's completion check to either never pass or silently miss a dangling link.

**C1 blocking items resolved:**
- PO-M-1: resolved — TASK-2 done_when criterion (3) now explicitly requires "The file contains a 'Full Startup Sequence' section (already present) that covers create_deps(), skills load, get_agent(), and MCP connect fallback." This directly verifies pre-bootstrap stage coverage without requiring a content rewrite.
- PO-M-2: resolved — TASK-3 Changes in `DESIGN-core.md` now explicitly states "remove the standalone Preflight row" and "The Bootstrap row becomes the single startup phase entry." The done_when checks that `grep "DESIGN-flow-preflight" docs/DESIGN-index.md docs/DESIGN-core.md` returns 0 lines, which enforces deletion rather than redirect.

**New major issues:**
- **PO-M-1** [TASK-4 / done_when + files block — CD-M-1 adoption gap]: The decisions table records CD-M-1 as "adopt" — adding `docs/DESIGN-flow-core-turn.md` to TASK-4's files block and a change instruction to update its See Also entry. The Audit Log claims "Plan updated per C1 decisions." However, TASK-4's `files:` block still lists only `docs/DESIGN-flow-preflight.md (deleted)` and the generic grep wildcard. There is no explicit `docs/DESIGN-flow-core-turn.md` entry and no change instruction for updating that file's See Also link. After TASK-4 executes without this fix, `DESIGN-flow-core-turn.md` will contain a dangling link to the deleted file. Recommendation: add `docs/DESIGN-flow-core-turn.md` explicitly to TASK-4's `files:` block and add a change instruction: "In See Also, replace the link to `DESIGN-flow-preflight.md` with a link to `DESIGN-flow-bootstrap.md#model-dependency-check`."
- **PO-M-2** [TASK-4 / done_when + Testing table — CD-M-2 adoption gap]: The decisions table records CD-M-2 as "adopt" — narrowing the done_when grep to `--include="DESIGN-*.md" --include="TODO-*.md"` to avoid false failures on REVIEW docs. The plan text was not updated. TASK-4 done_when (line 170) still reads `grep -r "DESIGN-flow-preflight" docs/ returns 0 results` and the Testing table (line 186) uses the same broad command. `docs/REVIEW-flow-bootstrap.md` contains at least ten references that are correct to keep, so TASK-4 can never pass its own done_when as written. Recommendation: update both the done_when and the Testing table "No dangling preflight doc refs" row to use `grep -r "DESIGN-flow-preflight" docs/ --include="DESIGN-*.md" --include="TODO-*.md"`.

**New minor issues:**
- none

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1 | adopt | Add `docs/DESIGN-flow-core-turn.md` to TASK-4 files + change instructions |
| CD-M-2 | adopt | Narrow TASK-4 done_when grep to `--include="DESIGN-*.md" --include="TODO-*.md"`; same fix to Testing table |
| CD-M-3 | adopt | Pin heading to `## Model Dependency Check` (no "Stage:" prefix) in both TASK-2 done_when and TASK-3 instructions |
| PO-M-1 | modify | Bootstrap already has a complete "Full Startup Sequence" section covering skills load, get_agent, MCP fallback — the gap is the done_when not verifying this coverage. Expand TASK-2 done_when to check that the "Full Startup Sequence" section (or equivalent) covering pre-bootstrap stages is present; no new content section needed |
| PO-M-2 | adopt | Merge Preflight row into Bootstrap row in DESIGN-core.md lifecycle table; remove preflight as a named peer lifecycle phase |
| CD-m-1 | adopt | Annotate `flow-bootstrap` nav tree entry to indicate it covers the full startup sequence |
| CD-m-2 | adopt | Add explicit content preservation list to TASK-2 Changes section |
| PO-m-1 | adopt | Explicit "preserved vs dropped" content list added to TASK-2 |

## Cycle C1 — PO

**Assessment:** revise
**Blocking:** PO-M-1, PO-M-2
**Summary:** The plan correctly identifies the problem and stays doc-only — that is right. Two issues prevent approval: TASK-2's scope is narrower than what Option A requires, leaving the bootstrap doc still underspecifying earlier startup stages (skill load, get_agent, MCP fallback) after the plan executes; and TASK-3 retains a named Preflight row in the DESIGN-core.md lifecycle table while retiring its doc, which preserves the conceptual split at the highest-level summary view.

**Major issues:**
- **PO-M-1** [TASK-2 / done_when]: The done_when criterion checks only for the word "canonical" and a "Model Dependency Check" subsection. REVIEW-flow-bootstrap.md Option A explicitly requires bootstrap to own the full startup sequence from create_deps() — covering skill load and registry population, get_agent() creation, and MCP connect fallback as named startup stages. The TODO's TASK-2 only merges preflight content; it does not address the remaining early-startup gaps the review flagged. A reader consulting bootstrap after this plan executes will still not find skill load ordering or MCP fallback documented there. Recommendation: expand TASK-2 scope to include a top-level startup sequence diagram (as specified in Option A item 2) and brief coverage of the pre-bootstrap stages (skill load, get_agent, MCP fallback), or add a TASK-2b that explicitly owns this gap and update the done_when to verify it.
- **PO-M-2** [TASK-3 / DESIGN-core.md change]: The plan keeps a Preflight row in DESIGN-core.md's Session Lifecycle table and redirects its link to a subsection inside bootstrap. This is a half-measure: it retains "preflight" as a named peer lifecycle phase at the core summary level while the rest of the plan demotes it to an internal stage. After this plan, a reader of DESIGN-core.md will still see preflight listed alongside bootstrap as a named lifecycle row — the conceptual split survives at the highest-level view. Recommendation: merge the Preflight row into the Bootstrap row in the Session Lifecycle table (the model dependency check is now a stage inside startup, not a peer lifecycle phase), and verify the done_when criterion accordingly.

**Minor issues:**
- **PO-m-1** [TASK-2 / "Keep this section concise"]: The instruction says "deep prose lives in the now-retired preflight doc" as justification for a concise subsection — but the preflight doc is being deleted, so that prose will not live anywhere after retirement. If the content is worth preserving, it should land in the subsection; if it is not, the plan should say so explicitly. Recommendation: clarify what preflight content is dropped vs what must be preserved in the subsection, so the implementer does not inadvertently discard the hard vs soft failure boundary details or the model-chain pruning semantics.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2, CD-M-3
**Summary:** The plan is well-scoped and the task decomposition is sound, but three gaps will cause TASK-4 to miss dangling links or produce a silent broken state after execution: `DESIGN-flow-core-turn.md` carries a reference to `DESIGN-flow-preflight.md` that is entirely absent from the plan's scope and will cause TASK-4's own `done_when` grep to fail; the anchor name agreed between TASK-2 and TASK-3 is underspecified and will silently break the subsection link from `DESIGN-core.md`; and TASK-4's `done_when` grep scans all of `docs/` including `REVIEW-flow-bootstrap.md` which contains ten references to the retired file that are intentionally kept as historical record, making the completion check permanently unpassable.

**Major issues:**

- **CD-M-1** [TASK-4 / Scope and done_when]: `docs/DESIGN-flow-core-turn.md` line 345 contains `[DESIGN-flow-preflight.md](DESIGN-flow-preflight.md)` in its See Also section. This file is not listed in the Scope section, not in TASK-4's `files:` block, and not mentioned anywhere in the plan. After TASK-4 deletes `DESIGN-flow-preflight.md`, this link becomes dangling. More critically, TASK-4's own `done_when` check (`grep -r "DESIGN-flow-preflight" docs/`) will return this line and the task can never pass without fixing it. Recommendation: Add `docs/DESIGN-flow-core-turn.md` to TASK-4's `files:` block and add a change instruction to update the See Also entry to point to `DESIGN-flow-bootstrap.md` (with the Model Dependency Check anchor if available, otherwise top-level link).

- **CD-M-2** [TASK-4 / done_when — REVIEW doc collision]: The `done_when` for TASK-4 runs `grep -r "DESIGN-flow-preflight" docs/` and expects zero results. `docs/REVIEW-flow-bootstrap.md` contains at least ten references to `DESIGN-flow-preflight.md` at lines 8, 35, 46, 61, 95, 165, 239, 285, 360, 401, 405, 483 — these are in a historical review doc and are correct to keep as-is. TASK-4's completion check will never pass if the grep scans REVIEW docs. Recommendation: Narrow the `done_when` grep to exclude REVIEW docs: `grep -r "DESIGN-flow-preflight" docs/ --include="DESIGN-*.md" --include="TODO-*.md"` returns 0 results. Update the Testing table's "No dangling preflight doc refs" row command identically.

- **CD-M-3** [TASK-2 + TASK-3 — subsection anchor mismatch risk]: TASK-3 specifies updating the `DESIGN-core.md` Preflight row to link to the "Model Dependency Check" subsection within `DESIGN-flow-bootstrap.md` (implying anchor `#model-dependency-check`). TASK-2's `done_when` allows either `"Model Dependency Check"` or `"Stage: Model Dependency Check"` as the heading. If the implementer chooses `## Stage: Model Dependency Check`, the rendered anchor is `#stage-model-dependency-check`, which silently breaks the link written by TASK-3. Broken anchors produce no error — they render as plain unlinked text. The two tasks are executed independently with no cross-check. Recommendation: Pin the heading to a single agreed form in both TASK-2's `done_when` and TASK-3's change instructions. Suggest `## Model Dependency Check` (no "Stage:" prefix) since TASK-3's prose already uses that form. Add a note in TASK-2's `done_when` that the subsection heading must be exactly this text.

**Minor issues:**

- **CD-m-1** [TASK-3 / DESIGN-index.md navigation tree]: The plan removes the `flow-preflight` entry from the navigation tree but gives no instruction to update the `flow-bootstrap` entry to signal it now covers preflight too. After the change a reader scanning the tree would see `flow-bootstrap` listed under "tracing a bug or runtime workflow?" with its existing label and no indication it is the answer to startup questions previously split across two entries. Recommendation: Add an instruction to annotate the `flow-bootstrap` entry in the navigation tree, e.g. append `(canonical startup: model check + bootstrap)` so it remains self-describing after the peer entry disappears.

- **CD-m-2** [TASK-2 / content inventory for retirement]: TASK-2 lists three content bullets for the new subsection (check sequence, hard vs soft failure, model_roles mutation) but does not mention: the `PreflightResult` schema, the chain pruning behavior table, the Extension Model pattern (`_check_*` function signature and `run_preflight` dispatch), and the note that `_status.py` calls check functions directly as read-only probes. PO-m-1 already flags the "deep prose lives in the now-retired preflight doc" rationale as broken once the doc is deleted. Recommendation: Enumerate explicitly which content is preserved vs dropped in TASK-2's Changes section so the implementer makes a deliberate choice rather than discovering lost content in a follow-up review. At minimum the Extension Model pattern should be preserved (it tells future contributors how to add checks without touching bootstrap or chat_loop).

## Cycle C3 — Core Dev

**Assessment:** approve
**Blocking:** none
**Summary:** All four C2 blocking items are resolved in the plan text. TASK-4's files block now explicitly names `DESIGN-flow-core-turn.md` with a concrete replacement link, done_when and Testing table both use `--include` flags, TASK-3's DESIGN-core.md change instruction pins the `#model-dependency-check` anchor, and TASK-1 done_when criterion (5) uses grep commands for "System-owned" and "Agent-owned". The plan is internally consistent and ready to implement.

**C2 blocking items resolved:**
- CD-M-1: resolved — TASK-4 `files:` block now explicitly lists `docs/DESIGN-flow-core-turn.md (See Also link update)`, and the Changes section contains a concrete change instruction specifying the exact replacement link `[DESIGN-flow-bootstrap.md — Model Dependency Check](DESIGN-flow-bootstrap.md#model-dependency-check)`.
- CD-M-2: resolved — TASK-4 done_when and the Testing table "No dangling preflight doc refs" row both use `grep -r "DESIGN-flow-preflight" docs/ --include="DESIGN-*.md" --include="TODO-*.md"`.
- CD-M-3: resolved — TASK-3 Changes in `DESIGN-core.md` now states the link must be `[DESIGN-flow-bootstrap.md](DESIGN-flow-bootstrap.md#model-dependency-check)`, pinning the anchor to `#model-dependency-check` and matching the heading locked by TASK-2 done_when criterion (2).
- CD-m-3: resolved — TASK-1 done_when criterion (5) now reads `grep -n "System-owned" docs/DESIGN-doctor.md returns at least 1 result, AND grep -n "Agent-owned" docs/DESIGN-doctor.md returns at least 1 result`, making both checks machine-verifiable.

## Cycle C3 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** Both C2 blocking items (PO-M-1, PO-M-2) are resolved in the plan text. All five High-Level Design done criteria have corresponding task coverage. The plan is ready for implementation.

**C2 blocking items resolved:**
- PO-M-1: resolved — TASK-4 `files:` block now explicitly names `docs/DESIGN-flow-core-turn.md` with the exact change instruction: replace the See Also link to `DESIGN-flow-preflight.md` with `[DESIGN-flow-bootstrap.md — Model Dependency Check](DESIGN-flow-bootstrap.md#model-dependency-check)`.
- PO-M-2: resolved — TASK-4 `done_when` grep now reads `grep -r "DESIGN-flow-preflight" docs/ --include="DESIGN-*.md" --include="TODO-*.md"` with the `--include` flags present. The Testing table "No dangling preflight doc refs" row uses the same narrowed form.

---

# Post-Delivery Corrections — Plan Audit Log
_Date: 2026-03-07 | Replanning after REVIEW-startup-flow-consolidation.md ACTION_REQUIRED verdict_

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C1 — Core Dev

**Assessment:** approve
**Blocking:** none
**Summary:** P1 is accurate and well-scoped. TASK-1 covers all required changes (variable rename + guard removal + inline comment) with machine-verifiable `done_when` greps. P2 and P3 dismissals are correct. Two minor issues below are style/clarity only and do not block execution.

**Major issues:** none

**Minor issues:**

- **CD-m-1** [TASK-1 `done_when`]: The second grep (`grep "if is_fresh" docs/DESIGN-flow-bootstrap.md`) will match the existing line 227 text (`is_fresh(session,`) even *before* the fix is applied, because the substring `if is_fresh` does not yet appear there verbatim — the current line reads `if session is not None AND is_fresh(...)`. Post-fix the new line will read `if is_fresh(session_data, ...)`, so the grep will succeed. However, the grep would also succeed if an implementer added the phrase anywhere else in the file (e.g. the explanatory paragraph). Consider tightening to `grep "if is_fresh(session_data" docs/DESIGN-flow-bootstrap.md` to pin both the call form and the correct variable name in one check. Recommendation: replace the second `done_when` grep with `grep "if is_fresh(session_data" docs/DESIGN-flow-bootstrap.md`.

- **CD-m-2** [TASK-1 Change block]: The change description mentions updating `return session` → `return session_data` (line 237 in the current doc). This rename is required for consistency, but it is not covered by either `done_when` grep. If an implementer misses that trailing `return` line the greps still pass. Recommendation: add a third `done_when` check — `grep "return session_data" docs/DESIGN-flow-bootstrap.md` — or explicitly note in the change block that all three occurrences of bare `session` (lines 224, 228, 237) must be renamed.

## Cycle C1 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** The plan addresses a real, narrow documentation accuracy problem with the minimum change needed. P2 and P3 dismissals are correctly reasoned and independently verifiable against source. No scope, value, or first-principles concerns block execution.

**Major issues:**
- none

**Minor issues:**
- **PO-m-1** [P2 dismissal]: The dismissal is correct — `checks` is the grammatical subject of line 157's sentence, not buried in the parenthetical. However the sentence structure that caused the auditor to misread it once will cause the next auditor to misread it again. Recommendation: when TASK-1 ships, consider rewriting line 157 to lead with `checks` unambiguously (e.g. "The `checks` list is serialized to the tool return dict; other capability fields (`knowledge_backend`, `reranker`, …) are included alongside it."). Zero-risk, low-effort, prevents the same false positive from re-entering on the next review cycle. Not a blocker.
- **PO-m-2** [Testing]: The two `done_when` greps verify string removal and string presence but do not verify the variable rename is consistent across all three pseudocode lines (already flagged by CD-m-2). Core Dev's recommendation to add `grep "return session_data"` as a third check is the right call — it closes the gap with one line. Recommendation: adopt CD-m-2's suggestion before dev starts so done_when is complete before the work begins, not discovered during review.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-m-1   | adopt    | Tightened second `done_when` grep to `grep "if is_fresh(session_data"` — pins both the call form and the correct variable name |
| CD-m-2   | adopt    | Added third `done_when` grep: `grep "return session_data"` — covers the trailing return rename |
| PO-m-1   | reject   | Rewriting DESIGN-doctor.md line 157 for sentence clarity is out of scope for this plan. The doc is not wrong. Can be addressed by `/sync-doc` separately if desired |
| PO-m-2   | adopt    | Same as CD-m-2 — already adopting |
