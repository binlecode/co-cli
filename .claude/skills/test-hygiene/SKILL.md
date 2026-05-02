---
name: test-hygiene
description: Enforce test rules, purge structural and redundant tests, verify behavioral depth. Active test quality gate — call it any time, not just pre-ship.
---

# test-hygiene

**Invocation:** `/test-hygiene [path]`

Scans `[path]` (default: `tests/`) for violations of the test policy in `agent_docs/testing.md`. Fixes all violations it can auto-correct, escalates any that require architectural decisions, then runs the full suite to confirm green. Produces a summary report.

**Default stance: violations exist. CLEAN is earned, not assumed.**

**Consumes:** every `test_*.py` / `*_test.py` under `[path]`, `agent_docs/testing.md`, `tests/conftest.py`, `tests/_settings.py`.  
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

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| tests/context/test_context_compaction.py | test_full_chain_p1_to_p5_llm | Unsanctioned marker | Removed @pytest.mark.timeout(180) | DONE |
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
- After each Phase 3 finding: append a row to the Audit Findings table immediately.
- After each Phase 5 fix: mark the row DONE immediately. Do not batch updates.
- After Phase 6: record the result.

This log is permanent (`REPORT-*.md` files are never deleted). It doubles as the final report in Phase 7.

---

## Phase 1 — Load

1. Read `agent_docs/testing.md` in full. Keep the complete rule set in context for all phases — do not re-read per file.
2. Determine scan path: use the argument if given, otherwise `tests/`.
3. Enumerate every `test_*.py` and `*_test.py` file under the path via `find`. Collect the full list.
4. **Read the foundational test support files** before opening any test file:
   - `tests/conftest.py` — understand what pytest plumbing is in place
   - `tests/_settings.py` — establish the ground truth for `SETTINGS`, `SETTINGS_NO_MCP`, and `make_settings()` semantics
   - `tests/_timeouts.py` — establish the sanctioned timeout constants; needed to evaluate every IO-bound timeout rule
   - `tests/_ollama.py` — understand the `ensure_ollama_warm` contract: it must be called **before** any `asyncio.timeout` block, never inside one
5. **Create the tracking log** at `docs/REPORT-test-hygiene-<YYYYMMDD-HHMMSS>.md` with the Phase 1 section and the full file list pre-populated as `[ ]` pending entries.
6. Announce scope:
   ```
   Scanning: tests/  (N files)
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

## Phase 3 — Rule Audit

Apply every rule from `agent_docs/testing.md` to every test file and function. Assign each finding a severity:

**Blocking** (must fix before proceeding):
- **Mock/fake use**: `monkeypatch`, `unittest.mock`, `MagicMock`, `pytest-mock`, hand-assembled domain objects that bypass production code paths — including in conftest.py
- **Structural test**: would still pass after gutting the function body to `pass`; asserts on import layout, module importability, class/attribute presence, type annotations, docstrings, or registration tables
- **Fixture not wired**: `tmp_path` (or other injected fixture) in signature but never passed to a production function — assertion trivially passes regardless
- **Truthy-only assertion**: `assert result.x` where `x` could be any non-falsy value and the test still "passes" with a wrong value (e.g. `assert result.version` instead of `assert re.fullmatch(...)`)
- **Duplicate with trivial delta**: two tests invoke the same function with the same observable effect; the second adds only a trivially-true extra assertion
- **Subsumed file**: an entire test file whose every test is a strict subset of tests in another file covering the same module
- **IO-bound timeout violation**: any `await` to LLM, network, or subprocess not individually wrapped with `asyncio.timeout(N)`; multiple sequential `await`s wrapped in a single `asyncio.timeout` block; timeout constants hardcoded inline instead of imported from `tests._timeouts`; `ensure_ollama_warm` called inside an `asyncio.timeout` block (it is infrastructure prep — must be called before the timeout, never inside it)
- **Production config override**: `model=`, `model_settings=`, or `temperature=` passed to `agent.run()` or equivalent; personality stripped in test setup; module-level model (`build_model(...)`) or agent rebuilt inside a test function instead of cached at module scope
- **Local settings construction**: `_CONFIG = make_settings()` or `_CONFIG_NO_MCP = make_settings(mcp_servers={})` defined at module scope instead of importing from `tests._settings`
- **Inline logic copy**: display formatting or string construction replicated in assertions instead of asserting on the output directly
- **Hardcoded `~/.co-cli` path**: use `USER_DIR` and derived constants from `co_cli/config/core.py`
- **Stale parameter call**: test passes a positional arg or kwarg to a production function that the function no longer accepts — manifests as `TypeError` at runtime rather than assertion failure, so Phase 2 reading alone may miss it. When a test calls a production function with any non-trivial kwargs, grep the function definition to confirm those parameters still exist.
- **conftest.py scope violation**: conftest does anything beyond neutral pytest plumbing — shadows config, injects substitutes, builds domain objects

**Minor** (fix if straightforward, note if not):
- **Unsanctioned categorization marker**: `@pytest.mark.<category>` (e.g. `integration`, `slow`) not explicitly requested. `@pytest.mark.timeout(N)` is **sanctioned** when the test's total LLM call budget legitimately exceeds the 120s pytest ceiling — do not flag it as a violation
- **Unjustified skip**: `pytest.mark.skip` or `pytest.mark.skipif` not gated on credential-based external integration
- **Test name describes structure not behavior**: test name says "test_class_exists" or "test_module_imports" — rename to describe what the code *does*
- **Non-test script in `tests/`**: a `.py` file that is not a pytest file (no `test_` prefix, or is a helper without a corresponding `conftest.py` registration) — move to `scripts/` or `evals/`
- **File naming convention**: new test files not following `test_flow_<area>.py` prefix — flag as a minor violation and suggest a rename

For each finding, append a row to the Phase 3 table in the tracking log immediately: file, line range, rule violated, severity, status=OPEN. Do not accumulate findings in context — write them to the log as you go.

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

## Phase 5 — Auto-Fix Loop

For each blocking finding, in order:

1. **Apply the minimal correct fix:**
   - Structural test → rewrite to drive a real code path and assert on observable outcome, OR delete if no real failure mode exists
   - Mock/fake → replace with real dependency (real SQLite, real filesystem, real service); if the production API makes this impossible, escalate — do not add a mock as a workaround
   - Fixture not wired → wire it to the production function call, or remove the parameter if it serves no purpose
   - Truthy-only assertion → replace with a precise assertion (regex, equality, type + value)
   - Duplicate → delete the weaker test (the one with fewer assertions or less specificity)
   - Subsumed file → delete the subsumed file entirely, confirm the covering file tests the same module
   - IO timeout violation → wrap each `await` individually; import the constant from the timeout module
   - Config override → remove the override; use the production orchestration path
   - Local settings → replace with `from tests._settings import SETTINGS` or `SETTINGS_NO_MCP`
   - conftest violation → strip the offending logic; if a real service setup is needed, move it to a production fixture

2. **Architectural decision required?** If fixing requires changing a public API, restructuring a module, or adding a new production dependency — escalate:
   ```
   ✗ ESCALATE: <finding> requires architectural decision.
   File: <file>:<line>
   What needs to change: <description>
   Recommended next step: <fix the API / open a follow-up>
   ```
   Do not work around it with a fake or a skip.

3. After each fix, re-read the affected test to confirm the fix is correct and complete. Then immediately mark the Phase 5 row DONE in the tracking log and update the corresponding Phase 3 Audit Findings row Status from OPEN to FIXED (or ESCALATED if escalated).

4. Loop until all blocking findings are resolved or escalated.

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
Escalations: N
Tests: N passed, 0 failed
Log: .pytest-logs/<timestamp>-test-hygiene.log
Report: docs/REPORT-test-hygiene-<timestamp>.md

Verdict: CLEAN / ESCALATIONS PENDING — <one sentence>
```

Full detail (per-file results, findings table, fix table) lives in the report file — do not repeat it in the terminal summary.

---

## Rules

- **Read every file in full**: no Explore, no excerpts. A partial read produces false-negative "all clear" reports.
- **Critical functional validation test**: for every test that survives, ask two questions: (1) "if deleted, would a real regression in a production flow go undetected?" and (2) "can I name the specific user-visible failure mode this test catches?" If either answer is no — the test must be rewritten to target a real failure mode, or removed. A passing suite means every test defends a critical regression, not that code achieves high coverage.
- **No mocks under any circumstances**: if a fix tempts you to mock a dependency, the production API is wrong — escalate.
- **Deletion over disable**: never `@pytest.mark.skip` to quiet a test. Either fix it or delete it.
- **CLEAN means the suite is genuinely effective**: every surviving test must defend a real failure mode.
