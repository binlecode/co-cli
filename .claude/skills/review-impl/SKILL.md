---
name: review-impl
description: Deep self-correcting implementation review. Evidence-first scan, auto-fix loop, full test suite with RCA, behavioral verification. Replaces the G2→fix→G3 manual cycle — after PASS, TL reads verdict at Gate 2 and ships.
---

# review-impl

**Invocation:** `/review-impl <slug>`

Reads `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`. Runs a deep, self-correcting review of every `✓ DONE` task: evidence-first spec check, quality scan, auto-fix of blocking issues, full test suite with mandatory RCA, and behavioral verification. Appends verdict to plan. After PASS, TL reads the verdict at Gate 2 and runs `/ship` — no further automated review is required.

**Default stance: issues exist. PASS is earned, not assumed.**

**Consumes:** `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`, source files in each task's `files:`. **Produces:** appends `## Implementation Review — <date>` to the plan file.

---

## Phase 1 — Load

1. Locate the plan file: glob `docs/exec-plans/active/*-<slug>.md`. If not found, stop:
   ```
   ✗ No plan at docs/exec-plans/active/*-<slug>.md.
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

### Pre-step — Scope-creep scan and lint

Run before spawning subagents:

```bash
git diff --name-only HEAD
```

Compare against the union of all `files:` across every `✓ DONE` task. For each file in the diff that is **not** in any task's `files:`, immediately record:
```
⚠ Extra file: <path> — not declared in any task's files:
```
Carry these into the findings list. Do not block here.

Also run the global lint check:
```bash
scripts/quality-gate.sh lint
```
Any violation is a blocking finding. Carry into findings list.

### Per-task evidence (parallel subagents)

Spawn **one subagent per `✓ DONE` task** in parallel. Declare tools: `Read, Bash, Grep`. Each subagent receives the task description, `done_when`, `files:` list, the Engineering Rules section from CLAUDE.md, **and the full text of `.agent_docs/review.md`** — read it once in the orchestrator and pass it by value into every subagent prompt so the rules are guaranteed present in each reviewer's context (do not pass a bare file pointer).

Each subagent runs:

#### A — Read files

Read every file listed in `files:`. If a file does not exist: immediate blocking finding.

#### B — Spec fidelity with evidence

For every requirement in the task description and `done_when`:

- **Find the exact file:line that confirms it is implemented.** Do not accept a description — read the code.
- **Trace the call path** for every public function or method changed in this delivery: identify caller → callee chain. Confirm the chain exists in source, not just in comments or docs. Do not stop at functions that don't look like "integration points" — trace all of them.
- **Check for existing implementations** that already solve the stated problem. If the code was already there before this task, the implementation may be redundant or conflicting.
- **Run `done_when` literally** — same standard as orchestrate-dev Step 5. Do not assume it passes because the delivery run verified it. Re-execute now.

Record for each requirement: file:line evidence, or a finding if absent.

#### C — Convention checklist

Check every file listed in `files:` against CLAUDE.md's Engineering Rules. Each violation is a potential blocking finding. Key areas:

- **Tool conventions**: correct registration pattern, structured return type, deps from `ctx.deps`, no global state
- **Test policy**: no mocks, fakes, or patching — real services only
- **Test bloat** (tests in `files:` only — do not re-sweep the suite): apply the stub-litmus to each new/changed `def test_*` — mentally replace the production function under test with `return []`/`return None`; if the test still passes it is structural/weak (`.agent_docs/testing.md` *Assertion strength*; clean-tests rule 4) → flag to strengthen to assert the observed value. Also flag a new test that duplicates an existing test's branch + observable, adding no unique failure mode (clean-tests rules 5/6) — prove via the same-branch read before flagging. Honor the two stub-litmus exceptions (defensive crash-guard whose correct output genuinely is empty/None; the inclusion half of an inclusion/exclusion pair). Out of scope: Criticality-gate Low-tier volume trimming — that stays a `/clean-tests` decision.
- **Code hygiene**: dead code, stale imports, misplaced lazy imports
- **Over-engineering**: abstractions or helpers not required by the spec
- **Display**: terminal output via the project's shared `console` — not `print()` or hardcoded color names
- **Security**: command injection, path traversal, SQL injection, missing input validation at system boundaries
- **Naming**: class naming suffixes, variable naming, shared display primitives — per `.agent_docs/code-conventions.md`
- **Visibility**: `_prefix` convention — leading-underscore modules must not be imported outside the package
- **API shape**: parameter order, return types, signature width — consistent with existing callers and peer public APIs
- **Modular structure**: logic placed in the wrong module or layer; cohesion/coupling violations
- **Leaf-boundary judgment** (`review.md:40`): if the diff adds an import edge *from* a leaf package (`context`/`tools`/`memory`/`session`) *into* `tools`/`agent`/`bootstrap`, or places loop/prompt-assembly logic inside a leaf package, flag it and ask: is this module correctly homed, or does it belong at the `agent` layer? Frame as judgment, not a structural test — the goal is a conscious siting decision, not an allowlist gate.
- **Anti-patterns**: module-level mutable state (global state); speculative abstractions (helpers or wrappers with a single caller)

- **Clarity by subtraction**: apply the full ruleset in `.agent_docs/review.md` (delete one-sided members; collapse redundant same-lifecycle state; flatten wrapper bags; module home = owning domain; underscore visibility both directions; no import-time side effects; flags only after success; renames are hard and total). Flag any violation introduced or left by the change as blocking.

Each subagent returns a structured findings list: task ID, requirement or rule, verdict (pass/fail), file:line evidence for every entry.

### Aggregation

After all subagents return:
1. Merge all findings lists into a single table keyed by task.
2. Run cross-task integration check: stale imports or API inconsistencies visible only when task boundaries are viewed together.
3. Carry forward scope-creep extra files and lint violations from the pre-step.

---

## Phase 3 — Adversarial Review

Spawn one **adversarial subagent** with declared tools `Read, Grep`. Pass it:
- The merged findings table (task, requirement/rule, verdict, file:line citation) — no analysis narrative, just the structured list.
- The Engineering Rules from CLAUDE.md.

The adversarial subagent re-reads every cited file:line cold with no prior context:

**For each PASS:**
- Confirm the cited line actually implements what was claimed. If it does not: downgrade to FAIL.

**For each FAIL:**
- Confirm the issue is real and not working-as-intended.
- Ask: would the fix count as over-engineering? Is it a hard Engineering Rule violation or a style preference?
- If false positive: downgrade to minor or remove. If real: confirm as blocking or minor.

**Classification:**
- **Blocking** = spec requirement missing, `done_when` fails, hard Engineering Rule violated (mock/stub in test, wrong tool pattern, global state), security issue. A structural/weak test that survives the stub-litmus is blocking only when it guards a Critical/Important behavior (the regression would otherwise ship silently); a weak test over Low-tier behavior is minor.
- **Minor** = style, non-required improvement, partial convention drift with no functional impact.

The adversarial subagent returns: the reconciled findings list with each entry marked confirmed-blocking, confirmed-minor, or false-positive (removed). Main agent applies this list. Only confirmed-blocking findings proceed to Phase 4.

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
   mkdir -p .pytest-logs
   uv run pytest <affected_test_file> -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-fix-verify.log
   ```
5. Loop until all blocking findings are resolved or escalated.

---

## Phase 5 — Full Test Suite with Mandatory RCA

Run the full test suite (`pyproject.toml` `addopts` already supplies `-x --durations=0`, so the run halts at the first failure):
```
mkdir -p .pytest-logs
uv run pytest -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-review-impl.log
```

**Tail the log live** — do not block silently on the suite. The harness emits a per-test `outcome=… | duration=…s` line; watch `duration` to catch a stalled or slow real-LLM call early and fail fast (`testing.md` run-logs policy). A call that balloons only in the full suite but is normal in isolation is cold-load/contention — diagnose, never raise a timeout to absorb it.

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

## Phase 6 — Final Re-scan

After all fixes and tests are green, run the quality gate one final time:
```bash
scripts/quality-gate.sh lint
```
Then re-scan every changed file one more time:

- Dead code introduced during fixes
- Stale imports left by fix edits
- Misplaced lazy imports
- Any test using mocks or fakes introduced during fix (blocking — remove it)
- Doc-code mismatches in changed file docstrings or inline comments
- Naming violations from `.agent_docs/code-conventions.md` introduced during fixes
- `_prefix` visibility leaks — private helpers exposed outside their package by fix edits
- API shape regressions — parameter order or return type inconsistencies introduced by fixes
- Modular structure violations — new logic placed in wrong module during fixes
- Anti-patterns introduced by fixes: new global state or speculative abstraction with a single caller

Fix anything found. This catches what sub-agents and fix loops leave behind.

---

## Phase 7 — Behavioral Verification

**Required for any task that modifies user-facing surface** (CLI commands, tools visible in chat, output formatting, config loading, bootstrap, status).

Run the system and confirm user-visible behavior:

```bash
uv run co --help          # always — boot smoke: confirms the import graph + bootstrap load with no LLM cost
uv run co tail            # if observability or tracing changed (snapshot one trace: uv run co trace <trace_id>)
```

The CLI subcommands are `chat`, `tail`, `trace`, `dream`, `google` — there is no `co status`/`co logs`/`co health` command. Health checks live behind the `/status` slash command **inside** `co chat`, not a non-interactive subcommand.

`co --help` is the always-run boot smoke because it exercises the full import + bootstrap graph at zero LLM cost. `co chat` is an interactive LLM REPL — do not treat a chat turn as a gating check. When behavior under test is LLM-mediated (tool selection, prompt assembly, agent loop), verify it via the task's eval or a direct-call repro and mark the chat interaction non-gating in the verdict.

For each run:
- Confirm it starts without error
- Exercise the specific changed behavior from the task spec via the relevant subcommand or eval/repro
- Confirm output matches the spec — not just "no crash"

**Verify `success_signal`:** For each task with a non-N/A `success_signal`, confirm the stated user-observable outcome during behavioral verification. This is not a second `done_when` — it is a smoke check that the delivered feature produces the effect the user would notice. Record the result in the Behavioral Verification section of the verdict (e.g. "`success_signal` verified: user sees X when Y").

If behavioral verification fails: treat as a blocking finding, go back to Phase 4.

If no user-facing surface was changed: skip and note "no user-facing changes — behavioral verification skipped."

---

## Phase 8 — Verdict

Only append after: all blocking findings resolved, test suite green, final re-scan clean, behavioral verification passed or skipped with justification.

Append to `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`:

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

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- <subcommand or eval/repro>: ✓ <what was verified> (LLM-mediated behavior verified via eval/repro, chat non-gating)
_(or: "No user-facing changes — skipped.")_

### Overall: PASS / ESCALATE
<one sentence. If ESCALATE: list each unresolved finding with recommended next step.>
```

---

## Rules

- **Evidence**: every pass and every fail requires a file:line citation. Pattern-matching on names is not evidence.
- **Auto-fix, don't report**: blocking findings are fixed here — the verdict is clean or escalated, never a to-do list.
- **No mocks or fakes**: if a fix tempts you to mock a dependency, the production API is wrong — fix the API.
