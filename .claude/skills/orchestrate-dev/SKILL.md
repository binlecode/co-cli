---
name: orchestrate-dev
description: Execute a reviewed plan end-to-end as Dev: implement each task, self-review, verify done_when, run tests, sync docs, and produce a delivery report. Run after Gate 1 (PO + TL approved the plan).
---

# Dev Orchestration Workflow

You are executing a reviewed, approved plan as **Dev**. Your job is to implement each task faithfully, self-review your own work, verify completion criteria, and produce a delivery report that the TL can use for Gate 2 sign-off.

**Invocation:** `/orchestrate-dev <slug>`

Reads `docs/TODO-<slug>.md`. Executes each task. Produces `docs/DELIVERY-<slug>.md`.

---

## Phase 1 — Load Plan

1. Read `docs/TODO-<slug>.md`. If the file does not exist, stop with:
   ```
   ✗ No plan found at docs/TODO-<slug>.md. Run /orchestrate-plan first.
   ```
2. Extract from each task:
   - `id` (TASK-1, TASK-2…)
   - `title`
   - `files:` list of paths to create or modify
   - `done_when:` the single verifiable completion criterion
   - `prerequisites:` (if present) — other task IDs that must complete first
3. Build execution order: topological sort respecting prerequisite chains. Tasks with no prerequisites run first. Tasks blocked by unfinished prerequisites are deferred.
4. Announce the task list and execution order before starting any work:
   ```
   ## Plan loaded: <feature name>

   Execution order:
   1. TASK-1: <title>
   2. TASK-2: <title> (requires TASK-1)
   3. TASK-3: <title>
   ```
5. Run `git status`. If uncommitted changes unrelated to this plan are present, warn before proceeding:
   ```
   ⚠ Uncommitted changes detected. Stash or commit unrelated work first
     if you want a clean rollback point (git reset --hard HEAD).
   ```

---

## Phase 2 — Execute Each Task

**Same-file batching:** When multiple tasks in the execution order modify the same file, read that file once before the first task that touches it. Do not re-read it between tasks — your earlier read is still valid. Make each task's edits in sequence without reloading the file between them.

### Pre-flight — Validate all tasks before executing any

Before writing a single line of code, validate every task in execution order:
- The task MUST have a non-empty `files:` list
- The task MUST have a non-empty `done_when:` criterion
- `done_when:` must be machine-verifiable (a command that returns a result, not a subjective
  judgment). Acceptable forms: `grep <pattern> <file>`, `test X passes`, `file <path> exists`,
  `file <path> contains field Z`, `doc section X matches code behavior`.

If any task fails validation, stop immediately with:
```
✗ Plan invalid: TASK-<id> missing `files:` / `done_when:` / non-verifiable done_when
Fix the plan before running /orchestrate-dev.
```

Fail-fast: stop at the first invalid task. Do not begin implementation until all tasks pass.

---

For each task in execution order:

### Step 1 — Announce

```
--- TASK <id>: <title> ---
```

### Step 2 — Read before writing

Read every file listed in `files:`. If a file does not exist yet (new file to create), note that and proceed. Understand the existing code before touching anything.

### Step 3 — Implement

Write or edit only the files listed in `files:`. Do not touch files outside that list unless a prerequisite file (e.g. a new module import) makes it strictly necessary — if so, announce the extra file explicitly.

---

### Step 4 — Self-review (inline Dev QA)

After implementing, read every changed file and check:

**CLAUDE.md anti-patterns:**
- `from X import *` — forbidden; always explicit imports
- `tool_plain()` used instead of `agent.tool()` with `RunContext`
- `settings` imported directly in tool files instead of via `ctx.deps`
- `Settings` object passed into `CoDeps` instead of flattening to scalar fields
- Approval logic inside a tool instead of `requires_approval=True`
- Mock or unit tests instead of functional tests
- `.env` file used instead of `settings.json` or env vars
- Tool returning raw `list[dict]` instead of `dict[str, Any]` with `display` field
- `CoDeps` holding a config object instead of flat scalar fields
- Trailing inline comments instead of comments on the line above

**Security:**
- Command injection (user input passed to shell without sanitization)
- Path traversal (unvalidated paths used in file operations)
- Missing input validation at system boundaries (external APIs, user input)
- SQL injection (string-concatenated queries instead of parameterized)

**Spec fidelity:**
- Does the implementation match the task description exactly?
- No scope creep — features not in the task spec must not be added
- No missing pieces — everything in the task spec must be present
- If you added to an existing test file, scan it for assertions that hardcode counts, sets, or
  enums your changes affect (e.g. `assert set(COMMANDS.keys()) == {...}`). Update stale
  assertions before verifying `done_when`.
- If `docs/reference/REVIEW-<scope>.md` exists for this feature area, cross-check changed
  files against any P0 Code Dev findings listed there. Do not introduce or deepen an
  inaccuracy that the REVIEW already flagged as blocking.

Fix any issues found before proceeding to the next step.

### Step 5 — Verify done_when

Execute the `done_when` criterion literally:

| done_when type | Action |
|----------------|--------|
| `test X passes` or `uv run pytest <path>` | Run `uv run pytest <path> -v` and check exit code |
| `grep <pattern> <file>` | Run the grep and confirm expected output is present |
| `file <path> exists` | Check the file exists at the path |
| `file <path> contains field Z` | Read the file and verify the field is present |
| `doc section X matches code behavior` | Read both and confirm they agree |

**If done_when FAILS:** Stop immediately. Report:
```
✗ TASK-<id> blocked: done_when failed
  Criterion: <done_when text>
  Failure: <what was wrong>
  Do not proceed to next task.
```

**Total stop on failure.** When any task is blocked, halt execution for all remaining tasks — including tasks with no prerequisites. Mark every unstarted task as `— skipped` in the delivery report. Partial delivery is not valid; the plan must be re-entered from the blocked task after the issue is resolved.

### Step 6 — Report task result

```
✓ TASK-<id> done: <done_when text>
```
or
```
✗ TASK-<id> blocked: <reason>
```

---

## Phase 3 — Integration

Run after all tasks have been attempted (or after the first blocked task if stopping early).

### Step 1 — Run tests

Collect all test files that were created or modified by completed tasks (tasks that reached ✓ pass). Skipped or blocked tasks do not contribute to this set.

If one or more test files were touched by completed tasks, run only those:
```
uv run pytest <test_file_1> <test_file_2> ... -v
```

If no test files were touched by any completed task, skip this step and record "no tests run — no test files touched."

Do not run the full suite after a partial execution — a full run against incomplete code would produce misleading failures.

Record: files run, number passed, number failed, any failure output.

### Step 2 — Sync docs

Run `/sync-doc` with no args (full scope). This ensures all DESIGN docs are checked for stale refs and inaccuracies introduced by this work — not just docs covering the specific modules touched. For cross-cutting changes (renames, API changes, schema updates), unrelated docs may reference the same symbols and would be missed by a narrower scope.

Record result: clean / fixed (what was fixed).

### Step 3 — TODO lifecycle

For every task that reached ✓ pass, remove it from `docs/TODO-<slug>.md`. Design details that belong in a DESIGN doc should be merged there (already handled by Step 2 sync).

- **All tasks shipped, no deferred items remain:** delete `docs/TODO-<slug>.md` entirely. An empty TODO is not kept.
- **All tasks shipped, deferred items remain:** remove shipped tasks; leave only unimplemented work and any explicit "Deferred" sections.
- **Partial delivery (some tasks blocked):** leave the blocked and unstarted tasks in place; remove only the tasks that reached ✓ pass.

### Step 4 — Fix before reporting

If any test fails or any doc sync found inaccuracies: fix those issues now, before writing the delivery report. The delivery report reflects the final state after fixes, not the intermediate state during execution.

---

## Phase 4 — Delivery Report

Write `docs/DELIVERY-<slug>.md`:

```markdown
# Delivery: <feature name>
Date: <ISO 8601 date>

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | <done_when text> | ✓ pass | |
| TASK-2 | <done_when text> | ✗ fail | <what broke> |
| TASK-3 | <done_when text> | — skipped | blocked by TASK-2 |

## Files Changed
- `<path>` — <one-line description of change>
- `<path>` — <one-line description of change>

## Tests
- Files run: <list> / none (no test files touched by completed tasks)
- Result: pass / fail (<N> passed, <N> failed)

## Doc Sync
- Result: clean / fixed (<what was fixed>)  (full-scope sync-doc run)

## Overall: DELIVERED / BLOCKED
<one sentence summary>
```

**DELIVERED** = all tasks passed, all tests pass, doc sync clean or fixed.
**BLOCKED** = one or more tasks failed their `done_when` criterion, or tests still failing after fix attempts.

**If BLOCKED — escalation note for TL:**
Blocked task(s): [list tasks that failed `done_when`]. Review needed:
- If the plan is wrong (bad done_when, missing step): revise `docs/TODO-<slug>.md` and re-run
- If the code is fixable without plan changes: fix and re-run from the blocked task
- If the issue requires new work: open a follow-up TODO

Use `git diff HEAD` to review what landed before the block. Clean up with
`git reset --hard HEAD` if a fresh start is preferred.

**Lifecycle:** This file is the artifact for Gate 2 (TL delivery check). After Gate 3 (PO acceptance), delete it — it is temporary scaffolding, not a permanent project record.

---

## Execution Rules

- Never skip a `done_when` check — every task must be verified, not assumed complete
- Never proceed past a blocked task — total stop, including independent tasks with no prerequisites
- Mark every unstarted task as `— skipped (blocked by TASK-N)` in the delivery report
- Never modify files outside a task's `files:` list without announcing the addition
- The self-review step is mandatory — do not mark a task done before completing it
- Delivery report is written even if the result is BLOCKED — the TL needs to see what failed
- Delete `DELIVERY-<slug>.md` after Gate 3 (PO acceptance) — it is temporary scaffolding
