---
name: clean-tests
description: Keep the suite focused on functional behavior verification. Purge dead, structural, unit-only, duplicate, low-ROI, backward-compat, and noise tests. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** every test must verify functional behavior of production code under realistic conditions. Delete anything that doesn't.

**Default stance:** violations exist. PASS is earned, not assumed.

**Non-negotiable:** Pass 1 requires reading every test file with the Read tool and annotating every `def test_*` function. Grep patterns are supplementary triage only — they are not a substitute for reading test bodies. A run that skips per-test reading is incomplete regardless of what the summary claims. For every batch, the private-call inventory (step 2 below) must complete before any annotation row is written — skipping it is a protocol violation even if all verdicts would be KEEP.

**Produces:** terminal summary + `.pytest-logs/<timestamp>-clean-tests.log`.

**Verdict:** binary **PASS** / **FAIL**. Any unfixed Blocking, lint error, or red suite → FAIL.

---

## Pass 0 — File-level triage (fast scan before per-test audit)

Run once across all test files before touching individual tests. Identifies whole-file issues so they can be resolved before the per-test pass.

```bash
ls tests/test_*.py
```

### 0a — Broken imports

```bash
uv run pytest --collect-only 2>&1 | grep -E "ERROR collecting"
```

Run this once to get the full list of broken files. If a file fails to import/collect → every test in it is Rule 4 (stale). Mark the whole file `BROKEN`. Do not audit individual tests. Fix the import or delete the file outright if the underlying module is gone.

### 0b — Pervasive bad patterns (grep-level pre-scan)

```bash
grep -l "monkeypatch\|unittest\.mock\|from unittest import mock\|patch\b" tests/test_*.py
grep -l "assert True\b\|assert result\b\|assert result\." tests/test_*.py
grep -l "from.*_legacy\|from.*_compat\|from.*_old\b" tests/test_*.py
```

Use these to pre-tag files as `SUSPECT_MOCK` or `SUSPECT_STRUCTURAL`. These tags guide priority during Pass 1 — they do **not** replace reading the file.

### 0c — Clustered test name scan (Rule 11 trigger)

These clusters are the most common form of same-file near-duplicates (Rule 11):

```bash
grep -h "^def test_" tests/test_*.py | sed 's/(.*$//' | sed 's/^def //' | \
  awk -F_ '{print $1"_"$2"_"$3}' | sort | uniq -c | sort -rn | head -20
```

Flag any 3-part prefix with **count ≥ 3** (e.g., `test_blocks_*`, `test_fires_on_*`, `test_approval_subject_*`). During Pass 1, compare every test in the group side-by-side before assigning verdicts. The most common sub-patterns:

- **Enumerated-input negatives**: `test_blocks_X / _Y / _Z` all assert `is None` / `raises` for different inputs that activate the same branch. Keep the most representative input; delete the rest.
- **Format-template variants**: `test_subject_create / _delete / _append` all verify the same substitution formula with different values. Two examples (a boundary pair) prove the pattern; delete the rest.

### 0d — Merge candidates (file-pair scan)

The grep-per-line approach misses file pairs that share multiple modules. Use the pairwise scan instead:

```python
# Run via: python3 - << 'EOF' ... EOF
import glob, re
files = {}
for f in sorted(glob.glob("tests/test_*.py")):
    mods = set()
    for line in open(f):
        line = line.strip()
        if line.startswith("from co_cli") or line.startswith("import co_cli"):
            m = re.match(r"(?:from|import) (co_cli[.\w]*)", line)
            if m:
                mods.add(m.group(1))
    files[f] = mods
names = list(files)
for i in range(len(names)):
    for j in range(i+1, len(names)):
        shared = files[names[i]] & files[names[j]]
        if len(shared) >= 4:
            print(f"[{len(shared)} shared] {names[i]} <-> {names[j]}")
            for s in sorted(shared): print(f"  {s}")
            print()
```

Flag every pair with **≥4 shared `co_cli` modules** for investigation. For each flagged pair:

1. **Read both files** (do not skip — shared infrastructure imports like `CoDeps` inflate the count; the true signal is which production *functions* are called).
2. A pair is a **merge candidate** when ≥50% of tests in each file call functions from the **same production module**.
3. A pair is **NOT** a merge candidate when:
   - The shared imports are all infrastructure (`CoDeps`, `ShellBackend`, `CoSessionState`, `skills.loader`) and the tests call functions from different production modules.
   - One file is workflow-named and spans >2 distinct `co_cli/` module paths.

Canonical filename rule: `co_cli/<pkg>/<module>.py` → `test_flow_<pkg>_<module>.py`.

Examples:
- `co_cli/skills/curator.py` → `test_flow_skills_curator.py`
- `co_cli/tools/memory/recall.py` → `test_flow_memory_recall.py`
- `co_cli/commands/core.py` → `test_flow_slash_dispatch.py`

Record each confirmed merge candidate. Merges execute in Pass 2.

---

## Pass 1 — Per-test audit (mandatory full read)

### How to execute

Process test files **in batches of 5**. For each batch:

1. **Read every file in the batch using the Read tool.** Do not use grep output as a substitute for file content. If a file is too long to read in one call, read it in offset/limit chunks until you have seen every line.

2. **Private-call inventory — mandatory before any annotation row.**

   Scan the batch for private symbols imported from `co_cli` or accessed as module attributes:

   ```bash
   # Private symbols imported from co_cli modules
   grep -n "from co_cli" <file> | grep " _[a-z]"
   # Private attribute access on co_cli module references
   grep -En "[a-z_]+\._[a-z][a-z_]*\(" <file> | grep -v "#"
   ```

   For each unique private symbol found (e.g. `_repair_json_args`, `trace_view._build_tree`):

   a. Locate its definition: `grep -rn "def _symbol_name" co_cli/`
   b. Find all production callers: `grep -rn "_symbol_name" co_cli/` (production code only, not tests)
   c. Identify which public-facing functions or entry points invoke it.
   d. Check whether any existing test exercises one of those public entry points with realistic inputs that would hit the same failure mode as the private-function test.
   e. Record your conclusion per symbol:
      - `_symbol → reachable via public_fn(); subsumed if test_X covers same branch` — Rule 8 candidate
      - `_symbol → not reachable via public entry for this input class` — keep the private test

   **Do not write any annotation row for a test calling a private co_cli symbol until its inventory entry is recorded.** A KEEP verdict without an inventory entry is a protocol violation.

3. **For every `def test_*` function in each file**, produce an annotation row:

   ```
   file | test_name | production_fn_called | verdict | rule (if violated)
   ```

   Verdict options: `KEEP`, `DELETE (rule N)`, `FIX (rule N)`.

4. Evaluate rules in order (1 → 16). Stop at the first Blocking violation per test — one is enough to act.
5. After annotating all tests in the batch, execute the deletions/fixes for that batch before moving to the next. **Dependency check before each deletion (Rules 8–11):** verify the test you are relying on as the subsuming test is not itself flagged for deletion in this or a prior batch. If it is, keep the original.

**Do not begin Pass 2 until every non-BROKEN test file has a complete annotation row for every test function.**

### Source code reads for subsumption rules (8, 9)

Rules 8-9 cannot be evaluated without reading production source. The private-call inventory in step 2 above is the enforcement mechanism — it happens per-batch before annotation, not as an afterthought. Key criteria:

- **Rule 8 (subsumed private helper)**: private function is called by a public entry point AND an existing integration test exercises that entry point with inputs that reach the same branch. Both conditions required.
- **Rule 9 (subsumed pipeline-internal)**: a test drives a non-terminal step in a pipeline when a terminal integration test already covers the same failure mode. Read the production pipeline to identify the terminal step.

For Rule 9, read the production pipeline to understand which step is "terminal" for the workflow being tested.

### Deletion test (apply before flagging any rule)

> "If this test is deleted, will a real regression go undetected by **any remaining test that is not itself being deleted**?"
> Yes → keep (note why). No → flag for deletion.

### Blocking — default action is DELETE

Fix only when: (a) the test covers a failure mode with no other coverage **and** (b) the fix is a one-line correction (wrong assertion value, stale import path). If the body needs rewriting, delete.

| # | Rule | Default action |
|---|---|---|
| 1 | **Mock/patch** — `monkeypatch`, `unittest.mock`, `pytest-mock`, or hand-rolled fakes replacing production behavior. Input literals (`User(id=1)`) are fine; fake return values (`LLMResponse(content="x")` to skip the LLM) are not. | Delete unless unique coverage with no real-call alternative |
| 2 | **Truthy-only assertion** — `assert True`, `assert result`, `assert result.flag` | Delete |
| 3 | **No behavioral assertion** — body drives code but asserts nothing meaningful | Delete |
| 4 | **Stale call signature** — calls a function with args/kwargs that don't exist in current source | Fix (update call) or delete |
| 5 | **Tautological assertion** — `assert x == x` or always-true by Python semantics | Delete |
| 6 | **Structural-only** — asserts dataclass defaults, field existence, module importability, type annotations; would pass if function body were `pass` | Delete |
| 7 | **Schema-only** — asserts shape (`len(result) == 6`) without checking values are functionally correct | Delete |
| 8 | **Subsumed private helper** — calls `_private()` while a public-entry test already exercises that helper via realistic inputs that hit the same failure mode. **Requires reading the production source.** | Delete |
| 9 | **Subsumed pipeline-internal** — drives a non-terminal step when a terminal test already covers the same failure mode with realistic inputs. Keep only when the unit-only input can't be reached from the terminal (specific malformed JSON, exception-only branches). **Requires reading the production pipeline.** | Delete otherwise |
| 10 | **Subsumed content gate** — asserts a narrow property (substring, field absent, flag value) that a broader behavioral test in the same file already asserts on the same production function. To confirm: grep the same file for that assertion string. If the broader test would also fail if the property changed, delete the narrower one. If they cover different code paths or the broader test would still pass, keep. | Delete |
| 11 | **Same-file near-duplicate** — two tests in the same file, same function, same branches activated, same observable asserted. **Read both test bodies before deciding.** Common sub-patterns: (a) enumerated-input negatives — `test_blocks_X / _Y / _Z` each pass a different input that hits the same `return None` branch; keep the most representative, delete the rest; (b) format-template variants — tests that verify the same string formula with different substitution values; two examples (a boundary pair) prove the pattern, delete the rest. | Delete the weaker one |
| 12 | **Backward-compat shim** — imports from `*_legacy`/`*_compat`/`*_old`, or docstring mentions `old behavior`/`compat`/`deprecated`/`legacy`. | (a) Update import to canonical. (b) Rerun; update expected value if failure mode still exists. (c) Delete if verifying an alias with no non-test callers. Flag alias as separate-cleanup candidate. |
| 13 | **Unguarded filesystem write** — writes to `~/.co-cli/`, `USER_DIR`, or `CO_HOME` without `tmp_path` | Fix: route paths through `tmp_path`; set `CO_HOME=tmp_path` if production reads it at call time |
| 14 | **Unrestored env mutation** — sets/deletes `os.environ` without `try/finally` restoring original | Fix |
| 15 | **`ensure_ollama_warm` inside `asyncio.timeout`** — must precede all timeout blocks | Move outside |
| 16 | **Multiple LLM awaits in one `asyncio.timeout`** — each LLM/external await needs its own timeout | Split |

**Rule-failed files:** if ≥50% of a file's tests are flagged Blocking and no unique coverage survives after deletions, delete the entire file.

### Minor — record, fix if trivial

- Naming: test function name missing `test_flow_` prefix.
- Missing `asyncio.timeout` on a real-LLM `await`.
- Test name describes arrangement not behavior (`test_disk_scan_fallback` → `test_search_returns_empty_when_store_missing`).
- **Degenerate LLM test**: assertion passes regardless of model behavior (`result.outcome == "continue"`, or body comment says "if model defied prompt, accept it"). List; do not auto-delete.
- **Shared mutable module-level state** that changes during a test without restoration. Exempt: `SETTINGS`, `SETTINGS_NO_MCP`, cached LLM models.
- **Trim candidate**: behavioral test carrying a weaker sub-assertion (Rule 6, 7, or 10 territory) inside an otherwise strong test. Note for in-place trim in Pass 2.

---

## Pass 2 — Structural cleanup

Perform the file-level actions identified in Pass 0 that could not be done per-batch:

1. **Fix BROKEN files** (Pass 0a): update import or delete file.
2. **Merge duplicate files** (Pass 0d):
   - Copy unique tests from the non-canonical file into the canonical file.
   - Delete the non-canonical file.
   - If the canonical file doesn't exist yet, rename the more complete file to the canonical name, then merge the other's unique tests in and delete it.
3. **Delete rule-failed files** (≥50% Blocking, no unique coverage).
4. **Trim candidates**: strip the weak sub-assertion, keep the behavioral core.
5. If any deletion leaves a production helper with zero callers, flag it in the summary — do not delete production code.

---

## Pass 3 — Verify

1. Coverage sanity check: for each public `@agent_tool`, slash command, and `co_cli/agent/` public surface, confirm at least one behavioral test survives.

   ```bash
   grep -rn -E '^\s*@agent_tool' co_cli/
   ```

   Read `co_cli/commands/core.py` `BUILTIN_COMMANDS` for the command list. If a deletion stripped the only coverage of a user-facing entry, restore or write a replacement (waiver allowed with rationale).

2. Run lint — catches orphaned imports left by deletions:

   ```bash
   scripts/quality-gate.sh lint
   ```

   Fix every issue. No `# noqa` without an explanatory comment.

3. Run the suite:

   ```bash
   mkdir -p .pytest-logs
   uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests.log
   ```

   **Suite failure protocol:**
   - Real-LLM test fails → rerun in isolation. Passes alone → LLM non-determinism; note in summary and restart suite without `-x`. Fails alone consistently (≥2 solo runs) → real failure; diagnose.
   - Non-LLM test fails → diagnose root cause, fix, re-run with `-x`. Never report PASS until suite is green.

---

## Verdict + terminal summary

- **PASS**: all Blocking resolved + lint clean + suite green.
- **FAIL**: any unfixed Blocking, lint error, or red suite.

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
Minor findings: N (noted/deferred)
Degenerate LLM tests: N — [list]
Trim candidates resolved: N
Tests moved to canonical files: N
Coverage gaps after deletion: N — [list with waivers]
Separate-cleanup candidates: N — [list]
LLM non-determinism events: N — [test + run count]
Suite: N passed, 0 failed
Log: .pytest-logs/<timestamp>-clean-tests.log

Verdict: PASS / FAIL — <one sentence>
```
