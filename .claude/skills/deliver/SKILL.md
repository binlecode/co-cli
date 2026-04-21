---
name: deliver
description: Lightweight solo delivery — read task/TODO, implement, test-gate, self-review, stage only related files, commit with version bump. No subagent orchestration. Use for focused single-dev work that doesn't need the full TL/PO/Dev cycle.
---

# Deliver

Solo implementation skill. No subagents. Same quality bar as orchestrate-dev, none of the orchestration overhead.

**Invocation:** `/deliver <slug>` or `/deliver` (ad-hoc, without a TODO doc)

- With slug: reads `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` as the task list.
- Without slug: implements the task described inline in the prompt.

---

## Phase 1 — Load

If a slug was given:
1. Locate the plan file: glob `docs/exec-plans/active/*-<slug>.md` to find the exact filename. If missing, stop: `✗ No plan at docs/exec-plans/active/*-<slug>.md`.
2. Extract tasks in order: id, title, files, done_when, prerequisites.
3. Announce task list before touching anything.

If no slug: confirm what will be implemented before starting.

Check `git status`. Warn if unrelated uncommitted changes are present.

---

## Phase 2 — Execute Each Task

For each task, in order:

### Step 1 — Announce
```
--- TASK <id>: <title> ---
```

### Step 2 — Read before writing
Read every file in `files:`. Understand existing code before editing anything.

### Step 3 — Implement
Edit only files in `files:`. If a strictly necessary extra file must be touched, announce it:
```
⚠ Extra file: <path> — <reason>
```

**co-cli-specific rules (apply to every edit):**
- No stale imports — remove any import unused after your edit
- No dead code — remove unreachable code your change leaves behind
- No over-engineering — implement the minimal change that satisfies the spec; no speculative abstractions
- No mocks or fakes in tests — real services, real SQLite, real filesystem (repo policy)
- Comments go on the line above, never trailing at end of code line
- Use the project `console` object for terminal output; never hardcode color names

### Step 3b — Test quality gate (if new tests were written)

For each new test function added, answer:
1. **Deletion check**: if this test is deleted, which specific production regression goes undetected?
   If the answer is "none obvious", the test is waste — either remove it or fix the assertion.
2. **Fixture wiring**: every `tmp_path` or injected fixture must be passed to a production function.
   If not, the assertion trivially passes — remove or fix the test.
3. **No duplication**: does an existing test already exercise this exact code path and invariant?
   If yes, remove the weaker one.
4. **Assertion strength**: every assertion must check the actual property, not just truthiness.
   `assert result` → `assert result == expected_value`.

### Step 4 — Lint fix
```bash
scripts/quality-gate.sh lint --fix
```
Fix any remaining violations auto-fix can't resolve.

### Step 5 — Run tests (scoped)
```bash
uv run pytest <affected test files> -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task.log
```
If any test fails: diagnose from traceback, fix, re-run. Do not move to the next task with a red suite.

### Step 6 — Verify done_when
Execute the `done_when` criterion literally. If it fails, stop and report — do not proceed.

### Step 7 — Report
```
✓ TASK-<id> done: <done_when text>
```

---

## Phase 3 — Integration

After all tasks pass:

### Full quality gate
```bash
scripts/quality-gate.sh full 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```
Fix any failures before continuing.

### Self-review (concise)
Read every changed file. Check:
- No dead code, stale imports, or over-engineering left behind
- No scope creep beyond announced `⚠ Extra file:` paths
- No mocks or fakes in tests
- Spec fidelity: does the diff match the task description exactly

Report findings as a short table (severity, file:line, description). Stop at blocking issues; proceed on minor.

### Doc sync
Run `/sync-doc` scoped to affected docs, or full if cross-cutting changes were made.

---

## Phase 4 — Ship

**Format gate** — run before staging anything:
```bash
scripts/quality-gate.sh lint --fix
```
Catches any formatting issues introduced by self-review fixes or doc-sync edits after Phase 3's quality gate.

Mark completed tasks `✓ DONE` in the plan file (if a slug was used).

**Version bump** — read current version from `pyproject.toml`, bump patch by 2 (feature) or 1 (bugfix):
- Even patch = feature/enhancement
- Odd patch = bugfix

**Archive plan** — if a slug was used and review-impl returns PASS, move the plan to completed:
```
git mv docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md docs/exec-plans/completed/
```

**Commit** — stage only delivery files + `pyproject.toml` + the plan archive move (if applicable). Never include unrelated files. If anything is ambiguous, ask before staging.

Commit message:
- `feat:` / `fix:` / `refactor:` prefix
- One-line subject
- 3–6 body bullets for significant changes
- Ends with `Co-Authored-By: Claude <noreply@anthropic.com>`

---

## Research Rules (applies when this delivery involves peer comparison)

- Always use `fork-claude-code` at `~/workspace_genai/fork-claude-code` — not public `claude-code`. Confirm the path exists before reading.
