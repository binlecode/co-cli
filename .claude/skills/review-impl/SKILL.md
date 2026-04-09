---
name: review-impl
description: Deep self-correcting implementation review. Evidence-first scan, auto-fix loop, full test suite with RCA, behavioral verification. Replaces the G2→fix→G3 manual cycle — after PASS, ship directly.
---

# review-impl

**Invocation:** `/review-impl <slug>`

Reads `docs/TODO-<slug>.md`. Runs a deep, self-correcting review of every `✓ DONE` task: evidence-first spec check, quality scan, auto-fix of blocking issues, full test suite with mandatory RCA, doc sync, and behavioral verification. Appends verdict to TODO. After PASS, no further gate needed — ship directly.

**Default stance: issues exist. PASS is earned, not assumed.**

**Consumes:** `docs/TODO-<slug>.md`, source files in each task's `files:`. **Produces:** appends `## Implementation Review — <date>` to `docs/TODO-<slug>.md`.

---

## Phase 1 — Load

1. Read `docs/TODO-<slug>.md`. If not found, stop:
   ```
   ✗ No TODO at docs/TODO-<slug>.md.
   ```
2. Extract all `✓ DONE` tasks. If none, stop:
   ```
   ✗ No completed tasks found. Run /orchestrate-dev first.
   ```
3. Load the full Engineering Rules section from CLAUDE.md. Keep in context for every phase — do not re-read per task.
4. Announce scope:
   ```
   Reviewing: TASK-1, TASK-2, TASK-3
   Stance: issues exist — PASS is earned
   ```

---

## Phase 2 — Evidence Collection

**For each `✓ DONE` task**, cold-read the implementation. No relying on what was written earlier in this session — approach the code fresh.

### A — Read files

Read every file listed in `files:`. If a file does not exist: immediate blocking finding.

### B — Spec fidelity with evidence

For every requirement in the task description and `done_when`:

- **Find the exact file:line that confirms it is implemented.** Do not accept a description — read the code.
- **Trace the call path** for every public function or method changed in this delivery: identify caller → callee chain. Confirm the chain exists in source, not just in comments or docs. Do not stop at functions that don't look like "integration points" — trace all of them.
- **Check for existing implementations** that already solve the stated problem. If the code was already there before this task, the implementation may be redundant or conflicting.
- **Run `done_when` literally** — same standard as orchestrate-dev Step 5. Do not assume it passes because the delivery run verified it. Re-execute now.

Record for each requirement: file:line evidence, or a finding if absent.

### C — Quality gate (lint + types)

```bash
scripts/quality-gate.sh types
```
Any violation is a blocking finding. Auto-fix ruff with `scripts/quality-gate.sh lint --fix` in Phase 4. Pyright errors require manual fixes.

### D — Convention checklist

Check every changed file against CLAUDE.md's Engineering Rules section. Each violation is a potential blocking finding. Key areas:

- **Tool conventions**: correct registration pattern, structured return type, deps from `ctx.deps`, no global state
- **Test policy**: no mocks, fakes, or patching — real services only
- **Code hygiene**: dead code, stale imports, misplaced lazy imports, scope creep (files outside `files:` not announced as `⚠ Extra file:`)
- **Over-engineering**: abstractions or helpers not required by the spec
- **Display**: terminal output via the project's shared `console` — not `print()` or hardcoded color names
- **Security**: command injection, path traversal, SQL injection, missing input validation at system boundaries

---

## Phase 3 — Adversarial Self-Review

Before proceeding to fixes, challenge every finding from Phase 2:

**For each PASS:**
- Did I actually read the code at that file:line, or did I pattern-match on the function name?
- If I cannot cite the specific line, it is not a confirmed pass — re-read.

**For each FAIL:**
- Is this actually wrong, or is it working-as-intended and I missed context?
- Would the fix I have in mind count as over-engineering?
- Does this violate a hard Engineering Rule, or is it a style preference?

Downgrade false positives to minor or remove them. Upgrade under-detected issues. Only blocking findings that survive this challenge proceed to Phase 4.

**Blocking** = spec requirement missing, `done_when` fails, hard Engineering Rule violated (unit test, wrong tool pattern, global state), security issue.
**Minor** = style, non-required improvement, partial convention drift with no functional impact.

---

## Phase 4 — Auto-Fix Loop

For each blocking finding, in order:

1. **Apply the minimal idiomatic fix.** No new abstractions. No over-engineering. The fix changes only what is wrong.
2. **Architectural decision required?** If the fix would require restructuring a module, changing a public API, or adding new dependencies — stop and escalate:
   ```
   ✗ ESCALATE: <finding> requires architectural decision. Cannot auto-fix.
   Description: <what needs to change and why>
   Recommended next step: <revise TODO / open follow-up / manual TL decision>
   ```
   Do not guess. Do not apply a workaround.
3. **Re-verify `done_when`** for the affected task after the fix.
4. **Re-run tests scoped to the affected file:**
   ```
   uv run pytest <affected_test_file> -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-fix-verify.log
   ```
5. Loop until all blocking findings are resolved or escalated.

---

## Phase 5 — Full Test Suite with Mandatory RCA

Run the full test suite:
```
uv run pytest -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-review-impl.log
```

**Any failure = stop immediately. Do not proceed. Do RCA:**

1. Read the failing test.
2. Read the full error and traceback.
3. Trace to root cause in source — not in the test.
4. Fix the root cause. Never modify a test to make it pass unless the test itself is stale (API removed or renamed).
5. Re-run the full suite.
6. Repeat until green.

**Never dismiss a failure as flaky** without:
- Running it 3 times to confirm non-determinism
- Identifying the specific race condition or external dependency causing it
- Documenting it as a known flaky test with evidence

Only proceed to Phase 6 with a fully green suite.

---

## Phase 6 — Doc Sync

Determine scope (same rule as orchestrate-dev Phase 3 Step 3):
- **Full** (`/sync-doc`): any task touches shared modules, renames a public API, or changes a schema.
- **Narrow** (`/sync-doc <doc>`): all tasks are self-contained within a single module with no API changes.

Run sync-doc. Record: clean / fixed (what was fixed).

---

## Phase 7 — Final Re-scan

After all fixes and tests are green, run the quality gate one final time:
```bash
scripts/quality-gate.sh types
```
Then re-scan every changed file one more time:

- Dead code introduced during fixes
- Stale imports left by fix edits
- Misplaced lazy imports
- Any test using mocks or fakes introduced during fix (blocking — remove it)
- Doc-code mismatches in changed file docstrings or inline comments

Fix anything found. This catches what sub-agents and fix loops leave behind.

---

## Phase 8 — Behavioral Verification

**Required for any task that modifies user-facing surface** (CLI commands, tools visible in chat, output formatting, config loading, bootstrap, status).

Run the system and confirm user-visible behavior:

```bash
uv run co status          # always — confirms system starts and health checks pass
uv run co chat            # if chat loop or tool behavior changed — brief interaction
uv run co logs            # if observability or tracing changed
```

For each run:
- Confirm it starts without error
- Exercise the specific changed behavior from the task spec
- Confirm output matches the spec — not just "no crash"

**Verify `success_signal`:** For each task with a non-N/A `success_signal`, confirm the stated user-observable outcome during behavioral verification. This is not a second `done_when` — it is a smoke check that the delivered feature produces the effect the user would notice. Record the result in the Behavioral Verification section of the verdict (e.g. "`success_signal` verified: user sees X when Y").

If behavioral verification fails: treat as a blocking finding, go back to Phase 4.

If no user-facing surface was changed: skip and note "no user-facing changes — behavioral verification skipped."

---

## Phase 9 — Verdict

Only append after: all blocking findings resolved, test suite green, doc sync complete, final re-scan clean, behavioral verification passed or skipped with justification.

Append to `docs/TODO-<slug>.md`:

```markdown
## Implementation Review — <date>

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | <criterion> | ✓ pass | foo.py:42 — X implemented as specified |
| TASK-2 | <criterion> | ✓ pass | bar.py:15 — call path A→B→C confirmed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Dead code: `_old_fn` never called | foo.py:88 | blocking | Removed |
| Stale import: `from x import y` | bar.py:3 | blocking | Removed |
| Minor: doc comment references old name | baz.py:12 | minor | Updated |

_(If no issues found: "No issues found.")_

### Tests
- Command: `uv run pytest -v`
- Result: N passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: full / narrow — <rationale>
- Result: clean / fixed: <what>

### Behavioral Verification
- `uv run co status`: ✓ healthy
- `uv run co chat`: ✓ <what was verified>
_(or: "No user-facing changes — skipped.")_

### Overall: PASS / ESCALATE
<one sentence. If ESCALATE: list each unresolved finding with recommended next step.>
```

---

## Rules

- **Evidence or it didn't happen**: every pass and every fail requires a file:line citation. Pattern-matching on names is not evidence.
- **Adversarial default**: do not look for reasons to pass. Look for reasons to fail — and verify each one survives the self-review challenge.
- **Auto-fix, don't report**: blocking findings are fixed here, not handed back to the TL as a to-do list. The verdict is clean or escalated — never "here are issues for you to fix."
- **Architectural decisions escalate**: if fixing correctly requires a decision beyond "change this line," stop and surface it. Never apply a workaround to avoid escalation.
- **No mocks or fakes under any circumstances**: if a fix tempts you to mock a dependency, the production API is wrong — fix the API.
- **RCA is not optional**: a failing test that is "probably flaky" is a failing test. Stop, investigate, fix root cause.
- **PASS means ship-ready**: after PASS, the TL can commit and ship. No further review gate is required.
