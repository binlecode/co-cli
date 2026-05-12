---
name: test-hygiene
description: Keep the suite focused on functional logic verification and aggressive issue finding. Purge dead/structural tests, dedup overlaps, trim subsumed units, audit workflow coverage, consolidate by workflow. Active test quality gate.
---

# test-hygiene

**Invocation:** `/test-hygiene [path]`

**Mission:** keep `[path]` focused on **functional logic verification and aggressive issue finding**. The skill does five things, in order:

1. Purge dead, structural, and rule-violating tests
2. Dedup cross-file overlaps and trim pure-unit tests subsumed by workflow tests
3. Audit coverage against `agent_docs/system-workflows-to-test.md` — every registered workflow must have ≥1 test asserting on at least one of its Primary failure modes
4. Consolidate tests under their owning workflow so each workflow has a single canonical file home
5. Run the full suite green

**Default stance: violations exist. CLEAN is earned, not assumed.**

**Consumes:** every `test_*.py` / `*_test.py` under `[path]`, `agent_docs/testing.md`, `agent_docs/system-workflows-to-test.md`, and `tests/_*.py` foundational support files (conftest, settings, timeouts, ollama, etc.) — enumerated in Phase 1.  
**Produces:** `docs/REPORT-test-hygiene-<YYYYMMDD-HHMMSS>.md` (persistent tracking log + final report) + `.pytest-logs/<timestamp>-test-hygiene.log`.

---

## Tracking Log

**Create the tracking log at the very start of Phase 1** and update it continuously throughout the run. This is the authoritative record of progress — it survives context compaction and lets you resume exactly where you left off.

Path: `docs/REPORT-test-hygiene-<YYYYMMDD-HHMMSS>.md` (timestamp fixed at invocation).

Structure:

```markdown
# REPORT-test-hygiene-<timestamp>

## Meta
- Scan path: tests/
- Files: N
- Started: <ISO datetime>
- Status: IN PROGRESS | DONE

## Phase 1 — Load
- [ ] rules loaded
- [ ] file list enumerated (N files)

## Phase 2 — File Read Progress
<!-- One line per file: [ ] pending, [x] done, [!] violation found -->
- [x] tests/approvals/test_approvals.py — CLEAN
- [!] tests/context/test_context_compaction.py — unsanctioned markers line 42, 87
- [ ] tests/memory/test_knowledge_tools.py
...

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/context/test_context_compaction.py | 42 | Unsanctioned marker | Minor | OPEN |
...

## Phase 4 — Adversarial Review
<!-- Record each challenged finding and outcome -->
- tests/context/test_context_compaction.py:42 — confirmed violation (not a conftest fixture workaround)

## Phase 4.5 — Workflow Coverage
| Workflow | Tests | Failure modes covered | Status | Severity |
|----------|-------|-----------------------|--------|----------|
| 3.1 run_turn | tests/test_flow_orchestrate_*.py | per-turn reset, error path | Covered | — |
| 3.7 HTTP 400 reformulation | — | — | Uncovered | Blocking |
| 6.4 Marker assembly | tests/test_flow_compaction_summarization.py | shape only | Stub-covered | Minor |

Scope-drift tests:
- tests/test_flow_X.py::test_foo — no matching workflow

Trim candidates:
- tests/test_flow_Y.py::test_helper — subsumed by workflow N.M

## Phase 4.6 — Consolidation Plan
| Workflow group | Current files | Target file | Test moves |
|----------------|---------------|-------------|------------|
| Compaction L3 proactive | test_flow_compaction_proactive.py, test_flow_compaction_processor_chain.py | test_flow_compaction_l3_proactive.py | test_thrash_window from chain → proactive |

## Phase 5 — Fixes Applied
| File | Test | Rule / Action | Status |
|------|------|---------------|--------|
| tests/context/test_context_compaction.py | test_full_chain_p1_to_p5_llm | Unsanctioned marker → removed @pytest.mark.timeout(180) | DONE |
| tests/test_flow_Y.py | test_helper | Trim — subsumed by workflow N.M | DONE |
| tests/test_flow_compaction_processor_chain.py | test_thrash_window | Consolidate → moved to test_flow_compaction_l3_proactive.py | DONE |
...

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Log: .pytest-logs/<timestamp>-test-hygiene.log
- Result: PENDING | N passed, 0 failed | FAILED: <test>

## Phase 7 — Final Verdict
CLEAN / ESCALATIONS PENDING
```

**Update discipline:**
- After completing each file in Phase 2: mark it `[x]` or `[!]` in the log immediately, before reading the next file. Drop per-file detail from active context — the log holds it.
- After Phase 2.5: record the count of call sites checked and any findings.
- After each Phase 3 finding: append a row to the Audit Findings table immediately.
- After each Phase 4.5 workflow row classified: append immediately.
- After Phase 4.6 plan complete: write the full Consolidation Plan table before entering Phase 5.
- After each Phase 5 fix / trim / move: mark the row DONE immediately. Do not batch updates.
- After Phase 6: record the result.

This log is permanent (`REPORT-*.md` files are never deleted). It doubles as the final report in Phase 7.

---

## Phase 1 — Load

1. Read `agent_docs/testing.md` in full. Keep the complete rule set in context for all phases — do not re-read per file.
2. Read `agent_docs/system-workflows-to-test.md` in full. This is the canonical workflow registry: every workflow has an Entry, Behavior, Primary failure modes, and Required test depth. Phase 4.5 (coverage audit) and Phase 4.6 (consolidation) read against it. Keep it loaded for the rest of the run.
3. Determine scan path: use the argument if given, otherwise `tests/`.
4. Enumerate every `test_*.py` and `*_test.py` file under the path via `find`. Collect the full list.
5. **Read the foundational test support files** before opening any test file:
   - `tests/conftest.py` — understand what pytest plumbing is in place
   - `tests/_settings.py` — establish the ground truth for `SETTINGS`, `SETTINGS_NO_MCP`, and `make_settings()` semantics
   - `tests/_timeouts.py` — establish the sanctioned timeout constants; needed to evaluate every IO-bound timeout rule
   - `tests/_ollama.py` — understand the `ensure_ollama_warm` contract: it must be called **before** any `asyncio.timeout` block, never inside one
6. **Create the tracking log** at `docs/REPORT-test-hygiene-<YYYYMMDD-HHMMSS>.md` with the Phase 1 section and the full file list pre-populated as `[ ]` pending entries.
7. Announce scope:
   ```
   Scanning: tests/  (N files)
   Workflows in registry: M
   Tracking log: docs/REPORT-test-hygiene-<timestamp>.md
   Stance: violations exist — CLEAN is earned
   ```

---

## Phase 2 — Full Read

**Read every test file in full** — not via Explore, not via excerpts. Explore reads partial windows and produces false-negative "all clear" reports. Use the Read tool on each file, starting at line 1, reading to EOF.

Process files in directory batches (e.g., all of `tests/approvals/`, then `tests/bootstrap/`, etc.). After finishing each file:
1. Mark it `[x] CLEAN` or `[!] <brief violation note>` in the tracking log.
2. Release per-file detail from active context — the log holds it.

For each file, catalog: test function name, production code path invoked, what it asserts, any rule violations spotted.

---

## Phase 2.5 — Stale Call Grep

Phase 2's full-read catches semantic violations; runtime API drift slips through. Tests that pass a kwarg or positional arg the production function no longer accepts manifest as `TypeError` at run time, not as an assertion failure during reading.

For every test that calls a production function with anything beyond a single positional argument:

1. From the Phase 2 catalog (test → production code path), collect each `(call_site, target_function, args)` tuple where `args` is non-trivial.
2. For each target, run `grep -n "def <function>" <module_path>` (or follow imports if needed) and compare the live signature against the call site.
3. If a parameter no longer exists, append a `Stale parameter call` row to the Phase 3 Audit Findings table with severity Blocking — even though the audit table is technically a Phase 3 artifact, record it now so it lands in the same workflow.
4. Mark Phase 2.5 done in the tracking log with the count of call sites checked and any findings.

Add a `## Phase 2.5 — Stale Call Grep` section to the tracking log structure with one line: `- [ ] N call sites with non-trivial args checked, M stale parameter calls found`.

---

## Phase 3 — Rule Audit

`agent_docs/testing.md` is the source of truth for *what* the rules are. This phase classifies *severity* and routes findings — do not restate the rules. Apply every bullet under `## Tests (tests/)` and `## Evals (evals/)` to every file in scope, citing the rule by its bullet keyword (e.g. `Real dependencies only`, `Behavior over structure`).

**Blocking** (must fix before proceeding) — citing testing.md bullet keyword:
- `Real dependencies only — no fakes` (mocks, `monkeypatch`, `pytest-mock`, hand-assembled domain objects, conftest substitution)
- `Behavior over structure` (test would still pass after gutting body to `pass`)
- `Suite hygiene` anti-patterns (fixture-not-wired, duplicate-with-trivial-delta, truthy-only assertion, subsumed file)
- `IO-bound timeouts` (per-await wrapping, sanctioned constants from `tests._timeouts`, `ensure_ollama_warm` placement)
- `Production config only — no overrides` (`model=`/`model_settings=`/`temperature=` overrides, personality stripped, non-module-cached agents)
- `Centralized test config` (local `_CONFIG = make_settings(...)` instead of importing `SETTINGS`/`SETTINGS_NO_MCP`)
- `Never copy inline logic into tests` (display/string construction replicated in assertions)
- `Stale parameter call` — test passes a kwarg/positional the production function no longer accepts. Surfaces at runtime as `TypeError`, not at static read — caught in **Phase 2.5**, recorded here.
- **Hardcoded `~/.co-cli` path** — CLAUDE.md rule; use `USER_DIR` and derived constants from `co_cli/config/core.py`.
- **Subsumed unit test** — exercises a helper / pure function in isolation when a registered workflow test (per `system-workflows-to-test.md`) already drives that helper end-to-end, with no extra failure mode covered. Trim → delete (or fold any unique assertion into the workflow test).
- **Cross-file overlap** — same workflow + same observable outcome in two different files. Keep the most direct one (the file whose name matches the owning workflow group); delete the other.
- **Happy-path-only** — drives a registered workflow but only the success path; ignores every Primary failure mode listed for that workflow in the registry. The fix is to add failure-mode assertions, not to preserve the test as-is.
- **Misplaced test** — a test in `test_flow_X.py` that actually exercises workflow Y owned by `test_flow_Y.py`. Recorded for Phase 4.6 consolidation.

**Minor** (fix if straightforward, note if not):
- `No categorization markers` — `@pytest.mark.<category>` other than sanctioned `@pytest.mark.timeout(N)` (allowed when total LLM budget exceeds the 120s pytest ceiling)
- `Suite hygiene` — unjustified `pytest.mark.skip` / `skipif` not gated on credential-based external integration
- `Behavior over structure` — test name describes structure (`test_class_exists`, `test_module_imports`) rather than behavior
- `Only pytest files in tests/` — non-test script in `tests/` (no `test_` prefix, not a registered helper)
- `File naming` — new file not following `test_flow_<area>.py` prefix

If the bullet keyword above doesn't appear in the current `testing.md`, the rule has been retired — drop it. Do not enforce policy not in the source.

For each finding, append a row to the Phase 3 table in the tracking log immediately: file, line range, rule (bullet keyword), severity, status=OPEN. Do not accumulate findings in context — write them to the log as you go.

---

## Phase 4 — Adversarial Self-Review

Before fixing, challenge every finding:

**For each PASS:**
- Did I actually read the assertion logic, or did I assume it was behavioral because it called a real function?
- If I cannot describe exactly what observable outcome the test verifies, go back and read.
- Can I name the specific user-visible failure mode this test catches? If not, it is not critical functional validation — flag it.

**For each FAIL:**
- Is this a genuine rule violation, or is there context I missed (e.g., a conftest fixture that wires the real production path)?
- Is the "duplicate" actually testing a meaningfully different failure mode?
- Would deleting this test let a real regression go undetected in a production flow that matters to users?

Downgrade false positives. Upgrade missed violations. Only findings that survive this challenge proceed to Phase 5.

---

## Phase 4.5 — Workflow Coverage Audit

The Phase 3 audit asks "is this test legitimate?" — per-test. This phase asks "is each registered workflow defended against its enumerated failure modes?" — per-workflow. The two together set the bar for CLEAN.

### Procedure

For every workflow in `agent_docs/system-workflows-to-test.md`:

1. From the Phase 2 catalog (`test → production code path`), find tests whose call sites include the workflow's **Entry**.
2. Read the assertions of each candidate test against the workflow's listed **Primary failure modes**.
3. Classify the workflow:
   - **Covered** — ≥1 test asserts on at least one Primary failure mode (not just happy path)
   - **Stub-covered** — test reaches the entry point but assertion is structural, truthy-only, or covers only happy path
   - **Uncovered** — no test reaches the entry point
4. Record the row in the Phase 4.5 Workflow Coverage table immediately.

After the workflow walk, identify two cross-cutting findings:

- **Scope drift** — every test from the Phase 2 catalog that does NOT map to any registered workflow. Either the workflow is missing from the registry (gap to flag) or the test is misnamed/orphaned (trim candidate).
- **Trim candidates** — tests flagged with `Subsumed unit test`, `Cross-file overlap`, or `Happy-path-only` in Phase 3, recorded as the trim list for Phase 5.

### Severity

- **User-facing workflow uncovered** (chat loop, slash commands, tool execution, memory recall, approval prompts, REPL, persistence) → **Blocking**
- **Internal mechanism uncovered** (planners, history processors, span emitters, helpers) → **Minor** unless they gate a user-facing workflow
- **Stub-covered** (any) → **Minor** with an `ESCALATE` tag for failure-mode probing

The registry's `## Coverage Audit Procedure` section codifies the same calibration — defer to it on edge cases.

### Output

Coverage gaps **escalate, don't auto-fix**. Writing a new test for an uncovered workflow is creative work — the skill's auto-fix loop only handles mechanical fixes. Emit per gap:

```
✗ ESCALATE: workflow N.M (<name>) — Uncovered / Stub-covered (<reason>)
Entry: <file:function>
Primary failure modes to probe: <bullet list from registry>
Recommended next step: open follow-up exec plan for new behavioral test
```

---

## Phase 4.6 — File Consolidation by Workflow

Tests should sit in the canonical file for their owning workflow. Fragmentation across files (multiple files testing the same workflow group) and misplacement (a test in the wrong file) both make coverage harder to read and easier to drift. This phase produces the move plan; Phase 5 executes it.

### File naming convention

- One file per tightly-coupled workflow group, typically a single registry section (`## N. <area>`), capped at ~5 workflows
- Naming: `test_flow_<section_slug>.py` aligned to the registry section name (e.g., section 5.4 `proactive_window_processor` → `test_flow_compaction_l3_proactive.py`; section 12 `Memory — Knowledge Channel` may split into `test_flow_memory_recall.py`, `test_flow_memory_write.py`, `test_flow_memory_canon.py`)
- Tests within a file ordered by workflow ID
- Multiple workflows per file when they share entry/setup (e.g., all bootstrap lifecycle workflows can share `test_flow_bootstrap_lifecycle.py`)
- Foundational support files keep the `tests/_*.py` private prefix

### Procedure

1. From the Phase 4.5 coverage table, group covering tests by owning workflow.
2. For each workflow group, identify the **canonical target file** by the convention above.
3. List moves: `(source_file, test_function) → target_file`. Cluster moves by source so a single edit pass per source file suffices.
4. Identify files that become empty after moves — schedule for deletion (with the post-removal sweep from Phase 5 step 2).
5. Identify files whose name no longer matches its remaining content — schedule for rename via `git mv`.
6. Record the full plan in the Phase 4.6 Consolidation Plan table.

### Limits

- **Do not consolidate tests for different workflows into one file** just because they touch the same module — the registry's workflow grouping is the source of truth, not Python module structure.
- **Do not split a file when ≤5 cohesive workflows share it** — small consolidations create churn without clarity.
- **Do not rename a file that already matches the convention** — Phase 4.6 is corrective, not stylistic.

### Output

The plan feeds Phase 5 as a structured fix list. No moves happen in this phase — only planning.

---

## Phase 5 — Auto-Fix Loop

Order of operations within this phase:

**A. Apply minimal correct fixes** to all Blocking findings from Phase 3.
**B. Apply trims** from the Phase 4.5 trim-candidates list.
**C. Apply consolidations** from the Phase 4.6 plan.
**D. Post-removal sweep** runs after every deletion or move — see step 2.

For each finding/trim/move, in order:

1. **Apply the minimal correct fix:**
   - Structural test → rewrite to drive a real code path and assert on observable outcome, OR delete if no real failure mode exists
   - Mock/fake → replace with real dependency (real SQLite, real filesystem, real service); if the production API makes this impossible, escalate — do not add a mock as a workaround
   - Fixture not wired → wire it to the production function call, or remove the parameter if it serves no purpose
   - Truthy-only assertion → replace with a precise assertion (regex, equality, type + value)
   - Duplicate → delete the weaker test (the one with fewer assertions or less specificity)
   - Subsumed file → delete the subsumed file entirely, confirm the covering file tests the same module
   - **Subsumed unit test** → delete the unit test; if it asserted on a failure mode the workflow test misses, fold that assertion into the workflow test before deleting
   - **Cross-file overlap** → keep the test in the file matching the owning workflow; delete from the other file
   - **Happy-path-only** → add assertions covering at least one of the workflow's Primary failure modes; if the failure mode requires a different setup the existing test can't accommodate, escalate as `Uncovered failure mode` and leave the happy-path test in place
   - **Misplaced test** → defer to step C (consolidation)
   - IO timeout violation → wrap each `await` individually; import the constant from the timeout module
   - Config override → remove the override; use the production orchestration path
   - Local settings → replace with `from tests._settings import SETTINGS` or `SETTINGS_NO_MCP`
   - conftest violation → strip the offending logic; if a real service setup is needed, move it to a production fixture

2. **Post-removal sweep — mandatory after any deletion.** Whenever a fix deletes a test file, test function, fixture, helper, or import, immediately grep the entire repo for the removed symbol:
   ```bash
   grep -rn "<symbol>" . --include="*.py" --include="*.md"
   ```
   Clean up every orphan: dead imports, broken `from tests.X import Y`, stale references in docs/specs, registration-table entries pointing to nothing. Do not mark the Phase 5 row DONE until the sweep returns no hits beyond the deletion site itself. This catches the dead-code-after-removal class of bug that Phase 6 may not surface (e.g. an unused private helper the deleted test was the sole caller of).

3. **Apply consolidations from the Phase 4.6 plan.** Process moves clustered by source file:
   - Move each test function to its target file using `Edit` (cut from source, paste into target at the position dictated by workflow ID order)
   - Carry over imports the moved test depends on; do not duplicate imports already present in the target file
   - When a source file becomes empty, delete it via `git mv` to nothing — actually `git rm` — and run the post-removal sweep (step 2) on the file path
   - When a source file requires a rename to match the convention, use `git mv` (not `mv`) so history is preserved
   - After each move/rename, mark the Phase 4.6 row DONE in the tracking log

4. **Architectural decision required?** If fixing requires changing a public API, restructuring a module, or adding a new production dependency — escalate:
   ```
   ✗ ESCALATE: <finding> requires architectural decision.
   File: <file>:<line>
   What needs to change: <description>
   Recommended next step: <fix the API / open a follow-up>
   ```
   Do not work around it with a fake or a skip.

5. After each fix, re-read the affected test to confirm the fix is correct and complete. Then immediately mark the Phase 5 row DONE in the tracking log and update the corresponding Phase 3 Audit Findings row Status from OPEN to FIXED (or ESCALATED if escalated).

6. Loop until all blocking findings, trims, and consolidations are resolved or escalated.

---

## Phase 6 — Full Test Suite

```bash
mkdir -p .pytest-logs
uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-test-hygiene.log
```

**Any failure = stop immediately. Do RCA:**

1. Read the failing test.
2. Read the full error and traceback.
3. Trace to root cause in source — not in the test.
4. Fix the root cause. Never modify a test to make it pass unless the test itself is stale (API removed or renamed).
5. Re-run the suite.
6. Repeat until green.

Never dismiss a failure as flaky without running it 3× to confirm non-determinism and identifying the specific cause.

Only proceed to Phase 7 with a fully green suite.

---

## Phase 7 — Report

Update the tracking log: set `Status: DONE`, fill in Phase 6 result, write the final verdict line.

Then print a terminal summary pointing to the log:

```
## test-hygiene — <date>

Files scanned: N
Violations fixed: N
Tests trimmed: N (subsumed / overlapping / happy-path-only)
Files consolidated: M (from K → J)
Workflows: N covered, M stub, K uncovered (J blocking)
Scope drift: P tests with no workflow mapping
Escalations: N
Tests: N passed, 0 failed
Log: .pytest-logs/<timestamp>-test-hygiene.log
Report: docs/REPORT-test-hygiene-<timestamp>.md

Verdict: CLEAN / ESCALATIONS PENDING — <one sentence>
```

Full detail (per-file results, findings table, coverage table, consolidation plan, fix table) lives in the report file — do not repeat it in the terminal summary.

---

## Rules

- **Read every file in full**: no Explore, no excerpts. A partial read produces false-negative "all clear" reports.
- **Functional validation only**: for every test that survives, ask two questions: (1) "if deleted, would a real regression in a production flow go undetected?" and (2) "can I name the specific user-visible failure mode this test catches?" If either answer is no — the test must be rewritten to target a real failure mode, or removed. A passing suite means every test defends a critical regression, not that code achieves high coverage.
- **Workflow-anchored coverage**: every surviving test must drive a workflow registered in `agent_docs/system-workflows-to-test.md`, and at least one test per workflow must probe one of the workflow's listed Primary failure modes. Happy-path-only coverage is not enough.
- **One workflow, one canonical file**: each workflow has a single canonical home. Tests in the wrong file (per Phase 4.6's naming convention) get moved, not duplicated.
- **No mocks under any circumstances**: if a fix tempts you to mock a dependency, the production API is wrong — escalate.
- **Deletion over disable**: never `@pytest.mark.skip` to quiet a test. Either fix it or delete it.
- **Aggressive over comprehensive**: prefer fewer, sharper tests that probe failure modes over many tests that confirm happy paths. The suite's job is to find bugs, not to inflate coverage numbers.
- **CLEAN means**:
  1. Every surviving test drives a workflow and probes ≥1 of its Primary failure modes
  2. Each workflow has a single canonical test file home
  3. No two tests probe the same failure mode of the same workflow with no meaningful delta
  4. Pure-unit tests survive only when they cover a failure mode no workflow test reaches
  5. The full suite is green
