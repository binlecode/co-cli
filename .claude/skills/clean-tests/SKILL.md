---
name: clean-tests
description: Keep the suite focused on functional logic verification and aggressive issue finding. Purge dead/structural tests, dedup overlaps, trim subsumed units, audit workflow coverage, consolidate by workflow. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** keep `[path]` focused on **functional logic verification and aggressive issue finding**. Nine phases, in order:

0. Sync reference docs against live source
1. Load rules and enumerate files
2. Read every test file; check for stale call signatures
3. Audit against testing rules; challenge each finding
4. Audit workflow coverage
5. Plan file consolidation
6. Auto-fix all surviving findings
7. Run the full suite green
8. Report

**Default stance: violations exist. CLEAN is earned, not assumed.**

**Consumes:** every `test_*.py` / `*_test.py` under `[path]`, `agent_docs/testing.md`, `agent_docs/system-workflows-to-test.md`, and `tests/_*.py` foundational support files.  
**Produces:**
- `docs/REPORT-clean-tests-<YYYYMMDD-HHMMSS>.md` — persistent tracking log + final report (Phase 2 catalog lives here under `### Test Catalog`).
- `.pytest-logs/<timestamp>-clean-tests.log` — pytest output.
- `docs/clean-tests-coverage-state.json` — cross-run audit timestamps (Phase 4 sample-mode rotation).

---

## Tracking Log

Create `docs/REPORT-clean-tests-<YYYYMMDD-HHMMSS>.md` at the start of Phase 0 (timestamp fixed at invocation). Sections, appended as phases run:

- **Phase status** — one line per phase: `PENDING` / `IN PROGRESS` / `DONE`.
- **Files processed** — Phase 2 marks each as `[ ]` pending → `[x] READ` after the per-test catalog block is written. (Rule classifications come from Phase 3, recorded under Findings, not here.)
- **Test Catalog** — one per-test record per file, per the Phase 2 schema.
- **Findings** — Phase 3 output: `file:line | rule keyword | severity (Blocking/Minor) | status (OPEN/FIXED/MINOR-DEFERRED/ESCALATED)`.
- **Coverage table** — Phase 4 output: per-workflow classification (Covered / Depth-mismatch / Stub-covered / Uncovered / Unaudited-this-run) + novel failure modes.
- **Fixes** — Phase 6 entries, one line per applied fix.
- **Verdict** — Phase 8 gate result.

Update after each file read and after each fix — do not batch.

`docs/clean-tests-coverage-state.json` is updated by Phase 4 (see Phase 4) and rolls forward across runs; the per-run report file is permanent (`REPORT-*.md` files are never deleted).

---

## Phase 0 — Source Sync

Validate both reference docs against the live codebase before any phase uses them. Stale references in either doc corrupt Phase 3 rule enforcement and Phase 4 coverage analysis.

### What to check

**`agent_docs/system-workflows-to-test.md`** — for every workflow Entry field:
- Extract all `co_cli/…py` file path tokens; verify each exists on disk.
- Extract every `filename.py: function_name` pair; verify the function name appears in that file.
- Parse multi-entry lines (`+`, `→`, `;`) by splitting on those separators before extracting pairs.

**`agent_docs/testing.md`** — for every rule bullet:
- Extract file path references (`tests/_*.py`, `co_cli/…py`); verify each exists on disk.
- Extract our own identifiers (module-qualified names like `tests._timeouts`, named constants like `USER_DIR`, `SETTINGS`, `SETTINGS_NO_MCP`, function names like `ensure_ollama_warm`, `build_model`, `make_settings`); grep for each in source. **Skip** external framework names (`monkeypatch`, `pytest-mock`, `unittest.mock`, `pytest.mark.*`, `tmp_path`, `asyncio`) — these are not our code.

### Action on stale references

Collect all stale references before acting. For each stale reference, report:
```
✗ STALE [doc:line]: <stale_reference> — not found in source
```

If any stale references are found, ask for approval to fix them in-place. On approval, apply the minimal correct fix (update path, function name, or constant to current source). On denial, abort — Phase 1 must not proceed with stale docs.

### Registry completeness (source → registry)

After validating existing entries, scan all of `co_cli/` for entry-point-shaped functions: `@agent_tool`-decorated functions and command dispatch registrations. For each source function, decide whether it is covered by **any** of these registry forms:

1. **Named match** — the function name appears in any Entry field as `path/file.py: function_name`.
2. **File-level match** — the function's containing file appears as a bare `path/file.py` in an Entry field (covers every function defined in that file — common for command dispatchers).
3. **Wildcard match** — the function's directory appears as a glob in an Entry field, e.g. `co_cli/tools/google/*` (covers every function under that directory).
4. **Multi-entry match** — Entry fields joined by `+`, `;`, or `→` count each segment independently for the three checks above.

A function is a **Registry Gap** only when none of the four forms covers it. Report:
```
✗ REGISTRY GAP: <file>: <function> — no matching workflow entry
Recommended next step: add workflow entry or classify as sub-step
```
If it is a helper or sub-step of a registered workflow, record as Sub-step (no gap). Registry gaps do not block Phase 1 — they are escalated in the Phase 8 report.

### Exit

After both checks complete (and any approved fixes are applied), announce `✓ Reference docs in sync with source` (with stale-fix count and registry-gap count) and proceed to Phase 1.

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
   - `tests/_co_harness.py` — pytest plugin loaded via `-p _co_harness`; per-test span diagnostics; does not define test-writing rules but informs what infrastructure runs around every test
6. Populate the tracking log (created in Phase 0) with the full file list as `[ ]` pending entries.
7. Announce scope: scan path, file count, workflow count, tracking log path.

---

## Phase 2 — Full Read

**Read every test file in full** — not via Explore, not via excerpts. Use the Read tool on each file, starting at line 1, reading to EOF. Process in directory batches.

For each test, build a structured record. Rule classification is Phase 3's job — Phase 2 captures facts only.

### Catalog schema (per-test record)

Persist these records to the tracking log under a `### Test Catalog` heading, one block per file. Phase 3 (rule audit), Phase 4 (coverage), and the subsumption / cross-file-overlap detectors (Phase 3) all read from this same catalog — a consistent shape is what makes them effective.

```yaml
- test: <file_path>::<function_name>
  prod_imports:                    # imports under co_cli.*
    - co_cli.tools.memory.recall: [knowledge_search, _grep_recall]
    - co_cli.deps: [CoDeps, CoSessionState]
  entry_called:                    # production entry-point functions/classes the test actually invokes
    - knowledge_search                # public surface
    - _grep_recall                    # private helper
  failure_modes_asserted:          # canonical workflow-id::mode references, plus novel modes
    - "12.4::grep fallback returns empty when store=None"
    - "12.5::current session included"
    - "novel::hits never carry 'channel' field"
  assertion_shape:                 # what shape of value is checked (used by Phase 4 depth check)
    - "equality on result.metadata['count']"
    - "set membership on result.metadata['results'][0].keys()"
    - "regex on result.return_value"
  uses_real_llm: false             # true iff test makes an actual LLM call
  fixtures: [tmp_path]             # pytest fixtures injected
```

#### Controlled vocabulary for `failure_modes_asserted`

To make subsumption and cross-file-overlap detection deterministic across runs, `failure_modes_asserted` uses a **closed vocabulary keyed off the registry**:

1. For each assertion the test makes, find the registry workflow it defends (via `entry_called`).
2. **Parse that workflow's `Primary failure modes` line**: take everything after `**Primary failure modes**:`, strip leading/trailing whitespace and any trailing period, then split on `;`. Each resulting segment (whitespace-stripped) is one canonical phrase. Example: `"env var not honored; secrets file overrides shell vars; invalid config silently accepted; precedence inverted."` → four phrases.
3. Match the assertion's intent to one of those canonical phrases. Encode as `"<workflow_id>::<canonical_phrase>"` — using the phrase verbatim so two runs converge on the same string.
4. If the assertion defends a failure mode the registry does **not** list, record it as `"novel::<short_phrase>"`. Each novel entry is a signal that the registry is incomplete and should be updated — surface novel modes in the Phase 8 report alongside Registry Gaps.

This anchoring guarantees set-subset comparisons (Phase 3 subsumption) and overlap intersection (Phase 3 cross-file) operate on canonical strings, not free-text variants of the same idea.

After cataloguing each file, mark it `[x] READ` in the tracking log; release per-file source detail from active context — the catalog holds what later phases need.

### Stale call-signature check

For each call site with non-trivial args (beyond a single positional), verify the live signature hasn't drifted — stale parameter mismatches (kwarg the production function no longer accepts) are Blocking findings.

---

## Phase 3 — Rule Audit + Adversarial

`agent_docs/testing.md` as loaded in Phase 1 is the sole source of truth for rules. Do not enforce any rule not present there — Phase 0 guarantees it is synced with source. Classify each violation as **Blocking** or **Minor** per the severity labels in `testing.md`, and cite each finding by the exact bullet keyword from that doc.

**Challenge every test's classification — clean or suspicious.** For each test in the Phase 2 catalog:

- *Apparent PASS:* Is the pass actually behavioral, or did you assume it because it calls a real function? Can you name the specific user-visible failure mode this test catches? Would deleting it let a real regression go undetected? If not — upgrade to a violation.
- *Apparent FAIL:* Is the violation real, or is there a conftest fixture wiring the real production path? Is the "duplicate" testing a meaningfully different failure mode? If the issue is a false positive — downgrade or remove.

When a finding comes from a grep / regex sweep, verify it by reading the surrounding lines in the source file before recording. Single-file checks suffice for most rules; the next subsection covers cross-test patterns that require comparing catalog records.

Only findings that survive this challenge are recorded and proceed to Phase 6.

### Detection methods — cross-test patterns

`testing.md`'s **Suite hygiene** rule names `Subsumed file` (entire test file is subset of another) and `Duplicate with trivial delta` (two tests asserting the same invariant). Both extend naturally to **unit-level subsumption** and **cross-file overlap** — same rule, finer grain — which cannot be detected by single-file grep. The algorithms below correlate Phase 2 catalog records to surface these as Suite-hygiene findings.

**Subsumed unit test** (Suite hygiene, finer grain than Subsumed file) — exercises a helper in isolation when a workflow test already drives it end-to-end with no extra failure mode covered. Cite as `Suite hygiene — subsumed unit`.

Algorithm (deterministic):
1. From the catalog, find every test `U` whose `entry_called` contains at least one private helper (name starting with `_`).
2. For each such test `U` calling private helper `_h`, find every candidate test `W` in the catalog whose `entry_called` includes a *public* entry `P` from the same module as `_h`.
3. **Bounded transitive-call confirmation**: read the public entry `P`'s source body **exactly once** (one `Read` call, no recursive expansion). Scan the body for the literal token `_h(` — direct call only. If `_h(` does not appear in `P`'s body, treat `W` as **not** a transitive caller of `_h` and move on. Do not follow further calls; do not read additional files. This bounds the work to a single source read per (`U`, `W`) pair.
4. If the public entry directly calls `_h` AND `set(U.failure_modes_asserted) ⊆ set(W.failure_modes_asserted)` (using the canonical references from the controlled vocabulary), `U` is **Subsumed** by `W` — flag for trim.
5. If `U` asserts at least one failure mode not in `W`, the test is **not** subsumed; preserve it.

**Cross-file overlap** (Suite hygiene, cross-file variant of Duplicate-with-trivial-delta) — same workflow + same observable outcome in two different files. Cite as `Suite hygiene — cross-file overlap`.

Algorithm:
1. Group catalog records by the public entry in their `entry_called` list (the entry that maps to the registry workflow).
2. Within each group, find pairs `(A, B)` where `A.test.file != B.test.file` and `set(A.failure_modes_asserted) ∩ set(B.failure_modes_asserted)` is non-empty.
3. For each overlapping failure mode, the earlier-defined test wins; the later one is flagged for trim or merge.
4. If both tests assert the same failure mode but with different assertion shapes (one regex, one value-equality), keep the stricter one; trim the looser.

Both detectors run after the per-file rule audit (above) and feed their findings into the same Phase 4 trim-candidate list.

---

## Phase 4 — Coverage Audit

For every workflow in `agent_docs/system-workflows-to-test.md`:

1. From the Phase 2 catalog, find tests whose `entry_called` includes the workflow's **Entry**.
2. Check `failure_modes_asserted` against the workflow's **Primary failure modes**. A workflow is **Covered** only when ≥1 covering test asserts on at least one Primary failure mode (not just the happy path).
3. **Required-test-depth check.** For each Covered workflow, compare the covering test's `assertion_shape` and `uses_real_llm` against the workflow's **Required test depth** field using a **closed set of depth-signal patterns**. The agent does not decide which tokens are load-bearing — the patterns are fixed.

   Extract depth signals from the registry's `Required test depth` text by regex:

   | Pattern                                | Required catalog field          | Pass condition                                                               |
   |----------------------------------------|---------------------------------|------------------------------------------------------------------------------|
   | `real (llm\|ollama\|model)`            | `uses_real_llm`                 | must be `true`                                                              |
   | `real (fts5\|sqlite\|store\|index)`    | `prod_imports`                  | must include `MemoryStore` or `co_cli.memory.*`                              |
   | `real (filesystem\|file\|tmp_path)`    | `fixtures`                      | must include `tmp_path`                                                      |
   | `real (settings\|config)`              | `prod_imports`                  | must include `co_cli.config.core` or `tests._settings`                       |
   | `real (mcp\|subprocess)`               | `prod_imports`                  | must include `co_cli.agents.mcp` or `co_cli.tools.background`                |
   | `assert ([a-z_][a-z_0-9]*)`            | `assertion_shape`               | at least one entry must reference the captured noun (case-insensitive substring) |
   | `no (mocks\|fakes\|llm\|api)`          | catalog flags                   | for `no mocks/fakes`: no `MagicMock`/`monkeypatch` in test; for `no llm/api`: `uses_real_llm` must be `false` |
   | `(skip\|skipif) (when\|on) (.+)`       | (informational only)            | not a depth check — record as a precondition note                            |

   Algorithm: scan the registry's `Required test depth` field once; emit a list of `(pattern, captured_token)` pairs; for each pair, check the pass condition against the catalog record. If **any** pair fails, downgrade the classification to **Depth-mismatch**.

   A Depth-mismatch is a Minor finding with the **SURFACE** tag: the test exists and touches the entry, but doesn't probe what the registry says it must. SURFACE means "list in the Phase 8 report and the coverage table" — it does **not** mean "escalate to a follow-up plan". Phase 6's "escalate" is a different action (architectural decisions, API changes); these two terms must not be conflated.
4. Final classification per workflow: **Covered** (entry reached + failure mode asserted + depth matches), **Depth-mismatch** (entry reached + failure mode asserted but depth wrong), **Stub-covered** (entry reached but assertion is structural or happy-path only), **Uncovered** (no test reaches the entry point).
5. **Scope-drift sweep**: identify tests in the Phase 2 catalog whose `entry_called` doesn't map to any registered workflow. These are trim candidates by default — new-workflow proposals were already surfaced by Phase 0's registry-completeness check, so a test with no anchoring workflow at Phase 4 time is unanchored fluff.

**Severity:** user-facing workflow uncovered → Blocking; internal mechanism uncovered → Minor unless it gates a user-facing workflow; stub-covered → Minor with SURFACE tag; depth-mismatch → Minor with SURFACE tag.

Coverage gaps **escalate, don't auto-fix**:
```
✗ ESCALATE: workflow N.M (<name>) — Uncovered / Stub-covered (<reason>)
Entry: <file:function>
Primary failure modes to probe: <bullet list from registry>
Recommended next step: open follow-up exec plan for new behavioral test
```

Trim candidates from Phase 3 (subsumed unit, cross-file overlap, happy-path-only) are already in the trim list — Phase 4 does not duplicate them here, but the consolidated trim list becomes the input to Phase 5 and Phase 6 step B.

### Scaling — sample-based audit

When `workflows × tests > 3000` (e.g. 112 workflows × 56 tests = 6,272 pairs), a full cross-product audit in one invocation is impractical. Two paths:

- **Full mode** (small suites): audit every workflow as written above.
- **Sample mode** (large suites): audit a representative subset (selection rules below). Sampled-out workflows are **Unaudited this run**, not "Covered" by default.

Sample mode is the default when the scaling threshold is exceeded.

#### Cross-run coverage state

To make sample-mode rotation deterministic across runs, the skill persists workflow audit timestamps in:

```
docs/clean-tests-coverage-state.json
```

Schema (`schema_version: 1`):
```json
{
  "schema_version": 1,
  "workflows": {
    "1.1": {"last_audited": "2026-05-14T22:30:36", "last_classification": "Covered"},
    "1.2": {"last_audited": "2026-05-14T22:30:36", "last_classification": "Stub-covered"}
  }
}
```

#### Sample selection (deterministic priority order)

1. On Phase 4 entry, read the coverage-state file (or treat as empty if absent).
2. **Tier 1 — high-signal** (always audit): every workflow whose `entry_called` is mentioned by at least one test in the Phase 2 catalog. These are workflows the suite is actively defending; we must verify the defense holds.
3. **Tier 2 — never-audited** (fill quota first): workflows with no entry in the state file.
4. **Tier 3 — oldest-audited** (fill remaining quota): workflows sorted ascending by `last_audited`. Tie-break by workflow id.
5. Compute the per-run quota as:
   ```
   quota = max(ceil(0.25 * total_workflows),
               min(total_workflows, floor(3000 / total_tests)))
   ```
   Stop sampling when `audited_count >= quota`. Worked example: for 112 workflows × 56 tests, `floor(3000 / 56) = 53` and `ceil(0.25 * 112) = 28` — quota is 53, so two-to-three runs cover the full registry. The clamp at 25% guarantees forward progress even when `tests` is very large.

After Phase 4 completes, write the state file: every audited workflow gets a fresh `last_audited` (= invocation timestamp) and its current `last_classification`. Sampled-out workflows are left untouched (preserving their prior audit timestamp).

The Phase 8 report lists audited vs sampled-out workflow ids explicitly, so a human inspector can see the rotation in action across reports.

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
**D. Address Minor findings** from Phase 3: fix inline if the change is trivial (≤ 5 lines, no new dependency); otherwise log as `MINOR-DEFERRED` in the tracking log and skip. Never escalate Minor findings.

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

## Phase 7 — Test Suite (full or scoped)

### Full suite (default)

```bash
mkdir -p .pytest-logs
uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests.log
```

### Scoped regression (narrow-fix mode)

When Phase 6 modified **≤ 3 test files** and made **no production code changes**, a scoped regression on just the affected files is sufficient as a Phase 7 gate within this skill:

```bash
mkdir -p .pytest-logs
uv run pytest <modified_files> -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests-scoped.log
```

The full suite remains required at `/ship` — this scoped path only relaxes the in-skill gate when the modification surface is provably narrow. If Phase 6 touched any production source file or more than 3 test files, run the full suite.

### Failure handling

Any failure: stop immediately. Read the failing test, read the full traceback, trace to root cause in source. Fix the root cause — never modify a test to make it pass unless the test is stale (API removed or renamed). Re-run until green. Never dismiss a failure as flaky without running it 3× and identifying the specific cause.

Only proceed to Phase 8 with a fully green run (whichever scope was used).

---

## Phase 8 — Report

Update the tracking log: set Status DONE, record the Phase 7 result, write the final verdict line.

### Verdict gate (mechanical)

Verdict is computed from a fixed checklist — not by judgment. Three tiers, evaluated in order:

| Condition (evaluated top-down)                                          | Verdict             |
|--------------------------------------------------------------------------|---------------------|
| Phase 0 was aborted (user denied a fix), OR                              | **FAIL**            |
| any Phase 3 Blocking finding remained `OPEN` at end of Phase 6, OR       | **FAIL**            |
| Phase 7 (full or scoped) did not finish green                            | **FAIL**            |
| Phase 4 has ≥1 user-facing **Blocking** uncovered workflow, OR           | **ESCALATIONS PENDING** |
| Phase 6 produced ≥1 architectural escalation, OR                         | **ESCALATIONS PENDING** |
| Phase 0 reported ≥1 unresolved Registry Gap, OR                          | **ESCALATIONS PENDING** |
| Phase 2 catalog contains ≥1 `novel::…` failure mode (registry incomplete), OR | **ESCALATIONS PENDING** |
| Phase 4 produced ≥1 Depth-mismatch                                       | **ESCALATIONS PENDING** |
| none of the above                                                        | **CLEAN**            |

Deferred Minor findings (Phase 6 step D `MINOR-DEFERRED`) do **not** prevent CLEAN. They are visible in the report but do not gate the verdict — by design: Minor findings that aren't trivial to fix shouldn't block a passing run.

After applying this gate, print the terminal summary:

```
## clean-tests — <date>

Ref doc sync: OK / N stale refs fixed
Registry gaps (Phase 0, source→registry, escalated): N
Files scanned: N
Blocking findings fixed: N
Minor findings: N fixed inline / M deferred
Tests trimmed: N (subsumed / overlapping / happy-path-only)
Files consolidated: M (from K → J)
Workflows: N covered, D depth-mismatch, M stub, K uncovered, U unaudited-this-run (J blocking)
Scope drift: P tests with no workflow mapping
Coverage escalations (Phase 4): N
Auto-fix escalations (Phase 6): N
Tests: N passed, 0 failed
Log: .pytest-logs/<timestamp>-clean-tests.log
Report: docs/REPORT-clean-tests-<timestamp>.md

Verdict: CLEAN / ESCALATIONS PENDING / FAIL — <one sentence citing the gate condition>
```

Full detail lives in the report file — do not repeat it in the terminal summary.
