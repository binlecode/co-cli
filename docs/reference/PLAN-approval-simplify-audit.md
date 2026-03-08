# Plan Audit Log: Approval Simplify
_Slug: approval-simplify | Date: 2026-03-07_

---

# Audit Log

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2
**Summary:** The plan is correct on the primary removal path (TASK-1 through TASK-3) and the done_when checks are concrete and machine-verifiable. Two issues block it: TASK-5's done_when grep catches `approval_risk` in docs but misses the eight surviving "four-tier" descriptions across docs not listed in TASK-5, and the TASK-1 pattern-hint import path calls an internal helper without checking whether it is already imported in `_orchestrate.py`. Both are fixable without restructuring the plan.

**Major issues:**

- **CD-M-1** [TASK-5 / done_when]: The `done_when` grep for TASK-5 (`grep -rn "approval_risk\|_approval_risk" docs/`) would pass even though the following docs still describe "four-tier" approval or reference the risk classifier by description without using the exact phrase `approval_risk`: `docs/DESIGN-core.md:287` ("four-tier approval decision chain"), `docs/DESIGN-flow-core-turn.md:355` ("full four-tier approval decision chain"), `docs/DESIGN-flow-skills-lifecycle.md:439` ("four-tier approval chain"), `docs/DESIGN-flow-tools-lifecycle.md:24,303,313` ("four-tier chain" in mermaid label, files table, and cross-ref), `docs/DESIGN-tools-execution.md:220` ("four-tier approval chain (skill grants → per-session → risk → user prompt)"), `docs/DESIGN-prompt-design.md:44` ("four-tier decision chain"), `docs/DESIGN-index.md:73` ("four-tier decision chain, … risk classifier"), `docs/reference/ROADMAP-co-evolution.md:1107,1111,1114` (three "four-tier approval" mentions). None of these files appear in TASK-5's `files:` list, and the done_when grep would not catch them. Recommendation: extend TASK-5's `files:` list to cover all affected docs, update their "four-tier" descriptions to "three-tier" and remove inline "risk classifier" mentions in description text; extend done_when to also assert `grep -rn "four-tier" docs/` returns no matches (or returns only acceptable uses if any survive).

- **CD-M-2** [TASK-1, step 2]: The pattern-hint snippet imports `derive_pattern` from `co_cli._exec_approvals` inside `_handle_approvals`. Inspecting `_orchestrate.py` confirms `_exec_approvals` is not currently imported at module level — only `add_approval` is called inline via a local import-free reference: `add_approval(deps.config.exec_approvals_path, cmd, call.tool_name)` at line 447. The plan adds a new `from co_cli._exec_approvals import derive_pattern` inside the loop body but the existing `add_approval` call (line 447) is a direct call with no import, meaning `add_approval` must already be imported at module level or the file will fail at runtime. Verifying: `add_approval` is indeed at module-level import in `_orchestrate.py` (confirmed by the working code). However the plan code snippet for the hint block calls `from co_cli._exec_approvals import derive_pattern` as an inline import, which is inconsistent with how `add_approval` is imported. Recommendation: confirm the existing import statement for `add_approval` at module top and add `derive_pattern` to that same top-level import line rather than using a redundant inline import inside the loop.

**Minor issues:**

- **CD-m-1** [TASK-5 / done_when]: `docs/REVIEW-flow-approval.md` contains references to `_approval_risk.py` (line 230) and "risk classifier" (lines 63, 137, 228, 446). This file is not in TASK-5's `files:` list. REVIEW docs are not deleted or kept in sync by TASK-5. The done_when grep runs against `docs/` which includes `docs/REVIEW-flow-approval.md`, so the TASK-5 done_when will actually *fail* due to matches in the REVIEW doc. Recommendation: either add `docs/REVIEW-flow-approval.md` to TASK-5 scope (preferred — update or add a note that the feature was removed), or narrow the done_when grep to exclude `docs/REVIEW-*.md` (e.g., `grep -rn "approval_risk\|_approval_risk" docs/ --include="DESIGN-*.md"`). The same applies to `docs/reference/REVIEW-delivery-test-audit.md:12` which mentions `tests/test_approval_risk.py`.

- **CD-m-2** [TASK-3]: The plan says to delete `tests/test_approval_risk.py` implicitly (it is listed as deleted in git status), but TASK-3 does not explicitly mention it. The git status shows `D tests/test_approval_risk.py` — meaning it is already deleted in the working tree. Confirm this is already gone and add a verification step (`test -f tests/test_approval_risk.py` exits non-zero) to TASK-3's done_when to make the gate explicit.

- **CD-m-3** [TASK-1 / FrontendProtocol docstring]: The docstring fix ("Returns 'y' or 'n'" → "Returns 'y', 'n', or 'a'") is correct and needed, but the done_when check for TASK-1 does not verify this change. Since the docstring is in `_orchestrate.py`, add a grep check such as `grep -n "Returns.*'a'" co_cli/_orchestrate.py` to the done_when for TASK-1 to ensure it does not regress silently.

---

## Cycle C1 — PO

**Assessment:** revise
**Blocking:** PO-M-1
**Summary:** The plan correctly identifies the right problem and the removal scope is minimal and well-bounded. One major gap: Outcome #4 ("All DESIGN docs reflect the simplified model") is not achievable with TASK-5 as written — eight docs outside TASK-5's `files:` list retain "four-tier" descriptions, meaning the stated outcome is not actually delivered. This is not an implementation detail (already flagged by CD-M-1 as a done_when gap) — it is a scope gap: those files are not listed in TASK-5 at all, so even a corrected done_when grep would still fail. The TL must either widen TASK-5 scope to cover them or explicitly narrow Outcome #4 to "primary approval flow docs only" and call the rest doc-debt. No other scope or value concerns.

**Major issues:**

- **PO-M-1** [Outcome #4 / TASK-5 scope]: The plan's stated Outcome #4 is "All DESIGN docs reflect the simplified model." TASK-5's `files:` list covers four files (`DESIGN-index.md`, `DESIGN-tools-execution.md`, `DESIGN-flow-tools-lifecycle.md`, `DESIGN-core.md`). CD-M-1 identified eight additional files that retain "four-tier" descriptions and would not be touched. If TASK-5 is executed as written, Outcome #4 is false. Recommendation: extend TASK-5's `files:` list to include all eight remaining files and update done_when to assert `grep -rn "four-tier" docs/DESIGN-*.md` returns no matches.

**Minor issues:**

- **PO-m-1** [TASK-1 step 2 / pattern hint wording]: The hint text `[always → persists pattern: <pattern>]` uses implementation vocabulary. A more user-oriented phrasing such as `[choosing 'always' will remember: <pattern>]` is preferred. Recommendation: revise the display string to use plain user-facing language before implementation.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Extended TASK-5 files list to all 8 affected docs + added `four-tier` grep to done_when |
| CD-M-2   | adopt    | Moved `derive_pattern` to top-level import alongside `add_approval` in TASK-1 |
| CD-m-1   | adopt    | Narrowed TASK-5 done_when grep to `--include="DESIGN-*.md"`; REVIEW docs excluded by policy |
| CD-m-2   | adopt    | Added `test -f tests/test_approval_risk.py` verification to TASK-3 done_when |
| CD-m-3   | adopt    | Added `grep -n "Returns.*'a'"` to TASK-1 done_when |
| PO-M-1   | adopt    | Same fix as CD-M-1 — TASK-5 scope now covers all affected DESIGN docs |
| PO-m-1   | adopt    | Already applied in TASK-1 update: hint string now uses "will remember:" |

---

## Cycle C2 — Core Dev

**Assessment:** revise
**Blocking:** CD2-M-1
**Summary:** All five C1 items are correctly resolved in the updated plan. One new blocking issue: `DESIGN-flow-approval.md` retains two "four-tier" strings and two stale `approval_risk` config field references that TASK-4's done_when does not check. Since `DESIGN-flow-approval.md` is owned by TASK-4 but is absent from TASK-5's `files:` list, these residuals will cause TASK-5's done_when (`grep -rn "four-tier" docs/ --include="DESIGN-*.md"`) to fail — making TASK-5 uncompletable without first fixing TASK-4.

**C1 resolution verification:** All five items verified resolved.

**Major issues:**

- **CD2-M-1** [TASK-4 / done_when + TASK-5 / done_when dependency]: `DESIGN-flow-approval.md` currently contains two "four-tier" strings that TASK-4 does not sweep: line 152 and line 223. It also retains two stale `approval_risk` config field references on line 229. TASK-4's `done_when` checks only for `approval_risk|risk classifier|Tier 3.*risk|_approval_risk` — it will pass even with the "four-tier" strings still present. TASK-5's `done_when` runs `grep -rn "four-tier" docs/ --include="DESIGN-*.md"` which will match these and fail. Recommendation: extend TASK-4 step 2/3 to also replace "four-tier" language and remove stale config field refs from files table; extend TASK-4's `done_when` to add `grep -n "four-tier" docs/DESIGN-flow-approval.md` returns no matches.

**Minor issues:** None.

## Cycle C2 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD2-M-1  | adopt    | Extended TASK-4 step 3 to replace "four-tier" language and remove stale approval_risk field refs from files table; added `grep -n "four-tier"` to TASK-4 done_when |

---

## Cycle C2 — PO

**Assessment:** revise
**Blocking:** PO2-m-1
**Summary:** PO-M-1 is resolved. One new minor gap: TASK-5's per-file instructions for `DESIGN-flow-skills-lifecycle.md` only mention line 439 but line 196 also contains "four-tier chain" — will block TASK-5 done_when.

**C1 resolution verification:** PO-M-1 resolved. Verified.

**Major issues:** None.

**Minor issues:**

- **PO2-m-1** [TASK-5 / DESIGN-flow-skills-lifecycle.md instructions]: Per-file bullet only names line 439; line 196 also has "four-tier chain". Done_when grep will fire on line 196 and fail. Recommendation: extend bullet to also fix line 196.

- **PO2-m-2** [TASK-5 / ROADMAP-co-evolution.md instructions]: ROADMAP currently has zero "four-tier" occurrences. Stale instructions. Recommendation: update to note ROADMAP is already clean.

## Cycle C3 — Core Dev

**Assessment:** revise
**Blocking:** CD3-M-1

**CD2-M-1 verification:** Resolved. Verified.

**Major issues:**

- **CD3-M-1** [TASK-5 / DESIGN-flow-skills-lifecycle.md instructions, escalated from PO2-m-1]: PO2-m-1 was not adopted in C2 TL Decisions. Source confirms two "four-tier" occurrences at lines 196 and 439. TASK-5 per-file bullet only names line 439. TASK-5 done_when will fail on line 196. Reclassifying as major. Recommendation: update bullet to read "update 'four-tier chain' at line 196 and 'four-tier approval chain' at line 439 to 'three-tier'."

**Minor issues:**

- **CD3-m-1** [TASK-5 / ROADMAP-co-evolution.md instructions, confirmed from PO2-m-2]: Source confirms zero "four-tier" occurrences in ROADMAP. Stale line references. Recommendation: update bullet to note nothing to change.

## Cycle C3 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD3-M-1  | adopt    | Extended TASK-5 skills-lifecycle bullet to also fix line 196 |
| PO2-m-1  | adopt    | Same fix as CD3-M-1 (escalated) |
| CD3-m-1  | adopt    | Updated ROADMAP bullet to note zero occurrences; keeps file in list as confirmation step |
| PO2-m-2  | adopt    | Same as CD3-m-1 |

---

## Cycle C4 — Core Dev

**Assessment:** approve
**Blocking:** none

**C3 resolution verification:** CD3-M-1 and CD3-m-1 both resolved. Full checklist clean across all five tasks. Prerequisites chain consistent. Done_when gates all machine-verifiable.

## Cycle C4 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** Both C2 minor items are resolved in the plan body. No new scope, value, or first-principles issues found. The plan is minimal and correctly scoped; Outcome #4 is achievable given the TASK-4 / TASK-5 sequencing established across C1–C3.
