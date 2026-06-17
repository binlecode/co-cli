---
name: clean-tests
description: Keep the suite focused on functional behavior verification. Purge dead, structural, unit-only, duplicate, low-ROI, backward-compat, underlying-lib, and noise tests. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** every surviving test must catch a real regression in co-cli's system functionality or agent behavior. The target: tool contracts, agent loop mechanics, approval logic, memory/session operations, config loading, security boundaries, CLI command behavior, compaction, and skill management. Delete anything that doesn't — structural checks, library/SDK verification, trivial passthroughs, and mocked behavior are all noise. Two axes decide a test's fate: **noise** (rules 1–10, auto-removed) and **criticality** (the Criticality gate — is the guarded regression worth gating on?). A genuine, unique behavioral test can still be cut if the failure it guards is too trivial for a critical-path suite.

**Default stance:** violations likely exist. PASS is earned, not assumed. (A mature suite can genuinely have zero Blocking violations — when so, do not manufacture deletions; the Criticality stratification becomes the primary output.)

**Non-negotiable:** Pass 1 requires reading every test file with the Read tool and annotating every `def test_*` function. Grep patterns are supplementary triage only. For every batch, the private-call inventory must complete before any annotation row is written — skipping it is a protocol violation even if all verdicts would be KEEP.

**Produces:** terminal summary + `.pytest-logs/<timestamp>-clean-tests.log`.

**Verdict:** binary **PASS** / **FAIL**. Any unfixed Blocking, lint error, or failing new-test run → FAIL.

---

## Pass 0 — File-level triage

Run once across all in-scope test files before touching individual tests.

`tests/` is **not flat** — it has nested dirs (`tests/tools/`, `tests/daemons/`, `tests/integration/`, `tests/observability/`, `tests/commands/`, `tests/skills/`). Every detector below scans the **full tree** (`grep -r --include="test_*.py" … tests/`, recursive glob). A flat `tests/test_*.py` glob silently drops ~20% of the suite — never use one.

### 0a — Broken imports

```bash
uv run pytest --collect-only 2>&1 | grep -E "ERROR collecting"
```

BROKEN file → every test in it is Rule 4. Fix the import or delete the file outright.

### 0b — Bad patterns

```bash
grep -rl --include="test_*.py" "assert True\b\|assert result\b\|assert result\." tests/
grep -rl --include="test_*.py" "inspect\.signature\|hasattr(" tests/
grep -rl --include="test_*.py" '"--help"\|CliRunner.*--help\|exit_code == 0' tests/
grep -rl --include="test_*.py" "assert.*\" in prompt\|assert.*\" in result\b" tests/
```

Pre-tag files `SUSPECT_STRUCTURAL` or `SUSPECT_LIB`. Does not replace reading.

The **mock/patch ban (Rule 1)** and **backward-compat imports (Rule 7)** are no longer hand-detected here: they graduated to the Tier-2 fitness function `tests/test_arch_test_hygiene.py`, which fails `quality-gate.sh full` on any *new* violation. Do not re-grep for them — a new one can no longer reach `main`. The conceptual nuance still matters when burning down the baselined DEBT (see Rules 1 and 7): `monkeypatch.setenv`/`delenv` for `CO_HOME`/`HOME` is the *sanctioned* auto-restoring isolation primitive, never a violation; `monkeypatch.setattr(..., fake)` is the real rule-1 violation. The gate carries the existing sites in `tests/_test_hygiene_debt.txt`; this skill's job for those two classes is to **shrink that allowlist** by rerouting fake-injection sites through real production paths.

**SUSPECT_LIB trigger**: grep for calls to third-party library functions that co-cli only thinly wraps — trafilatura, markdownify, httpx, google.oauth2. If the test's assertions would still pass if you deleted our wrapper and called the library directly → rule 10. (`inspect.signature` / `hasattr` usage is a structural assertion about our own code, not library behavior → SUSPECT_STRUCTURAL / rule 4.)

### 0c — Clustered test names (rule 6 trigger)

```bash
grep -rhE --include="test_*.py" "^(async )?def test_" tests/ | sed -E 's/^(async )?def //; s/\(.*$//' | \
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
for f in sorted(glob.glob("tests/**/test_*.py", recursive=True)):
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
grep -rh -A8 -E '^\s*@agent_tool' co_cli/tools/ | \
  awk '/def [a-z]/{sub(/.*def /,""); sub(/\(.*/,""); print}' | sort -u
```

(The `-A8` window spans multi-line decorators; the `def [a-z]` match covers `async def`. A plain `grep '@agent_tool' | grep def` finds nothing — the decorator and `def` are on separate lines.)

Cross-reference each name against the **full** test tree — `grep -rl --include="test_*.py" "\b<tool>\b" tests/` — not just `tests/test_flow_*.py`. Coverage often lives in a nested dir (e.g. `tests/tools/memory/`), so a flat-glob check produces false zero-coverage hits. Any `@agent_tool` function with no test invocation anywhere is a real coverage gap — record for Pass 2 backfill.

### 0f — Mechanical bloat signals (deterministic modules only)

Two **objective** detectors find bloat without reading. Run them only on **pure-logic** modules (parsers, classifiers, math, state machines, formatters); skip real-LLM, subprocess, and integration tests — too slow / nondeterministic to attribute or mutate.

- **Per-test coverage overlap** — run the module's tests with per-test coverage contexts (`pytest --cov=<module> --cov-context=test`), then compute each test's *unique* contribution. A test that adds **zero** lines/branches no sibling covers is an objective subsumption candidate (rule 5/6). Confirm the observable differs before deleting — a test can re-cover the same lines yet assert a distinct value.
- **Mutation survival** — mutate the module (no mutation tool is a project dependency; run one on demand, e.g. `uvx mutmut run` / `uvx cosmic-ray`). A test that kills **no** mutant catches nothing (rule 2/4 — delete or strengthen), and a mutant **no** test kills is a coverage gap (Pass 2 backfill). It is the strongest oracle when run, but is opt-in, not part of a bare `pytest` pass; line-coverage % is not a substitute (fully-covered lines can still kill zero mutants).

What these **do not** catch: a low-criticality test can have unique coverage *and* kill unique mutants (a distinct-but-trivial branch). Mechanical signals detect noise and subsumption; the **Criticality gate** is still the only filter for real-but-low-value tests. Treat all three as candidate-generators — the deletion test and same-branch proof decide.

### 0g — Graduation to fitness functions (close the loop)

This skill is a Tier-1 enforcer (prose rules applied by hand). Per the Code Regulation Model in `.agent_docs/review.md`, a hand-applied rule that recurs is a signal to **graduate the mechanically-checkable subset to a Tier-2 fitness function** so it stops re-accreting between runs. Rule 1 (mock/patch ban) and Rule 7 (backward-compat imports in tests) have already graduated — they are enforced by `tests/test_arch_test_hygiene.py`, not by hand here. While scanning, flag any *other* grep-decidable, absolute violation class that you are deleting yet again — the open candidate is the `test_flow_*` file-naming policy. For each, record a **Graduation candidate** in the summary (rule + current violation count + whether it would land green or needs a baseline-and-ratchet DEBT allowlist). Do not author the gate inside this run — surface it; authoring is a separate plan task. This is what converts "delete the same class for the 26th time" into "delete it once, then a test keeps it dead."

---

## Pass 1 — Per-test audit

Process in **batches of 5 files**.

**Large suite (> ~30 files):** fan the *read + annotate + private-call inventory* out to **read-only** audit subagents, one per cohesive domain group. Each returns its annotation table + cross-file subsumption notes and must NOT edit, delete, or write any file. The orchestrator resolves cross-file subsumption and executes **every** deletion/fix itself — keeping source-verification and cross-file safety in one place. Subagents systematically over-flag rules 4, 5, and 6; treat their DELETE rows as candidates, not decisions, and re-verify each against source (see Same-branch proof) before removing. The recurring false positives: (rule 5) deleting the *sole* test that proves component A invokes B; (rule 4) mislabeling a computed-relationship or distinct-branch assertion as "shape"; (rule 6) deleting an "all-fields-present" test that is in fact the *inclusion* half of an inclusion/exclusion pair whose sibling tests only assert exclusion.

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

- **Read the branch.** One regex / dispatch that handles several inputs through a single code path = same branch → flag the redundant test. A function that *branches on its input* (mode dispatch, key-presence check, type switch, a later normalization/repair pass) = distinct paths → keep both, even when the assertions read identically.
- **A single-call test does not subsume an accumulation / re-trigger test.** No-reset (count accumulates), re-arm (fires again after reset), and all-or-nothing (a valid item in a rejected batch is also discarded) each guard a failure mode a one-shot test never exercises.
- **A terminal/integration test does not subsume a unit test unless it asserts the same observable.** "Drives the same function" is not enough — check the assertion. A test that is the *sole* proof one component invokes another is not subsumed by a test of either component in isolation.
- **A constraint-rejection or computed-relationship assertion is behavioral, not structural.** Asserting a library rejects a removed/forbidden field is rule 10 (library-enforced) → delete; but a constraint-rejection that is the *only* guard against a downstream corruption path passes the deletion test → keep. An assertion tying an emitted value to a *computed* quantity is behavioral → keep; an assertion tying it to an *echoed input* is wiring → rule 4.

This is the single most common clean-tests error: deleting a test that *looks* like a duplicate but guards a distinct branch. When in doubt, read the source, not the test.

### Criticality gate — severity of the guarded regression

The deletion test asks *whether* a regression would go undetected; this asks *whether it is worth gating on*. A test that survives rules 1–10 can still guard a failure too trivial for a critical-path suite. Tier every surviving test by the severity of the regression it guards:

- **Critical** — security/safety boundary; data loss or corruption; core control-flow or state-machine correctness; a silent wrong result reaching the user; a cross-process, durability, or coordination invariant.
- **Important** — public/tool contract correctness; accuracy of a stored or retrieved value; resolution/precedence logic; error classification and recovery.
- **Low** — presentation/formatting output; telemetry, log, or trace *shape*; a pinned constant or default value; one more enumerated input on logic an earlier test already covers; a defensive no-op or smoke check.

Low-tier tests are removal candidates even when behavioral and unique. The cut line is a maintainer decision — a strategy call on how much low-severity regression may ship silently — set per run, never an automatic delete. Default: report the stratification (counts + the Low list, each with its guarded failure mode); remove only at or below the stated line.

### Blocking — default DELETE

Fix only when: (a) unique coverage AND (b) the fix is rerouting through a public entry point (Rule 5a) or a one-line value/import correction (Rules 3, 7). Otherwise delete.

| # | Rule | Action |
|---|------|--------|
| 1 | **Mock/patch** — `monkeypatch.setattr`/`.setitem`/`.delitem`, `unittest.mock`, `MagicMock`, hand-rolled fakes replacing production behavior. Input literals and `monkeypatch.setenv`/`delenv` isolation are fine; fake return values are not. *New* violations are caught automatically by `tests/test_arch_test_hygiene.py` — do not hand-detect them. Here, drive the burn-down: reroute a `behavioral-fake` site in `tests/_test_hygiene_debt.txt` through a real production path, then remove its allowlist entry. | Reroute + shrink allowlist |
| 2 | **Vacuous assertion** — no meaningful assertion present: no assertion at all (drives code but never fails), truthy-only (`assert True`, `assert result`, `assert result.flag`), or always-true by Python semantics (`assert x == x`). | Delete |
| 3 | **Stale signature** — calls function with args that don't exist in current source. | Fix or delete |
| 4 | **Structural-only / weak-assertion** — asserts existence/shape/presence rather than the behavioral value, or passes when the feature silently returns nothing. Passes regardless of what the production code actually computes. Sub-cases: dataclass field defaults, key or attribute existence (`assert "key" in attrs`, `"session_id" in results[0]`), schema shape (`len(result) == N`), presence-only (`x is not None` where the value is knowable → assert the value), bound-only that passes on empty (`len(hits) <= cap`, `id not in returned` → assert the exact outcome or add a non-empty guard), exclusion-only (`1 not in lines` → also assert the inclusion), internal-seam testing (drives a private store/helper when the agent-facing tool/CLI is the surface the user calls), importability, CLI `--help` option presence, string-in-assembled-prompt, trivial passthrough (`assert x.mode == mode` where mode is the direct input), object identity after simple assignment. Litmus: replace the production function with `return []`/`return None` — if the test still passes, it belongs here. **Two litmus exceptions — pass the stub-litmus but KEEP:** (a) *defensive crash-guard* whose correct output on a degenerate input genuinely IS empty/None — a stubbed `return ""`/`None` masks the very crash-regression the test catches (e.g. None-snapshot → `""`, zero-divisor → None); the test fails by *raising* if the guard is removed. (b) *exclusion-only test whose inclusion half lives in a sibling test* (`assert "ctx" not in result` here, `assert "ctx 47%" in result` in `..._all_fields`) — the inclusion+exclusion pair is the correct shape; delete the exclusion half only when the guarded regression is Low-criticality AND the gate is trivial. (Full checklist + behavioral replacements: `.agent_docs/testing.md` → *Assertion strength*.) | Delete or strengthen |
| 5 | **Subsumed coverage (cross-layer)** — a test in a *different* file/layer already reaches the same branch + observable; this test adds no unique failure mode. Two patterns — confirm via Same-branch proof before deciding: **(a) Private helper**: calls `_private()` while a public-entry test covers the same branch — FIX by rerouting through the public entry; delete if no unique observable survives. **(b) Pipeline-internal**: drives a non-terminal pipeline step while a terminal test covers the same failure mode — keep only if that input cannot reach the terminal; delete otherwise. (Same-*file* redundancy → rule 6.) | Fix (5a) or delete |
| 6 | **Same-file redundancy** — another test *in the same file* already catches this regression: either an exact near-duplicate (same function, same branch, same observable) or a narrower subset (this test asserts a property the broader test already asserts on the same function). Read both bodies before deciding. | Delete the weaker/narrower |
| 7 | **Backward-compat shim** — imports from `*_legacy`/`*_compat`/`*_old`; docstring mentions `deprecated`/`legacy`. *New* `*_legacy`/`*_compat`/`*_old` imports are caught automatically by `tests/test_arch_test_hygiene.py` (clean today, no allowlist) — do not hand-detect them. For a `deprecated`/`legacy` docstring shim still found: (a) update import. (b) Rerun; update expected value if still fails. (c) Delete if verifying an alias with no non-test callers. |
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
4. **Trim candidates**: strip weak sub-assertion, keep behavioral core. **Graft-then-delete**: if a test flagged for deletion (rule 5/6) carries one unique assertion the survivor lacks — a stricter post-condition, a propagated value, a distinct side effect — graft that assertion onto the survivor *first*, then delete. Never lose a unique observable just because the test is otherwise redundant. After deleting a test, remove imports/constants/helpers it solely used (lint catches these — fix them, don't blanket `--fix` real findings away).
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

   **Two different counts — never substitute one for the other.** The `def test_` count (what name-absence/diff verifies) is NOT the collected count: parametrized tests (`@pytest.mark.parametrize`, `[2]`/`[3]` ids) expand one `def` into many collected cases. Use the def-count delta to verify *deletions*; use `pytest --collect-only -q | tail -1` for the *headline total*. A suite of 690 `def`s can collect 700+.

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

Tests audited: N def_test functions (every one annotated; no sampling)
Collected total: N before → N after  ← from `pytest --collect-only` (≥ def-count due to parametrization)
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
Criticality stratification (when requested): Critical N | Important N | Low N
  Cut line: <tier> — Low-tier removed: N (candidates: N — [list w/ guarded failure mode])
Tests moved to canonical files: N
Zero-coverage surfaces backfilled: N — [list]
Coverage depth gaps fixed: N — [list]
Coverage gaps remaining: N — [list with waivers]
Separate-cleanup candidates: N — [list]
Graduation candidates (Tier-2 fitness functions): N — [rule + violation count + lands-green or needs-allowlist]
LLM non-determinism events: N — [test + run count]
New/modified tests: N passed, 0 failed
Suite: N passed, 0 failed (or: skipped per policy)
Log: .pytest-logs/<timestamp>-clean-tests.log

Verdict: PASS / FAIL — <one sentence>
```
