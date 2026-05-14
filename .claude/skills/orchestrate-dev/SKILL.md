---
name: orchestrate-dev
description: Execute a reviewed plan as a dev team — TL leads and codes alongside Dev subagents. TL assigns tasks, everyone implements, TL integrates, syncs docs, and appends delivery summary to plan. Run after Gate 1 (PO + TL approved the plan).
---

# Dev Orchestration Workflow

**TL leads and codes alongside Dev subagents** — TL takes tasks, not just oversight. Dev subagents handle parallel workstreams. Everyone executes Steps 1–6 for their assigned tasks.

**Invocation:** `/orchestrate-dev <slug>`

**Consumes:** `docs/exec-plans/active/*-<slug>.md`. **Produces:** ✓ DONE marks + delivery summary appended to plan.

---

## Phase 1 — Load and Assign

1. Glob `docs/exec-plans/active/*-<slug>.md`. If no match: `✗ No plan found — run /orchestrate-plan first.`
2. Extract each task: `id`, `title`, `files:`, `done_when:`, `prerequisites:`. Build execution order via topological sort on prerequisites.
3. **Pre-flight:** every task must have a non-empty `files:` and a machine-verifiable `done_when:`. If any task fails: `✗ Plan invalid: TASK-<id> missing files/done_when — fix the plan first.` Stop at the first invalid task.
4. **Assign:** TL takes critical-path and cross-cutting tasks (shared modules, schema changes, renames). Spawn one Dev subagent per independent parallel group. Never spawn a Dev subagent for a task whose prerequisites are unfinished.
5. Run `git status`. Warn if uncommitted changes unrelated to this plan are present.
6. Announce team and execution order before any work begins:
   ```
   ## Team
   TL:    TASK-1, TASK-3
   Dev-1: TASK-2 (requires TASK-1 — spawned after TL completes TASK-1)
   Dev-2: TASK-4 (independent — spawned in parallel with TL)
   ```

**When spawning a Dev subagent**, pass the task spec and this contract:
```
Apply Engineering Rules from CLAUDE.md in full.
Only modify files listed in `files:`. Announce any extra file: ⚠ Extra file: <path> — <reason>
No mocks, fakes, or patching in tests.
Run pytest scoped to your affected test files. Fix failures before reporting.
If a failure requires a design decision, escalate — do not continue past a broken result.
Report: files changed, what was done, test outcome.
```

---

## Phase 2 — Execute Each Task

TL and Dev subagents run Steps 1–6 for their assigned tasks. Independent tasks run in parallel; a Dev task blocked on a TL prerequisite is spawned only after TL completes that prerequisite.

**Same-file batching:** when multiple tasks touch the same file, read it once before the first task and make all edits in sequence without reloading between them.

TL collects all Dev results before Phase 3. Scan Dev outputs for `⚠ Extra file:` lines — include those files in Phase 3 scope.

### Step 1 — Announce
```
--- TASK <id>: <title> ---
```

### Step 2 — Read before writing
Read every file in `files:`. Note missing files (new). Understand existing code before touching anything.

### Step 3 — Implement
Write or edit only the files in `files:`. Announce any extra file touched.

### Step 4 — Self-review
Run `scripts/quality-gate.sh lint --fix`. Fix any violations before Step 5.

### Step 5 — Verify done_when
Execute the `done_when` criterion literally. On failure, stop immediately:
```
✗ TASK-<id> blocked: done_when failed
  Criterion: <text>
  Failure: <what was wrong>
```
**Halt all remaining tasks on any failure.** If a fully independent Dev subagent was already running, collect its output as "completed but not integrated."

### Step 6 — Report
```
✓ TASK-<id> done: <done_when text>
```
or
```
✗ TASK-<id> blocked: <reason>
```

---

## Phase 3 — Integration

Run after all tasks complete (or at first blocked task).

1. **Lint + scoped tests:**
   ```bash
   mkdir -p .pytest-logs
   scripts/quality-gate.sh lint 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-lint.log
   uv run pytest <touched test files> -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-scoped.log
   ```
   Any failure = stop. RCA, fix, re-run. Do not run the full suite — that's review-impl's job.

2. **Sync docs:** run `/sync-doc` (full scope) if any task touched shared modules, renamed a public API, or changed a schema. Narrow (`/sync-doc <doc>`) otherwise. State scope decision before running.

3. **Mark done:** prepend `✓ DONE` to each passing task heading in the plan. Do not delete tasks.

---

## Phase 4 — Delivery Summary

Fix any remaining lint or test failures before appending. Summary reflects final state.

Append to the plan:

```markdown
## Delivery Summary — <date>

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | <criterion> | ✓ pass |
| TASK-2 | <criterion> | ✗ blocked: <reason> |
| TASK-3 | <criterion> | — skipped |

**Tests:** scoped — <N> passed, <N> failed
**Doc Sync:** clean / fixed (<what was fixed>)

**Overall: DELIVERED / BLOCKED**
<one sentence. If BLOCKED: which tasks, and whether fix needs plan revision, code change, or follow-up.>
```

**DELIVERED** = all tasks passed, lint clean, scoped tests green, doc sync clean or fixed.
**BLOCKED** = any task failed `done_when`, or tests still failing after fix attempts.
