---
name: clean-tests
description: Keep the suite focused on functional behavior verification. Purge dead, structural, unit-only, duplicate, low-ROI, backward-compat, underlying-lib, and noise tests. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** every surviving test must catch a real regression in co-cli's system functionality or agent behavior. The target: tool contracts, agent loop mechanics, approval logic, memory/session operations, config loading, security boundaries, CLI command behavior, compaction, and skill management. Delete anything that doesn't — structural checks, library/SDK verification, trivial passthroughs, and mocked behavior are all noise.

**Default stance:** violations exist. PASS is earned, not assumed.

**Non-negotiable:** Pass 1 requires reading every test file with the Read tool and annotating every `def test_*` function. Grep patterns are supplementary triage only. For every batch, the private-call inventory must complete before any annotation row is written — skipping it is a protocol violation even if all verdicts would be KEEP.

**Produces:** terminal summary + `.pytest-logs/<timestamp>-clean-tests.log`.

**Verdict:** binary **PASS** / **FAIL**. Any unfixed Blocking, lint error, or failing new-test run → FAIL.

---

## Pass 0 — File-level triage

Run once across all in-scope test files before touching individual tests.

### 0a — Broken imports

```bash
uv run pytest --collect-only 2>&1 | grep -E "ERROR collecting"
```

BROKEN file → every test in it is Rule 4. Fix the import or delete the file outright.

### 0b — Bad patterns

```bash
grep -l "monkeypatch\|unittest\.mock\|from unittest import mock\|patch\b" tests/test_*.py
grep -l "assert True\b\|assert result\b\|assert result\." tests/test_*.py
grep -l "from.*_legacy\|from.*_compat\|from.*_old\b" tests/test_*.py
grep -l "inspect\.signature\|hasattr(" tests/test_*.py
grep -l '"--help"\|CliRunner.*--help\|exit_code == 0' tests/test_*.py
grep -l "assert.*\" in prompt\|assert.*\" in result\b" tests/test_*.py
```

Pre-tag files `SUSPECT_MOCK`, `SUSPECT_STRUCTURAL`, or `SUSPECT_LIB`. Does not replace reading.

**SUSPECT_LIB trigger**: grep for calls to third-party library functions that co-cli only thinly wraps — trafilatura, markdownify, httpx, google.oauth2. If the test's assertions would still pass if you deleted our wrapper and called the library directly → rule 10. (`inspect.signature` / `hasattr` usage is a structural assertion about our own code, not library behavior → SUSPECT_STRUCTURAL / rule 4.)

### 0c — Clustered test names (rule 6 trigger)

```bash
grep -h "^def test_" tests/test_*.py | sed 's/(.*$//' | sed 's/^def //' | \
  awk -F_ '{print $1"_"$2"_"$3}' | sort | uniq -c | sort -rn | head -20
```

Flag any 3-part prefix with count ≥ 2. During Pass 1, read both bodies side-by-side before deciding. Common sub-patterns:
- **Enumerated-input negatives**: `test_blocks_X / _Y / _Z` all hit the same branch with different inputs — keep the most representative, delete the rest.
- **Format-template variants**: verify the same formula with different values — keep a boundary pair, delete the rest.

### 0d — Merge candidates

```python
# python3 - << 'EOF'
import glob, re
files = {}
for f in sorted(glob.glob("tests/test_*.py")):
    mods = set()
    for line in open(f):
        line = line.strip()
        if line.startswith("from co_cli") or line.startswith("import co_cli"):
            m = re.match(r"(?:from|import) (co_cli[.\w]*)", line)
            if m: mods.add(m.group(1))
    files[f] = mods
names = list(files)
for i in range(len(names)):
    for j in range(i+1, len(names)):
        shared = files[names[i]] & files[names[j]]
        if len(shared) >= 4:
            print(f"[{len(shared)} shared] {names[i]} <-> {names[j]}")
            for s in sorted(shared): print(f"  {s}")
# EOF
```

Merge candidate: ≥50% of tests in each file call from the **same** production module. NOT a merge when shared imports are all infrastructure (`CoDeps`, `ShellBackend`, `CoSessionState`). Canonical filename: `co_cli/<pkg>/<module>.py` → `test_flow_<pkg>_<module>.py`.

### 0e — Zero-coverage tool surfaces

```bash
grep -rn -E '^\s*@agent_tool' co_cli/tools/ | grep "def " | sed 's/.*def //' | sed 's/(.*$//'
```

Cross-reference against `ls tests/test_flow_*.py`. Any `@agent_tool` function with no corresponding test file is a coverage gap — record for Pass 2 backfill.

---

## Pass 1 — Per-test audit

Process in **batches of 5 files**.

**Large suite (> ~30 files):** fan the *read + annotate + private-call inventory* out to **read-only** audit subagents, one per domain group (e.g. compaction, memory, skills, agent-loop, tools/IO/session, bootstrap/display/CLI, daemons/integration). Each returns its annotation table + cross-file subsumption notes and must NOT edit, delete, or write any file. The orchestrator resolves cross-file subsumption and executes **every** deletion/fix itself — keeping source-verification and cross-file safety in one place. Subagents systematically over-flag rule 6; treat their DELETE rows as candidates, not decisions, and re-verify each against source (see Same-branch proof) before removing.

**For each batch:**

1. **Read every file** with the Read tool. Offset/limit chunks if needed — read every line.

2. **Private-call inventory — mandatory before any annotation row.**

   ```bash
   grep -n "from co_cli" <file> | grep " _[a-z]"
   grep -En "[a-z_]+\._[a-z][a-z_]*\(" <file> | grep -v "#"
   grep -En "[a-z_]+\._[A-Z_][A-Z0-9_]+" <file> | grep -v "#"
   ```

   **Exclude** constants used solely as assertion boundaries (e.g. `assert len(hits) <= _USER_PRIORITY_CAP`). These bind assertions to the production value — not a Rule 8 violation.

   For each remaining private symbol:
   a. `grep -rn "def _symbol" co_cli/` — locate definition.
   b. `grep -rn "_symbol" co_cli/` — find all production callers.
   c. Identify which public entry points reach it.
   d. Check whether an existing test covers those entry points with inputs that hit the same branch.
   - Reachable + existing test covers same branch → Rule 8 candidate
   - Not reachable via public entry for this input class → keep

   Record conclusion per symbol before writing any annotation row.

3. **Annotate every `def test_*`** before moving to the next batch:

   | file | test_name | production_fn_called | verdict | rule |
   |------|-----------|----------------------|---------|------|

4. Evaluate rules 1→10 in order. Stop at first Blocking violation per test.

5. Execute deletions/fixes for the batch before proceeding. **Dependency check:** confirm the subsuming test is not itself flagged for deletion.

### Deletion test (apply before flagging any rule)

> "If deleted, will a real regression in system functionality or agent behavior go undetected by any remaining non-deleted test?"  
> Yes → keep (note the specific behavior guarded). No → flag.

A KEEP requires naming the concrete failure mode: e.g. "guards path-traversal boundary", "catches silent counter reset on run_step change", "verifies error is terminal not retried". "It tests this function" is not a reason to keep.

### Same-branch proof — required before flagging rules 5, 6, or 10

Subsumption and duplication are claims about the **production code path**, not the test text. Two tests that look alike often hit different branches. Before flagging, open the production function and prove it:

- **Read the branch.** A single regex / dispatch that handles several inputs in one path = same branch → flag the redundant one (e.g. `,\s*([}\]])` strips trailing commas in both objects and arrays). A function that *branches on the input* = **distinct paths, keep both**, even when the assertions read identically (e.g. `_file_search_marker` branches on `args.get("content")` → content-search vs listing are different code with different output; `_repair_json_args` Pass 5 re-strips a comma *after* balancing a brace, which the no-comma unclosed test never reaches).
- **A single-call test does not subsume an accumulation / re-trigger test.** count→2 (no-reset), fires-again-after-reset (re-arm), and "valid item in a rejected batch is also discarded" (all-or-nothing) each guard a failure mode a one-shot test never exercises.
- **A terminal/integration test does not subsume a unit test unless it asserts the same observable.** "Drives the same function" is not enough — check the assertion. A drain test that is the *sole* proof a hook is invoked is not subsumed by a test of the hook in isolation.
- **A constraint-rejection or computed-relationship assertion is behavioral, not structural.** `pytest.raises(ValidationError)` on a removed/forbidden field is rule 10 (library-enforced) → delete; but a Literal-rejection that is the *only* guard against a downstream corruption path passes the deletion test → keep. `span.attr == len(results)` ties a computed value → keep; `span.attr == <echoed input>` is wiring → rule 4.

This is the single most common clean-tests error: deleting a test that *looks* like a duplicate but guards a distinct branch. When in doubt, read the source, not the test.

### Blocking — default DELETE

Fix only when: (a) unique coverage AND (b) the fix is rerouting through a public entry point (Rule 5a) or a one-line value/import correction (Rules 3, 7). Otherwise delete.

| # | Rule | Action |
|---|------|--------|
| 1 | **Mock/patch** — `monkeypatch`, `unittest.mock`, hand-rolled fakes replacing production behavior. Input literals are fine; fake return values are not. | Delete |
| 2 | **Vacuous assertion** — no meaningful assertion present: no assertion at all (drives code but never fails), truthy-only (`assert True`, `assert result`, `assert result.flag`), or always-true by Python semantics (`assert x == x`). | Delete |
| 3 | **Stale signature** — calls function with args that don't exist in current source. | Fix or delete |
| 4 | **Structural-only** — asserts existence/shape/presence rather than behavioral values. Passes regardless of what the production code actually computes. Sub-cases: dataclass field defaults, key or attribute existence (`assert "key" in attrs`), schema shape (`len(result) == N`), importability, CLI `--help` option presence, string-in-assembled-prompt, trivial passthrough (`assert x.mode == mode` where mode is the direct input), object identity after simple assignment. Any test that would still pass if the asserted value were replaced with any other truthy value belongs here. | Delete |
| 5 | **Subsumed coverage (cross-layer)** — a test in a *different* file/layer already reaches the same branch + observable; this test adds no unique failure mode. Two patterns — confirm via Same-branch proof before deciding: **(a) Private helper**: calls `_private()` while a public-entry test covers the same branch — FIX by rerouting through the public entry; delete if no unique observable survives. **(b) Pipeline-internal**: drives a non-terminal pipeline step while a terminal test covers the same failure mode — keep only if that input cannot reach the terminal; delete otherwise. (Same-*file* redundancy → rule 6.) | Fix (5a) or delete |
| 6 | **Same-file redundancy** — another test *in the same file* already catches this regression: either an exact near-duplicate (same function, same branch, same observable) or a narrower subset (this test asserts a property the broader test already asserts on the same function). Read both bodies before deciding. | Delete the weaker/narrower |
| 7 | **Backward-compat shim** — imports from `*_legacy`/`*_compat`/`*_old`; docstring mentions `deprecated`/`legacy`. | (a) Update import. (b) Rerun; update expected value if still fails. (c) Delete if verifying an alias with no non-test callers. |
| 8 | **Test isolation** — test mutates shared state without cleanup: **(a)** writes to `~/.co-cli/`, `USER_DIR`, `CO_HOME` without routing through `tmp_path`; **(b)** sets/deletes `os.environ` without `try/finally`; **(c)** mutates shared module-level state (registries, caches, singletons, theme/console) without restoration — the classic source of batch-only-flaky failures (passes alone, fails in-suite). Exempt: `SETTINGS`, `SETTINGS_NO_MCP`, cached models. | Fix |
| 9 | **Async discipline** — incorrect asyncio around external `await`s: **(a)** `ensure_ollama_warm` called inside `asyncio.timeout` (infrastructure prep, not behavior under test); **(b)** multiple LLM awaits sharing one `asyncio.timeout` budget; **(c)** a real-LLM / network / subprocess `await` with no `asyncio.timeout` wrapper at all (a stalled call otherwise hangs to the 180s pytest ceiling). Import constants from `tests._timeouts`. | Fix |
| 10 | **Underlying library/SDK behavior** — the system under test is a third-party library's own behavior (trafilatura, httpx, anthropic SDK, google-auth, pydantic-ai internals), not co-cli's logic. Decision test: would the assertions still pass if you deleted our wrapper and called the library directly? If yes → library test. Examples: trafilatura extracting article prose from HTML; `google.oauth2.Credentials` round-tripping via google-auth's own serialization; httpx retry/redirect behavior. (Structural assertions made *via* a library — `inspect.signature`, `hasattr(module, "name")`, type-annotation checks — are not "library behavior"; they're structural → rule 4.) | Delete |

**Rule-failed file:** ≥50% Blocking with no unique coverage surviving → delete the file.

### Minor — record, fix if trivial

- Test name describes arrangement not behavior.
- **Degenerate LLM test**: assertion passes regardless of model output. List; do not auto-delete.
- **Trim candidate**: weak sub-assertion inside an otherwise strong test. Note for Pass 2.

(Missing `asyncio.timeout` → rule 9c; unrestored module-level state → rule 8c. Both are Blocking-fix, not Minor.)

---

## Pass 2 — Structural cleanup

1. **Fix BROKEN files** (Pass 0a): update import or delete.
2. **Merge duplicate files** (Pass 0d): copy unique tests to canonical file; delete non-canonical. If canonical doesn't exist, rename the more complete file then merge.
3. **Delete rule-failed files**.
4. **Trim candidates**: strip weak sub-assertion, keep behavioral core. **Graft-then-delete**: if a test flagged for deletion (rule 5/6) carries one unique assertion the survivor lacks — e.g. move-not-copy `not src.exists()`, a specific propagated value, an FTS-only field — graft that assertion onto the survivor *first*, then delete. Never lose a unique observable just because the test is otherwise redundant. After deleting a test, remove imports/constants/helpers it solely used (lint catches these — fix them, don't blanket `--fix` real findings away).
5. **Backfill missing test files** (Pass 0e + Pass 3 depth gaps): for each zero-coverage `@agent_tool` surface, create `test_flow_<pkg>_<module>.py`. Each new file must include:
   - At least one success-path test per tool function.
   - At least one rejection/error-path test per tool function.
   - For any `Literal[...]` parameter, at least one test per semantically distinct value.
6. If any deletion leaves a production helper with zero callers, flag it in the summary — do not delete production code.

---

## Pass 3 — Verify

1. **Coverage depth check**: for each `@agent_tool` confirmed to have at least one test, verify:
   - At least one test exercises a rejection/error path (not only the success path).
   - For any `Literal[...]` parameter, at least one test per distinct value exists.

   Missing either → treat as a gap and backfill per Pass 2 step 5.

2. **Run new and modified tests**: run any file created or significantly changed during this run:

   ```bash
   mkdir -p .pytest-logs
   uv run pytest -x -v <changed-files> 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests-new.log
   ```

   A failure here is usually a test-fixture bug from your edit — diagnose and fix. Two exceptions to rule out before blaming your change:
   - **Cold-start LLM:** a real-LLM test that fails on first invocation but passes warm is cold model load, not a defect. Warm the model first; never raise the timeout to absorb it.
   - **Batch-only non-LLM failure:** a non-LLM test that fails in a multi-file run but passes in isolation *and* as its own file is a pre-existing cross-test global-state leak (rule 8c — fix the *mutating* test, not the victim) or hash-seed nondeterminism. Deletions cannot introduce state mutation, so bisect to confirm it isn't yours, then surface it as a separate finding — it does not block the verdict if your changes didn't cause it.

   Verify the deletions themselves by **name-absence** (`grep -rn "def <name>" tests/` returns 0) and the removed-`def test_` diff list — not by collected-count arithmetic, which is unreliable when the working tree carries concurrent changes.

3. **Lint**:

   ```bash
   scripts/quality-gate.sh lint
   ```

   Fix every issue. No `# noqa` without an explanatory comment.

4. **Full suite**: skip per project policy. Mark as `PASS (conditional — suite skipped per project policy)`.

   If explicitly requested:

   ```bash
   uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests.log
   ```

   - LLM test fails → rerun in isolation. Passes alone → non-determinism; note and restart without `-x`. Fails consistently (≥2 runs) → real failure; diagnose.
   - Non-LLM test fails → diagnose root cause, fix, rerun. Never report PASS until green.

---

## Verdict + terminal summary

- **PASS**: all Blocking resolved + lint clean + new tests green. Suite skipped — note as `PASS (conditional)`.
- **PASS (suite)**: above + full suite green (only when explicitly requested).
- **FAIL**: any unfixed Blocking, lint error, or failing new test.

```
## clean-tests — <date>

Test files scanned: N
Broken files fixed/deleted: N — [list]
Duplicate files merged: N — [pairs: source → target]
Rule-failed files deleted: N — [list]

Tests audited: N  ← must equal total collected; no sampling
Tests deleted: N total
  Mock/patch (rule 1):          N
  Vacuous assertion (rule 2):   N
  Structural-only (rule 4):     N
  Subsumed (rule 5):            N
  Same-file dup (rule 6):       N
  Backward-compat (rule 7c):    N
  Underlying lib/SDK (rule 10): N
Tests fixed: N total
  Stale signature (rule 3):     N
  Backward-compat (rule 7a/b):  N
  Test isolation (rule 8):      N
  Async discipline (rule 9):    N
  Private-call rerouted (5a):   N
Minor findings: N (noted/deferred)
Degenerate LLM tests: N — [list]
Trim candidates resolved: N
Tests moved to canonical files: N
Zero-coverage surfaces backfilled: N — [list]
Coverage depth gaps fixed: N — [list]
Coverage gaps remaining: N — [list with waivers]
Separate-cleanup candidates: N — [list]
LLM non-determinism events: N — [test + run count]
New/modified tests: N passed, 0 failed
Suite: N passed, 0 failed (or: skipped per policy)
Log: .pytest-logs/<timestamp>-clean-tests.log

Verdict: PASS / FAIL — <one sentence>
```
