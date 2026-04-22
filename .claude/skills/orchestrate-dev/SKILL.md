---
name: orchestrate-dev
description: Execute a reviewed plan as a dev team — TL leads and codes alongside Dev subagents. TL assigns tasks, everyone implements, TL integrates, syncs docs, and appends delivery summary to TODO. Run after Gate 1 (PO + TL approved the plan).
---

# Dev Orchestration Workflow

**TL leads the dev team and codes alongside Dev subagents.** TL is not an overseer — TL takes tasks, writes code, and owns the critical path. Dev subagents handle parallel workstreams. Everyone applies the same execution standard (Steps 1–6). TL coordinates task assignment, resolves cross-task blockers, and runs integration after all devs complete.

**Invocation:** `/orchestrate-dev <slug>`

Reads `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`. Executes each task. Marks shipped tasks `✓ DONE` — never deletes mid-delivery. Appends delivery summary to plan. Plan moved to `completed/` after Gate 2 PASS.

**Consumes:** docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md. **Produces:** ✓ DONE marks + delivery summary appended to plan.

---

## Phase 1 — Load Plan

1. Locate the plan file: glob `docs/exec-plans/active/*-<slug>.md` to find the exact filename (the date prefix varies). If no match, stop with:
   ```
   ✗ No plan found at docs/exec-plans/active/*-<slug>.md. Run /orchestrate-plan first.
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
   - **When spawning each Dev subagent**, pass the task spec AND this explicit contract alongside it:
     ```
     Before implementing: read CLAUDE.md's Engineering Rules section and apply every item.
     The shortlist below is the minimum floor — the full Engineering Rules section
     contains additional constraints (tool patterns, display conventions, security) that apply.

     Before writing any code, echo back the files you will modify and confirm you will
     not touch anything else.

     Constraints (non-negotiable):
     - No mocks, fakes, or patching in tests (repo policy — real dependencies only)
     - No over-engineering — implement the minimal change that satisfies the spec
     - No dead code — remove any unreachable code your change leaves behind
     - No stale imports — remove any import unused after your edit
     - No lazy imports outside patterns already present in the file
     - Run pytest scoped to your affected test files. Fix any failures before reporting —
       do not surface red tests to TL. If a failure cannot be fixed without a design
       decision, stop and escalate rather than reporting a broken result.
     - For every new test written: (1) answer "if deleted, which regression goes undetected?" —
       if none, remove or fix; (2) every injected fixture (`tmp_path`, etc.) must be passed to
       a production function; (3) no duplicate code paths/invariants with existing tests;
       (4) assert the actual property, not just truthiness.
     - Report: files changed, what was done, test outcome (green or escalated), any ⚠ Extra file: paths
     ```
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
- **For tasks with a non-N/A `success_signal`** (user-facing behavior): `done_when` must be a test run or behavioral command, not only a grep or file-exists check. A grep confirms structure; it does not confirm the feature works. If `done_when` is grep-only for a user-facing task, stop and ask TL to strengthen it before proceeding.

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

After implementing, run the quality gate in fix mode:
```bash
scripts/quality-gate.sh lint --fix
```
Fix any remaining violations that auto-fix cannot resolve. Then read every changed file and check:

**Project coding rules:** Apply every item from the Engineering Rules loaded at pre-flight.

**Do NOT ship with any of the following — fix before Step 5:**

| Check | What to look for |
|-------|-----------------|
| **Dead code** | Any function, variable, or import defined but unreachable after this change — including renamed leftovers (e.g. `_OLD_NAME`, `_AGENT_FOR_RETRY`) |
| **Stale imports** | Anything imported but unused after the edit |
| **Misplaced lazy imports** | Lazy imports added outside patterns already present in the file |
| **No mocks or fakes** | Any test using mocks, patches, `monkeypatch`, or isolated helpers with no real services — blocking, remove it |
| **Scope creep** | Changes outside `files:` not announced as `⚠ Extra file:` |
| **Over-engineering** | Abstractions, utilities, or helpers not required by the task spec — if a junior wrote it and you'd push back, remove it |
| **Test quality** | For every new test: (1) deletion check — "which regression goes undetected if deleted?" (if none, remove/fix); (2) fixture wiring — `tmp_path` and other fixtures must be passed to a production function, not just declared; (3) no duplicates — same code path + invariant already covered by an existing test; (4) assertion strength — `assert result` must be `assert result == expected_value` |

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

**Total stop on failure.** When any task is blocked, halt execution for all remaining tasks — including tasks with no prerequisites. Mark every unstarted task as `— skipped` in the delivery summary. Partial delivery is not valid; the plan must be re-entered from the blocked task after the issue is resolved. **Exception: if a Dev subagent was already running on a fully independent task when TL's failure occurred, collect its output — do not discard it. Record it in the delivery summary as completed but not integrated; it may be reusable after the blocker is resolved.**

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

### Step 1 — Quality gate and test run

**Full delivery (all tasks passed):**
```bash
mkdir -p .pytest-logs
scripts/quality-gate.sh full 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```
Runs lint and the full test suite. Any failure = stop and fix. Use `scripts/quality-gate.sh lint --fix` for ruff violations; test failures require manual fixes.

**Partial delivery (any task blocked or skipped):** Run only test files touched by completed tasks — a full suite against incomplete code produces misleading failures.
```bash
mkdir -p .pytest-logs
uv run pytest <test_file_1> <test_file_2> -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-touched.log
```
Collect touched test files from completed tasks only. If none were touched, skip and record "no tests run — no test files touched by completed tasks."

Any failure = stop. Do RCA: read the failing test, trace to root cause in source, fix it, re-run. Never dismiss a failure as flaky without running it 3 times and identifying the specific cause.

Record: command run, number passed, number failed, any failure output.

### Step 2 — Sync docs

Determine scope before running:

- **Full scope** (run `/sync-doc` with no args): any task touches shared modules (config, agent registration, core dependency infrastructure), renames or removes a public API, or changes a schema. Cross-cutting changes may affect docs not directly related to the touched modules.
- **Narrow scope** (run `/sync-doc <doc>` for affected doc(s) only): all completed tasks are self-contained within a single module with no public API changes and no cross-module dependencies.

State the scope decision and rationale before running (e.g., "narrow — all tasks confined to `co_cli/tools/foo.py`, no API changes" or "full — TASK-2 renames a public tool").

Record result: clean / fixed (what was fixed).

### Step 3 — TODO lifecycle

For every task that reached ✓ pass, mark it done in the plan file — do not delete or remove it. The task record is preserved as a track log for debugging, troubleshooting, and potential revert.

Mark a completed task by prepending `✓ DONE` to its heading, e.g.:
```
### ✓ DONE — TASK-1: <title>
```

- **All tasks shipped (done or deferred):** mark all shipped tasks `✓ DONE`. Keep the file — it tracks the full delivery through Gate 2 PASS. Move to `completed/` after review-impl returns PASS verdict.
- **Deferred items remain:** mark shipped tasks done; leave deferred tasks unmarked. Same Gate 2 PASS move rule applies.
- **Partial delivery (some tasks blocked):** mark ✓ DONE for passed tasks; leave blocked and unstarted tasks unmarked.
- Apply this update before appending the delivery summary. The summary must describe the current plan state.
- If a passed task is not marked `✓ DONE` in the plan file, the delivery tracking is incomplete. Fix before proceeding.

---

## Phase 4 — Delivery Summary

Fix any test failures or doc sync inaccuracies before appending this summary — it reflects final state after fixes, not intermediate state.

Append to `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`:

```markdown
## Delivery Summary — <date>

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | <done_when text> | ✓ pass |
| TASK-2 | <done_when text> | ✗ blocked: <reason> |
| TASK-3 | <done_when text> | — skipped |

**Tests:** <full suite / touched files> — <N> passed, <N> failed
**Doc Sync:** clean / fixed (<what was fixed>)

**Overall: DELIVERED / BLOCKED**
<one sentence summary. If BLOCKED: list blocked tasks and whether the fix needs plan revision, code fix, or follow-up TODO.>
```

**DELIVERED** = all tasks passed, tests pass, independent review clean or minor only, doc sync clean or fixed.
**BLOCKED** = one or more tasks failed `done_when`, or tests still failing after fix attempts.

---

## Phase 5 — Ship

Run only after `/review-impl` returns PASS. Do not ship a DELIVERED-only delivery — Gate 2 requires the review-impl PASS verdict first.

Run `/ship <slug>`. Do not re-run `/orchestrate-dev` — that restarts task execution from the beginning.
