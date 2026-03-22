---
name: orchestrate-dev
description: Execute a reviewed plan as a dev team — TL leads and codes alongside Dev subagents. TL assigns tasks, everyone implements, TL integrates, syncs docs, and ships the delivery report. Run after Gate 1 (PO + TL approved the plan).
---

# Dev Orchestration Workflow

**TL leads the dev team and codes alongside Dev subagents.** TL is not an overseer — TL takes tasks, writes code, and owns the critical path. Dev subagents handle parallel workstreams. Everyone applies the same execution standard (Steps 1–6). TL coordinates task assignment, resolves cross-task blockers, and runs integration after all devs complete.

**Invocation:** `/orchestrate-dev <slug>`

Reads `docs/TODO-<slug>.md`. Executes each task. Marks shipped tasks `✓ DONE` — never deletes mid-delivery. Produces `docs/DELIVERY-<slug>.md`. Deletes TODO alongside DELIVERY at Gate 3.

**Consumes:** docs/TODO-<slug>.md. **Produces:** docs/DELIVERY-<slug>.md (temporary)

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
6. **Assess team size and assign tasks.** Group tasks by independence (tasks with no shared prerequisites can run in parallel). Assign based on scope:
   - 1–2 tasks → TL handles all tasks alone
   - 3+ independent task groups → TL takes the critical-path tasks; spawn one Dev subagent per additional parallel group
   - TL takes tasks touching cross-cutting concerns (renames, schema changes, shared modules) — these benefit from TL's full plan context
   - **Execution timing follows the dependency graph, not role:** TL and Dev subagents whose tasks are fully independent run in parallel. When a Dev task has a prerequisite owned by TL, TL completes that prerequisite first, then spawns the Dev subagent. Never spawn a Dev subagent for a task whose prerequisites are unfinished.
   - Announce assignments and sequencing before any work begins:
     ```
     ## Team
     TL:    TASK-1, TASK-3
     Dev-1: TASK-2 (requires TASK-1 — spawned after TL completes TASK-1)
     Dev-2: TASK-4 (independent — spawned in parallel with TL)
     ```

---

## Phase 2 — Execute Each Task

Each team member (TL and every Dev subagent) executes the same Steps 1–6 for their assigned tasks. **Whether TL and Dev run in parallel or sequentially depends on the dependency graph, not on role:** independent tasks run in parallel; a Dev task whose prerequisite is owned by TL is spawned only after TL completes that prerequisite. TL collects all Dev results before Phase 3. Before starting Phase 3, TL scans Dev subagent outputs for lines matching `⚠ Extra file: <path>` — these extra files must be included in the Phase 3 integration diff and test scope alongside planned `files:` entries.

**Same-file batching:** When multiple tasks in the execution order modify the same file, read that file once before the first task that touches it. Do not re-read it between tasks — your earlier read is still valid. Make each task's edits in sequence without reloading the file between them.

### Pre-flight — Validate all tasks before executing any

**Load project coding rules once before executing any task:**

Review CLAUDE.md's Engineering Rules section (imports, tool patterns, testing rules, display conventions, anti-patterns). Keep these in context for all Step 4 self-review checks — do not re-read per task.

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

If a task requires more than 20 tool calls without clear progress, stop and report it as blocked rather than continuing — runaway tasks should escalate, not spiral.

---

For each task in execution order:

### Step 1 — Announce

```
--- TASK <id>: <title> ---
```

### Step 2 — Read before writing

Read every file listed in `files:`. If a file does not exist yet (new file to create), note that and proceed. Understand the existing code before touching anything.

### Step 3 — Implement

Write or edit only the files listed in `files:`. Do not touch files outside that list unless a prerequisite file (e.g. a new module import) makes it strictly necessary — if so, announce it using exactly this format:
```
⚠ Extra file: <path> — <reason>
```

---

### Step 4 — Self-review (inline Dev QA)

After implementing, read every changed file and check:

**Project coding rules:** Apply every item from the Engineering Rules loaded at pre-flight.

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

Fix any issues found before proceeding to the next step.

### Step 5 — Verify done_when

Execute the `done_when` criterion literally:

| done_when type | Action |
|----------------|--------|
| `test X passes` or `uv run pytest <path>` | Run `uv run pytest <path> -v 2>&1 \| tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task.log` and check exit code |
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

**Total stop on failure.** When any task is blocked, halt execution for all remaining tasks — including tasks with no prerequisites. Mark every unstarted task as `— skipped` in the delivery report. Partial delivery is not valid; the plan must be re-entered from the blocked task after the issue is resolved. **Exception: if a Dev subagent was already running on a fully independent task when TL's failure occurred, collect its output — do not discard it. Report it in the delivery report as completed but not integrated; it may be reusable after the blocker is resolved.**

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

**Full delivery (all tasks passed):** Run the full test suite to catch cross-module regressions.
A task can pass its own `done_when` while silently breaking an unrelated module that imports the
changed code. Touched-files-only testing misses this.
```
uv run pytest -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

**Partial delivery (any task blocked or skipped):** Run only test files touched by completed
(✓ pass) tasks — a full suite against incomplete code produces misleading failures.
```
uv run pytest <test_file_1> <test_file_2> ... -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-touched.log
```
Collect touched test files from completed tasks only. If none were touched, skip and record
"no tests run — no test files touched by completed tasks."

Record: command run, number passed, number failed, any failure output.

### Step 2 — Independent code review

Spawn a reviewer subagent. Pass it exactly:
- The output of `git diff HEAD` scoped to files changed by completed tasks
- The task specs (id, title, done_when) for all completed tasks
- The Engineering Rules section from CLAUDE.md

The reviewer has no access to the implementation conversation — cold read only.

**Reviewer checks:**
- Anti-patterns from CLAUDE.md not caught by self-review (blind spots from implementation context)
- Spec fidelity: does the diff implement exactly the task spec, no more, no less
- Security: command injection, path traversal, SQL injection, missing input validation
- Cross-task coherence: do the combined changes from all tasks form a consistent whole

**Reviewer output** (append to `docs/DELIVERY-<slug>.md` under `## Independent Review`):

```markdown
## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| ...  | ...     | blocking/minor | TASK-N |

**Overall: clean / <N> blocking / <N> minor**
```

If any blocking findings: fix before writing the final delivery report. Minor findings:
record and proceed — TL decides at Gate 2.

Record result: clean / N blocking / N minor.

### Step 3 — Sync docs

Determine scope before running:

- **Full scope** (run `/sync-doc` with no args): any task touches shared modules (config, agent registration, core dependency infrastructure), renames or removes a public API, or changes a schema. Cross-cutting changes may affect docs not directly related to the touched modules.
- **Narrow scope** (run `/sync-doc <doc>` for affected doc(s) only): all completed tasks are self-contained within a single module with no public API changes and no cross-module dependencies.

State the scope decision and rationale before running (e.g., "narrow — all tasks confined to `co_cli/tools/foo.py`, no API changes" or "full — TASK-2 renames a public tool").

Record result: clean / fixed (what was fixed).

### Step 4 — TODO lifecycle

For every task that reached ✓ pass, mark it done in `docs/TODO-<slug>.md` — do not delete or remove it. The task record is preserved as a track log for debugging, troubleshooting, and potential revert.

Mark a completed task by prepending `✓ DONE` to its heading, e.g.:
```
### ✓ DONE — TASK-1: <title>
```

- **All tasks shipped (done or deferred):** mark all shipped tasks `✓ DONE`. Keep the file — it tracks the full delivery through Gate 3. Delete it only after PO acceptance at Gate 3, in the same session that deletes `DELIVERY-<slug>.md`.
- **Deferred items remain:** mark shipped tasks done; leave deferred tasks unmarked. Same Gate 3 deletion rule applies.
- **Partial delivery (some tasks blocked):** mark ✓ DONE for passed tasks; leave blocked and unstarted tasks unmarked.
- Apply this update before writing the delivery report. The report must describe the current TODO state.
- If a passed task is not marked `✓ DONE` in `docs/TODO-<slug>.md`, the delivery tracking is incomplete. Fix before proceeding.

---

## Phase 4 — Delivery Report

Fix any test failures or doc sync inaccuracies before writing this report — it reflects final state after fixes, not intermediate state.

Before writing, read the delivery report template now:

> Read: .claude/skills/orchestrate-dev/assets/DELIVERY-template.md

Use that template to write `docs/DELIVERY-<slug>.md`. Fill in every section with the actual results from this delivery run.

**DELIVERED** = all tasks passed, all tests pass, independent review clean or minor only, doc sync clean or fixed.
**BLOCKED** = one or more tasks failed their `done_when` criterion, or tests still failing after fix attempts.

**If BLOCKED — escalation note for TL:**
Blocked task(s): [list tasks that failed `done_when`]. Review needed:
- If the plan is wrong (bad done_when, missing step): revise `docs/TODO-<slug>.md` and re-run
- If the code is fixable without plan changes: fix and re-run from the blocked task
- If the issue requires new work: open a follow-up TODO

**Lifecycle:** This file is the artifact for Gate 2 (TL delivery check) and Gate 3 (PO acceptance). It must include an explicit `## Gate 3 Cleanup` section. After Gate 3, delete `docs/DELIVERY-<slug>.md` in the same session — it is temporary scaffolding, not a permanent project record.
