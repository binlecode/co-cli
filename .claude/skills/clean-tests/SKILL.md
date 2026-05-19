---
name: clean-tests
description: Keep the suite focused on functional behavior verification. Purge dead, structural, unit-only, duplicate, low-ROI, backward-compat, and noise tests. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** every test must verify functional behavior of production code under realistic conditions. Delete anything that doesn't.

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
```

Pre-tag files `SUSPECT_MOCK` or `SUSPECT_STRUCTURAL`. Does not replace reading.

### 0c — Clustered test names (Rule 11 trigger)

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

4. Evaluate rules 1→16 in order. Stop at first Blocking violation per test.

5. Execute deletions/fixes for the batch before proceeding. **Dependency check:** confirm the subsuming test is not itself flagged for deletion.

### Deletion test (apply before flagging any rule)

> "If deleted, will a real regression go undetected by any remaining non-deleted test?"  
> Yes → keep (note why). No → flag.

### Blocking — default DELETE

Fix only when: (a) unique coverage AND (b) the fix is rerouting through a public entry point (Rule 8) or a one-line value/import correction (Rules 4, 12). Otherwise delete.

| # | Rule | Action |
|---|------|--------|
| 1 | **Mock/patch** — `monkeypatch`, `unittest.mock`, hand-rolled fakes replacing production behavior. Input literals are fine; fake return values are not. | Delete |
| 2 | **Truthy-only** — `assert True`, `assert result`, `assert result.flag` | Delete |
| 3 | **No assertion** — drives code but asserts nothing meaningful | Delete |
| 4 | **Stale signature** — calls function with args that don't exist in current source | Fix or delete |
| 5 | **Tautological** — `assert x == x` or always-true by Python semantics | Delete |
| 6 | **Structural-only** — asserts dataclass defaults, field existence, importability; passes if body is `pass` | Delete |
| 7 | **Schema-only** — asserts shape (`len(result) == 6`) without verifying values are correct | Delete |
| 8 | **Subsumed private helper** — calls `_private()` while a public-entry test already covers the same branch. **Requires reading production source.** FIX by rerouting: replace the private call with the public entry call and adjust assertions to the public return type. Delete if no unique observable survives the reroute. | Fix or delete |
| 9 | **Subsumed pipeline-internal** — drives a non-terminal pipeline step when a terminal test already covers the same failure mode. Keep only when the specific input can't be reached from the terminal. **Requires reading the pipeline.** | Delete otherwise |
| 10 | **Subsumed content gate** — asserts a narrow property that a broader test in the same file already asserts on the same function. Confirm: if the broader test would also fail when that property changes, delete the narrower one. | Delete |
| 11 | **Same-file near-duplicate** — same function, same branches activated, same observable. Read both bodies. | Delete weaker |
| 12 | **Backward-compat shim** — imports from `*_legacy`/`*_compat`/`*_old`; docstring mentions `deprecated`/`legacy`. | (a) Update import. (b) Rerun; update expected value if failure still exists. (c) Delete if verifying an alias with no non-test callers. |
| 13 | **Unguarded filesystem write** — writes to `~/.co-cli/`, `USER_DIR`, `CO_HOME` without `tmp_path` | Fix: route through `tmp_path`; set `CO_HOME=tmp_path` |
| 14 | **Unrestored env mutation** — sets/deletes `os.environ` without `try/finally` | Fix |
| 15 | **`ensure_ollama_warm` inside `asyncio.timeout`** | Move outside |
| 16 | **Multiple LLM awaits in one `asyncio.timeout`** | Split |

**Rule-failed file:** ≥50% Blocking with no unique coverage surviving → delete the file.

### Minor — record, fix if trivial

- Test name describes arrangement not behavior.
- Missing `asyncio.timeout` on a real-LLM `await`.
- **Degenerate LLM test**: assertion passes regardless of model output. List; do not auto-delete.
- **Shared mutable module-level state** changed without restoration. Exempt: `SETTINGS`, `SETTINGS_NO_MCP`, cached models.
- **Trim candidate**: weak sub-assertion inside an otherwise strong test. Note for Pass 2.

---

## Pass 2 — Structural cleanup

1. **Fix BROKEN files** (Pass 0a): update import or delete.
2. **Merge duplicate files** (Pass 0d): copy unique tests to canonical file; delete non-canonical. If canonical doesn't exist, rename the more complete file then merge.
3. **Delete rule-failed files**.
4. **Trim candidates**: strip weak sub-assertion, keep behavioral core.
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

   A failure here is a test fixture bug — diagnose and fix before issuing verdict.

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
  Mock/patch (rule 1):                  N
  Vacuous/tautological (rules 2, 3, 5): N
  Structural/schema (rules 6-7):        N
  Subsumed (rules 8-10):                N
  Same-file dup (rule 11):              N
  Backward-compat delete (rule 12c):    N
Tests fixed: N total
  Stale signature (rule 4):             N
  Backward-compat update (rule 12a/b):  N
  Isolation (rules 13-14):             N
  Async discipline (rules 15-16):       N
  Private-call rerouted (rule 8 fix):   N
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
