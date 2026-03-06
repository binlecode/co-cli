---
name: orchestrate-review
description: Co-system health check. TL resolves scope, spawns two parallel diagnostic subagents (Code Dev audits doc accuracy, Auditor checks TODO health), then synthesizes a verdict. Use after a delivery or before starting a new development cycle to confirm code and docs are in an honest state. For architecture tradeoff analysis against peer systems, use /orchestrate-research instead.
---

# Review Orchestration Workflow

**TL is the orchestrator.** Two subagents run in parallel — Code Dev and Auditor — each reviewing from their domain. TL reads both outputs and writes the final synthesis to `docs/REVIEW-<scope>.md`.

**Invocation:** `/orchestrate-review <scope>`

`<scope>` is a feature area, module name, or `all`. Output is permanent — `docs/REVIEW-<scope>.md` is not temporary scaffolding.

**Consumes:** DESIGN docs, TODO docs, source files. **Produces:** docs/REVIEW-<scope>.md

---

## Phase 1 — TL: Scope Resolution

**1. Resolve DESIGN docs.**
- `scope = all`: glob `docs/DESIGN-*.md`.
- Otherwise: match scope as prefix or substring against filenames. If no filename matches, grep h1/h2 headings and Files-section entries — proceed if 2+ headings or a Files-section entry match. If still nothing: list available docs and stop.

**2. Resolve TODO docs.** Same prefix/substring match against `docs/TODO-*.md`, then same content fallback. A scope may match DESIGN docs but no TODO docs — that is valid.

**3. Identify source modules.** For each matched DESIGN doc, read its Files section and extract listed source paths — these are the files Code Dev checks.

**Spawn Code Dev and Auditor simultaneously (Phases 2a, 2b). Wait for both before Phase 3.**

---

## Phase 2a — Code Dev: Accuracy Audit

Code Dev reads each in-scope DESIGN doc and checks every factual claim against source. **Reports only — does not fix.**

**What to read:** Every in-scope DESIGN doc in full. Every source file in their Files sections. Also `co_cli/config.py` and `co_cli/deps.py` when any in-scope doc has a Config section — these are ground truth for settings and env vars.

**Inaccuracy patterns to check:**

| Pattern | What to look for |
|---------|-----------------|
| **Phantom feature** | Class, function, field, table, or tool described in the doc that does not exist in source |
| **Stale status** | "not yet implemented" / "Phase N ships next" when the code is already there |
| **Wrong schema** | Column names, table names, or SQL structure that do not match actual CREATE statements |
| **Wrong flow** | Function call sequence, control flow, or branching the code does not follow |
| **Wrong tool registration** | Tool listed as agent-registered when it is not, or vice versa |
| **Missing config entry** | Setting or env var in `config.py` env_map absent from the doc's Config table |
| **Wrong default** | Default value in doc does not match `Field(default=...)` or dataclass default in source |
| **Wrong field name** | Struct, dataclass, or schema field name misspelled or renamed in code |
| **Stale file path** | File listed in doc's Files section has been moved or deleted |
| **Missing coverage** | Shipped feature with no doc coverage at all |
| **Stale cross-doc index** | `DESIGN-core.md` Component Docs table missing a current `DESIGN-*.md`, or referencing a deleted one |

**Severity:** `blocking` — would mislead a developer implementing or debugging (wrong schema, wrong flow, phantom feature, wrong tool registration, stale status on a shipped feature; when in doubt, classify as blocking). `minor` — incomplete but not wrong (missing config entry, missing coverage of a minor feature, stale file path in an inactive section).

**Return to TL:** For each finding — doc, section, pattern, what the doc claims, what the source actually shows, severity. Overall counts and a 1–3 sentence summary. If nothing found: state that all docs are clean.

---

## Phase 2b — Auditor: TODO Health

Auditor checks every task in in-scope TODO docs for staleness, correctness, and readiness. **Reports only — does not modify TODO docs.**

**If no TODO doc matched scope:** Check if a `docs/DELIVERY-<scope>.md` exists — if so, spot-check 2–3 source files against its shipped claims. Scan active `docs/TODO-*.md` for prerequisite dependencies on the shipped feature. If neither exists, note "No TODO docs in scope — feature fully shipped."

**For each task, check:**
- **Already shipped?** If source already implements what the task describes, it's stale.
- **Stale assumption?** Does the task reference a function, field, or module that no longer exists as described?
- **Design contradiction?** Does the task conflict with a current DESIGN doc?
- **Well-formed?** Must have: a non-empty `files:` list; a non-empty `done_when:` that is machine-verifiable (grep / test pass / file exists — not "feature works" or "developer satisfied"); atomic scope (≤5 files, one verifiable outcome).

**Readiness verdict per TODO doc:**
- `ready_for_plan` — all tasks well-formed, no stale assumptions, nothing already shipped
- `needs_cleanup` — some well-formedness issues or minor staleness, fixable before planning
- `blocked` — a prerequisite hasn't shipped when the TODO assumes it has, or a majority of tasks are invalid

**Return to TL:** Per-task findings (task title, issues found, verdict), per-doc readiness verdict, and a 1–3 sentence summary.

---

## Phase 3 — TL: Synthesis

TL reads both reports and writes `docs/REVIEW-<scope>.md` covering:

- What was reviewed (DESIGN docs, source modules, TODO docs)
- Code Dev findings: a table of docs checked with status and findings; overall blocking/minor counts
- Auditor findings: a table of TODO docs with readiness verdict and issues
- Overall verdict: **HEALTHY** (no blocking findings, all TODO docs ready or none in scope) / **NEEDS_ATTENTION** (minor inaccuracies or TODO cleanup needed, can proceed but fix first) / **ACTION_REQUIRED** (any blocking doc finding, or a TODO blocked on a prerequisite for planned scope)
- Recommended next step: one sentence naming what to fix and where, or "proceed to `/orchestrate-plan <slug>`" if healthy

Print a brief terminal summary when done: scope, verdict, output path, recommended next step.

---

## Rules

- **No-fix rule:** Code Dev and Auditor report only. Fixes go to `/sync-doc` or manual edits per TL verdict.
- **Scope mismatch stops immediately:** If Phase 1 finds no matching DESIGN docs, stop — no output file.
- **Output is permanent:** `docs/REVIEW-<scope>.md` is not temporary scaffolding.
