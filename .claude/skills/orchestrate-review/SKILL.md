---
name: orchestrate-review
description: Co-system health check. TL resolves scope, spawns two parallel diagnostic subagents (Code Dev audits doc accuracy, Auditor checks TODO health), then synthesizes a verdict. Use after a delivery or before starting a new development cycle to confirm code and docs are in an honest state. For architecture tradeoff analysis against peer systems, use /orchestrate-research instead.
---

# Review Orchestration Workflow

**TL is the orchestrator.** Two subagents run in parallel — Code Dev and Auditor — each reviewing from their domain. TL reads both outputs and writes the final synthesis to `docs/reference/REVIEW-<scope>.md`.

**Invocation:** `/orchestrate-review <scope>`

`<scope>` is a feature area, module name, or `all`. Output is permanent — `docs/reference/REVIEW-<scope>.md` is not temporary scaffolding.

---

## Phase 1 — TL: Scope Resolution

### Step 1a — Resolve DESIGN docs

- If `scope = all`: glob `docs/DESIGN-*.md` — all files in scope.
- Otherwise: match `scope` as prefix or substring against filenames in `docs/DESIGN-*.md`. Example: `knowledge` matches `DESIGN-knowledge.md`; `skills` matches `DESIGN-skills.md`. Multiple docs may match.
- If no DESIGN doc filename matches, try a **content fallback**: grep each `docs/DESIGN-*.md` for the scope keyword in h1/h2 headings and Files-section entries. If at least two headings or a Files-section entry match, proceed with those docs and record that fallback was used (Step 1e will announce it). Only stop if content grep also finds nothing:
  ```
  ✗ No DESIGN docs matched scope "<scope>" (filename or content).
  Available: <list of docs/DESIGN-*.md>
  Available: <list of docs/TODO-*.md>
  Refine scope and re-run.
  ```

### Step 1b — Resolve TODO docs

- If `scope = all`: glob `docs/TODO-*.md`.
- Otherwise: apply the same prefix/substring match against `docs/TODO-*.md` filenames. If no filename matches, try the same content fallback (h1/h2 headings only) against `docs/TODO-*.md`.
- A scope may match DESIGN docs but no TODO docs even after fallback — that is valid. Record both sets.

### Step 1c — Identify source modules

For each matched DESIGN doc, read its **Files** section (section 4) and extract listed source paths. These are the files Code Dev reads for accuracy checking.

### Step 1d — Check for existing REVIEW doc

Check if `docs/reference/REVIEW-<scope>.md` already exists. If it does, note its date.

### Step 1e — Announce scope

Before spawning subagents, announce:
```
## Review scope: <scope>

DESIGN docs:     [list]
TODO docs:       [list]
Source modules:  [list from Files sections]
Existing REVIEW: [found at <path>, dated <date> / none]
```

If docs were resolved via content-grep fallback (no filename match), include: "Scope matched via content search in: [docs] — no dedicated DESIGN doc exists."

**Spawn both subagents (Phases 2a, 2b) simultaneously. Wait for both before Phase 3.**

---

## Phase 2a — Code Dev: Accuracy Audit

Code Dev reads each in-scope DESIGN doc and checks every factual claim against the corresponding source files. **Reports only — does not fix.**

### Step 2a-1 — Read docs and source

Read every in-scope DESIGN doc in full. Read every source file listed in their Files sections. Also read `co_cli/config.py` and `co_cli/deps.py` whenever any in-scope doc has a Config section — these are the ground truth for settings and env vars.

### Step 2a-2 — Check all claims

For each doc, check every factual claim. Inaccuracy patterns:

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

### Step 2a-3 — Severity

| Severity | Criteria |
|----------|---------|
| `blocking` | Would mislead a developer implementing or debugging: wrong schema, wrong flow, phantom feature, wrong tool registration, stale status on a shipped feature. When in doubt, classify as blocking. |
| `minor` | Incomplete but not wrong: missing config entry, missing coverage of a minor feature, stale file path in a doc section not actively used |

### Step 2a-4 — Return findings to TL

Return a structured report to TL:
```json
{
  "docs_checked": ["DESIGN-knowledge.md"],
  "findings": [
    {
      "id": "CD-A-1",
      "doc": "DESIGN-knowledge.md",
      "section": "<section heading>",
      "pattern": "<pattern name>",
      "claim": "<exact claim in the doc>",
      "reality": "<what the source code actually shows>",
      "severity": "blocking | minor"
    }
  ],
  "blocking_count": 0,
  "minor_count": 1,
  "summary": "<1-3 sentences>"
}
```

If no findings: `"findings": []` with summary "All docs checked. No inaccuracies found."

---

## Phase 2b — Auditor: TODO Health

Auditor checks every task in in-scope TODO docs for staleness, correctness, and readiness. **Reports only — does not modify TODO docs.**

**If no TODO doc matched scope** (e.g. the TODO was deleted on delivery):
- Check whether a `docs/DELIVERY-<scope>.md` exists. If so, spot-check 2–3 source files against its shipped claims and record the results.
- Scan active `docs/TODO-*.md` for any prerequisite dependencies on the shipped feature — note whether each is now satisfied.
- If neither a DELIVERY doc nor any active dependency exists, return `"docs_checked": []` with summary "No TODO docs in scope — feature fully shipped."

### Step 2b-1 — For each task in each in-scope TODO doc

Read the full TODO doc. For each task:

**a. Already shipped?** Check source files named in the task's `files:` list and related modules. If the code already implements what the task describes, mark `stale`.

**b. Stale assumption?** Does the task assume a function, field, or module that no longer exists as described?

**c. Contradicts current design?** Does the task propose something that conflicts with a shipped DESIGN doc?

**d. Well-formed?** Check:
- Has a non-empty `files:` list
- Has a non-empty `done_when:` criterion
- `done_when:` is machine-verifiable (grep, test pass, file exists — not "feature works", "developer satisfied")
- Scope is atomic: single agent session, ≤5 files, one verifiable outcome

### Step 2b-2 — Per-task check table

| Check | Pass condition |
|-------|---------------|
| `files:` present | At least one path listed |
| `done_when:` present | Non-empty |
| `done_when:` verifiable | Grep / test / file-exists — not subjective |
| Scope atomic | ≤5 files, one outcome |
| Not already shipped | Described behavior not in current source |
| No stale assumption | All referenced functions/fields/modules still exist as described |
| No design contradiction | Does not conflict with a current DESIGN doc |

### Step 2b-3 — Readiness verdict per TODO doc

| Verdict | Criteria |
|---------|---------|
| `ready_for_plan` | All tasks well-formed, no stale assumptions, nothing already shipped |
| `needs_cleanup` | Some tasks have well-formedness issues or minor staleness — fixable before planning |
| `blocked` | A prerequisite has not shipped when the TODO assumes it has, or a majority of tasks are invalid |

### Step 2b-4 — Return findings to TL

Return a structured report to TL:
```json
{
  "docs_checked": ["TODO-subagent-delegation.md"],
  "tasks": [
    {
      "id": "AU-1",
      "doc": "TODO-subagent-delegation.md",
      "task_title": "<task title>",
      "issues": [
        {
          "check": "already_shipped | stale_assumption | contradiction | malformed",
          "finding": "<what was found>"
        }
      ],
      "verdict": "ok | needs_attention | stale"
    }
  ],
  "verdicts": {
    "TODO-subagent-delegation.md": "ready_for_plan | needs_cleanup | blocked"
  },
  "summary": "<1-3 sentences>"
}
```

If no TODO docs matched scope: `"docs_checked": []`, summary "No TODO docs in scope."

---

## Phase 3 — TL: Synthesis

TL reads both subagent reports and writes the final output file.

### Step 3a — Overall verdict

| Verdict | Criteria |
|---------|---------|
| `HEALTHY` | Code Dev: no blocking findings. Auditor: all TODO docs `ready_for_plan` (or no TODO docs in scope). |
| `NEEDS_ATTENTION` | Code Dev: 1-2 minor inaccuracies. OR Auditor: some tasks need cleanup but no active-sprint blockers. Can proceed to plan, but fix first is preferred. |
| `ACTION_REQUIRED` | Code Dev: any blocking finding. OR Auditor: TODO `blocked` with a prerequisite dependency on planned work. |

**Conflict resolution:**
- 3+ minor findings across both agents → NEEDS_ATTENTION regardless of individual severity
- Auditor blocked + Code Dev clean → ACTION_REQUIRED only if blocked task is a prerequisite for planned scope; otherwise NEEDS_ATTENTION

### Step 3b — Priority table

| Priority | Criteria | Typical action |
|----------|---------|----------------|
| P0 | Blocks planned scope from shipping correctly | `/sync-doc <doc>` / fix TODO |
| P1 | Degrades quality; dev will produce a weaker result without it | Minor doc fix / cleanup |
| P2 | Housekeeping; won't affect current plan but accumulates debt | Minor doc fix / cleanup |

Only include rows with actual findings. Recommended next step names P0 actions only.

### Step 3c — Recommended next step (one sentence)

| Verdict | Template |
|---------|---------|
| HEALTHY | "Scope is clear — run `/orchestrate-plan <slug>` when ready." |
| NEEDS_ATTENTION + doc fix | "Fix [specific inaccuracy in DESIGN-X.md, section Y] before planning to prevent stale assumptions." |
| NEEDS_ATTENTION + blocked non-critical TODO | "Triage blocked item [task ID] in [TODO-doc] — confirm it is deferred before planning [scope]." |
| ACTION_REQUIRED + doc blocks plan | "Correct [DESIGN-X.md section Y] before planning — current content will misdirect implementation." |
| ACTION_REQUIRED + blocker dependency | "Unblock [task ID] in [TODO-doc] first — planned scope has a prerequisite dependency on it." |

### Step 3d — Write `docs/reference/REVIEW-<scope>.md`

TL authors the complete output file from both subagent reports:

```markdown
# REVIEW: <scope> — Co-System Health Check
_Date: <ISO 8601>_

## What Was Reviewed

- **DESIGN docs audited:** [list]
- **Source modules checked:** [list]
- **TODO docs checked:** [list]

---

## Code Dev — Doc Accuracy Audit

| Doc | Status | Finding |
|-----|--------|---------|
| DESIGN-foo.md | clean | No inaccuracies found |
| DESIGN-bar.md | inaccuracy (blocking) | [claim vs reality] |

**Overall: clean / N blocking, M minor**

---

## Auditor — TODO Health

| TODO doc | Verdict | Findings |
|----------|---------|---------|
| TODO-foo.md | ready_for_plan | — |
| TODO-bar.md | needs_cleanup | [task title]: missing done_when |

---

## TL Verdict

**Overall: HEALTHY / NEEDS_ATTENTION / ACTION_REQUIRED**

| Priority | Action | Source |
|----------|--------|--------|
| P0 | [action] | [CD-A-1 / AU-1] |
| P1 | [action] | [source] |

**Recommended next step:** [one sentence]
```

### Step 3e — Print verdict to terminal

```
## Review complete: <scope>

Verdict: HEALTHY | NEEDS_ATTENTION | ACTION_REQUIRED
Output:  docs/reference/REVIEW-<scope>.md

<recommended next step sentence>
```

---

## Execution Rules

- **Parallel spawn:** Phases 2a and 2b must be spawned simultaneously — do not run sequentially.
- **No-fix rule:** Code Dev and Auditor report only. They do not edit source files, DESIGN docs, or TODO docs. Fixes are delegated to `/sync-doc` or manual edits per TL verdict.
- **TL authors the output file:** Subagents return reports to TL; TL writes `docs/reference/REVIEW-<scope>.md` in one structured pass. No per-agent file creation.
- **Output file is permanent:** `docs/reference/REVIEW-<scope>.md` is not temporary scaffolding. Do not delete it after the review.
- **Scope mismatch stops immediately:** If Phase 1 finds no matching DESIGN docs, stop before creating any output file.
