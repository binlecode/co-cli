---
name: orchestrate-review
description: Co-system health check. Runs two parallel diagnostic agents — Code Dev audits doc accuracy, Auditor checks TODO health — then merges outputs into a verdict. Use after a delivery or before starting a new development cycle to confirm code and docs are in an honest state. For architecture tradeoff analysis against peer systems, use the research task instead.
---

# Review Workflow

**Two agents run in parallel.** Code Dev audits doc accuracy against source. Auditor checks TODO health. Both write directly to `docs/REVIEW-<scope>.md`. The agent that finishes last appends the verdict.

**Invocation:** `/orchestrate-review <scope>`

`<scope>` is a feature area, module name, or `all`. Output is permanent — `docs/REVIEW-<scope>.md` is not temporary scaffolding.

**Consumes:** DESIGN docs, TODO docs, source files. **Produces:** `docs/REVIEW-<scope>.md`

---

## Phase 1 — Scope Resolution

Resolve scope before spawning agents. **Only read files within the project working directory. Do not access peer or reference repos — those are for the research task only.**

**1. Resolve DESIGN docs.**
- `scope = all`: glob `docs/DESIGN-*.md`.
- Otherwise: match scope as prefix or substring against filenames. If no filename matches, grep h1/h2 headings and Files-section entries — proceed if 2+ headings or a Files-section entry match. If still nothing: list available docs and stop.

**2. Resolve TODO docs.** Same prefix/substring match against `docs/TODO-*.md`, then same content fallback. A scope may match DESIGN docs but no TODO docs — that is valid.

**3. Identify source modules.** For each matched DESIGN doc, read its Files section and extract listed source paths — these are the files Code Dev checks.

**Create the output file** at `docs/REVIEW-<scope>.md` with a header before spawning agents:
```
# REVIEW: <scope> — Co-System Health Check
_Date: <today>_

## What Was Reviewed
<list matched DESIGN docs, source modules, TODO docs>
```

**Spawn Code Dev and Auditor simultaneously. Wait for both before appending the verdict.**

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

**Append to `docs/REVIEW-<scope>.md`:**

```markdown
## Code Dev — Doc Accuracy Audit

| Doc | Section | Status | Finding |
|-----|---------|--------|---------|
...

### Finding Details
...

**Overall: <N> blocking, <N> minor**
```

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

**Append to `docs/REVIEW-<scope>.md`:**

```markdown
## Auditor — TODO Health

| TODO doc | Task | Verdict | Key finding |
|----------|------|---------|-------------|
...

**Overall verdict for `<doc>`: `<readiness>`**
```

---

## Phase 3 — Verdict (last agent to finish)

Whichever agent finishes last appends the verdict. The verdict is deterministic from the two reports — no judgment needed.

**Rules:**
- **HEALTHY** — no blocking Code Dev findings, all in-scope TODO docs `ready_for_plan` or no TODO docs in scope
- **NEEDS_ATTENTION** — minor inaccuracies or TODO cleanup needed; can proceed but fix first
- **ACTION_REQUIRED** — any blocking Code Dev finding, or a TODO doc `blocked` on a prerequisite for planned scope

**Append to `docs/REVIEW-<scope>.md`:**

```markdown
## Verdict

**Overall: HEALTHY / NEEDS_ATTENTION / ACTION_REQUIRED**

| Priority | Action | Source |
|----------|--------|--------|
| P1 | <fix> | <finding id> |

**Recommended next step:** <one sentence>
```

Print a brief terminal summary: scope, verdict, output path, recommended next step.

---

## Rules

- **No-fix rule:** Code Dev and Auditor report only. Fixes go to `/sync-doc` or manual edits per verdict.
- **Scope mismatch stops immediately:** If Phase 1 finds no matching DESIGN docs, stop — no output file.
- **Output is permanent:** `docs/REVIEW-<scope>.md` is not temporary scaffolding.
