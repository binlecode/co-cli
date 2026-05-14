---
name: clean-tests
description: Keep the suite focused on functional logic verification and aggressive issue finding. Purge dead/structural tests, dedup overlaps, trim subsumed units, audit workflow coverage, consolidate by workflow. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** keep `[path]` focused on **functional logic verification and aggressive issue finding**. Eight phases, in order:

1. Load rules and enumerate files
2. Read every test file; check for stale call signatures
3. Audit against testing rules; challenge each finding
4. Audit workflow coverage and registry completeness
5. Plan file consolidation
6. Auto-fix all surviving findings
7. Run the full suite green
8. Report

**Default stance: violations exist. CLEAN is earned, not assumed.**

**Consumes:** every `test_*.py` / `*_test.py` under `[path]`, `agent_docs/testing.md`, `agent_docs/system-workflows-to-test.md`, and `tests/_*.py` foundational support files.  
**Produces:** `docs/REPORT-clean-tests-<YYYYMMDD-HHMMSS>.md` (persistent tracking log + final report) + `.pytest-logs/<timestamp>-clean-tests.log`.

---

## Tracking Log

Create `docs/REPORT-clean-tests-<YYYYMMDD-HHMMSS>.md` at the start of Phase 1 (timestamp fixed at invocation). Track: current phase, files processed (`[x]` clean / `[!]` violation with brief note), findings (file, line, rule, severity, status), and fixes applied. Update after each file read and after each fix — do not batch.

This log is permanent (`REPORT-*.md` files are never deleted). It doubles as the final report.

---

## Phase 1 — Load

1. Read `agent_docs/testing.md` in full. Keep the complete rule set in context for all phases — do not re-read per file.
2. Read `agent_docs/system-workflows-to-test.md` in full. Keep it loaded for the rest of the run.
3. Determine scan path: use the argument if given, otherwise `tests/`.
4. Enumerate every `test_*.py` and `*_test.py` file under the path via `find`.
5. Read the foundational test support files before opening any test file:
   - `tests/conftest.py` — pytest plumbing
   - `tests/_settings.py` — ground truth for `SETTINGS`, `SETTINGS_NO_MCP`, `make_settings()`
   - `tests/_timeouts.py` — sanctioned timeout constants
   - `tests/_ollama.py` — `ensure_ollama_warm` contract: must be called **before** any `asyncio.timeout` block, never inside one
6. Create the tracking log with the full file list pre-populated as `[ ]` pending entries.
7. Announce scope: scan path, file count, workflow count, tracking log path.

---

## Phase 2 — Full Read

**Read every test file in full** — not via Explore, not via excerpts. Use the Read tool on each file, starting at line 1, reading to EOF. Process in directory batches.

For each file, catalog: test function name, production code path invoked, what it asserts, any rule violations spotted. After finishing each file, mark it `[x] CLEAN` or `[!] <brief violation note>` in the tracking log; release per-file detail from active context — the log holds it.

For each call site with non-trivial args (beyond a single positional), verify the live signature hasn't drifted — stale parameter mismatches (kwarg the production function no longer accepts) are Blocking findings.

---

## Phase 3 — Rule Audit + Adversarial

`agent_docs/testing.md` is the source of truth for the rules. Classify each violation by severity:

**Blocking** (must fix before proceeding) — cite the rule by its `testing.md` bullet keyword:
- `Real dependencies only — no fakes` (mocks, `monkeypatch`, `pytest-mock`, hand-assembled domain objects, conftest substitution)
- `Behavior over structure` (test passes after gutting body to `pass`)
- `Suite hygiene` anti-patterns (fixture-not-wired, duplicate-with-trivial-delta, truthy-only assertion, subsumed file)
- `IO-bound timeouts` (per-await wrapping, sanctioned constants from `tests._timeouts`, `ensure_ollama_warm` placement)
- `Production config only — no overrides` (`model=`/`model_settings=`/`temperature=` overrides, personality stripped, non-module-cached agents)
- `Centralized test config` (local `_CONFIG = make_settings(...)` instead of importing `SETTINGS`/`SETTINGS_NO_MCP`)
- `Never copy inline logic into tests` (display/string construction replicated in assertions)
- `Stale parameter call` — test passes a kwarg/positional the production function no longer accepts
- **Hardcoded `~/.co-cli` path** — use `USER_DIR` and derived constants from `co_cli/config/core.py`
- **Subsumed unit test** — exercises a helper in isolation when a workflow test already drives it end-to-end with no extra failure mode covered
- **Cross-file overlap** — same workflow + same observable outcome in two different files
- **Happy-path-only** — drives a registered workflow but probes none of its Primary failure modes
- **Misplaced test** — a test whose owning workflow belongs in a different canonical file

**Minor** (fix if straightforward):
- `No categorization markers` — unsanctioned `@pytest.mark.<category>` other than allowed `@pytest.mark.timeout(N)`
- `Suite hygiene` — unjustified `pytest.mark.skip` not gated on credential-based external integration
- `Behavior over structure` — test name describes structure, not behavior
- `Only pytest files in tests/` — non-test script in `tests/`
- `File naming` — new file not following `test_flow_<area>.py` prefix

If a bullet keyword above doesn't appear in the current `testing.md`, the rule has been retired — drop it. Do not enforce policy not in the source.

**Challenge every test's classification — clean or suspicious.** For each test in the Phase 2 catalog:

- *Apparent PASS:* Is the pass actually behavioral, or did you assume it because it calls a real function? Can you name the specific user-visible failure mode this test catches? Would deleting it let a real regression go undetected? If not — upgrade to a violation.
- *Apparent FAIL:* Is the violation real, or is there a conftest fixture wiring the real production path? Is the "duplicate" testing a meaningfully different failure mode? If the issue is a false positive — downgrade or remove.

Only findings that survive this challenge are recorded and proceed to Phase 6.

---

## Phase 4 — Coverage Audit + Registry Check

### Workflow coverage

For every workflow in `agent_docs/system-workflows-to-test.md`:

1. From the Phase 2 catalog, find tests whose call sites include the workflow's **Entry**.
2. Check assertions against the workflow's **Primary failure modes**.
3. Classify: **Covered** (≥1 test asserts on a failure mode, not just happy path), **Stub-covered** (entry reached but assertion is structural or happy-path only), **Uncovered** (no test reaches the entry point).

After the workflow walk, identify:
- **Scope drift** — tests that don't map to any registered workflow (registry gap or trim candidate)
- **Trim candidates** — tests flagged Subsumed, Cross-file overlap, or Happy-path-only

**Severity:** user-facing workflow uncovered → Blocking; internal mechanism uncovered → Minor unless it gates a user-facing workflow; stub-covered → Minor with ESCALATE tag.

Coverage gaps **escalate, don't auto-fix**:
```
✗ ESCALATE: workflow N.M (<name>) — Uncovered / Stub-covered (<reason>)
Entry: <file:function>
Primary failure modes to probe: <bullet list from registry>
Recommended next step: open follow-up exec plan for new behavioral test
```

### Registry completeness

Using the Entry field patterns from the registry, scan for entry-point-shaped functions in `co_cli/tools/` and `co_cli/commands/`. For each source entry point with no registry match, assess whether it is a distinct user-facing or integration-boundary workflow. If yes, flag as a Registry Gap (escalate, don't auto-fix). If it is a helper or sub-step of a registered workflow, record as Sub-step (no gap).

---

## Phase 5 — File Consolidation Plan

Tests should sit in the canonical file for their owning workflow. Fragmentation and misplacement make coverage harder to read and easier to drift.

**Convention:** one file per tightly-coupled workflow group (typically a single registry section, capped at ~5 workflows), named `test_flow_<section_slug>.py`. Tests within a file ordered by workflow ID. Multiple workflows per file when they share entry/setup.

**Procedure:**
1. From the Phase 4 coverage table, group covering tests by owning workflow.
2. For each workflow group, identify the canonical target file.
3. List moves: `(source_file, test_function) → target_file`. Cluster moves by source.
4. Identify files that become empty after moves — schedule for `git rm`.
5. Identify files whose name no longer matches content — schedule for `git mv`.

**Limits:** do not consolidate tests for different workflows just because they share a module; do not split a file when ≤5 cohesive workflows share it; do not rename a file that already matches the convention.

No moves happen here — planning only. Execution is Phase 6 step C.

---

## Phase 6 — Auto-Fix

Order of operations:

**A. Apply minimal correct fixes** to all Blocking findings from Phase 3.  
**B. Apply trims** from the Phase 4 trim-candidates list.  
**C. Apply consolidations** from the Phase 5 plan.

**Fix principle:** apply the minimal correct fix — rewrite to drive a real code path and assert on observable outcome, delete if no real failure mode exists, add failure-mode assertions, move to the canonical file per the consolidation plan.

**No mocks under any circumstances.** If fixing requires mocking a dependency, the production API is wrong — escalate instead.

**Escalate when** fixing requires a public API change, a new production dependency, or architectural restructuring:
```
✗ ESCALATE: <finding> requires architectural decision.
File: <file>:<line>
What needs to change: <description>
Recommended next step: <fix the API / open a follow-up>
```

**Post-removal sweep:** after any deletion or move, `grep -rn "<symbol>" . --include="*.py" --include="*.md"` and clean up every orphan — dead imports, stale doc references, registration-table entries pointing to nothing. Do not mark the row DONE until the sweep is clean.

After each fix, re-read the affected test to confirm correctness. Mark the tracking log row DONE immediately; update the Phase 3 finding status from OPEN to FIXED or ESCALATED.

---

## Phase 7 — Full Test Suite

```bash
mkdir -p .pytest-logs
uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests.log
```

Any failure: stop immediately. Read the failing test, read the full traceback, trace to root cause in source. Fix the root cause — never modify a test to make it pass unless the test is stale (API removed or renamed). Re-run until green. Never dismiss a failure as flaky without running it 3× and identifying the specific cause.

Only proceed to Phase 8 with a fully green suite.

---

## Phase 8 — Report

Update the tracking log: set Status DONE, record the Phase 7 result, write the final verdict line.

Print a terminal summary:

```
## clean-tests — <date>

Files scanned: N
Violations fixed: N
Tests trimmed: N (subsumed / overlapping / happy-path-only)
Files consolidated: M (from K → J)
Workflows: N covered, M stub, K uncovered (J blocking)
Scope drift: P tests with no workflow mapping
Registry gaps: N (escalated)
Escalations: N
Tests: N passed, 0 failed
Log: .pytest-logs/<timestamp>-clean-tests.log
Report: docs/REPORT-clean-tests-<timestamp>.md

Verdict: CLEAN / ESCALATIONS PENDING — <one sentence>
```

Full detail lives in the report file — do not repeat it in the terminal summary.
