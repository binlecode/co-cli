---
name: clean-tests
description: Keep the suite focused on functional logic verification and aggressive issue finding. Purge dead/structural tests, dedup overlaps, trim subsumed units, audit workflow coverage, consolidate by workflow. Active test quality gate.
---

# clean-tests

**Invocation:** `/clean-tests [path]`

**Mission:** cleanse, trim, and consolidate tests for concise functional-only coverage. Six phases, in order:

0. Load rules + code-scan entry points
1. Build test catalog
2. Rule audit + overlap detection
3. Coverage scan
4. File consolidation plan
5. Auto-fix + suite

**Default stance: violations exist. PASS is earned, not assumed.**

**Consumes:** every `test_*.py` under `[path]` (default: `tests/`), `agent_docs/testing.md`, `tests/_*.py` support files.  
**Produces:** terminal summary + `.pytest-logs/<timestamp>-clean-tests.log`. No REPORT-*.md.  
**Verdict:** binary **PASS** / **FAIL**. Any unfixed Blocking violation or red suite → FAIL.

---

## Phase 0 — Load Rules + Code-Scan Entry Points

Read `agent_docs/testing.md` in full. Keep it loaded for all phases.

Scan the codebase for entry points:

1. **Agent tools**: `grep -rn '^@agent_tool' co_cli/` — extract file path and function name from each match.
2. **Slash commands**: read `co_cli/commands/core.py` — extract every key in the `BUILTIN_COMMANDS` dict.
3. **Agent public surfaces**: read every `.py` in `co_cli/agents/` — collect public functions and classes (names not starting with `_`): `build_orchestrator`, `run_session_review`, `run_curator`, `MCPToolsetEntry`, etc.

Build a unified entry-point list: `{id, kind: tool|command|agent, module_path, function_name, tier}`.

**Tier classification** — assign algorithmically, not from a hardcoded list:

For each `@agent_tool` function, inspect its decorator arguments and the `ToolInfo` it registers:
- **Tier 1**: `requires` field is absent or resolves to a production setting satisfied by a default install (no API keys, no optional external services). These are tools a user exercises in every session without any extra configuration.
- **Tier 2**: `requires` field references an optional production setting (e.g. `obsidian_enabled`, MCP config, delegation profile). Present only when the user opts in — no credentials required.
- **Tier 3**: `requires` field references a credential-bearing production setting (OAuth token, external API key). Always skipped without explicit setup.

If the `requires` field is absent or ambiguous, read the tool's implementation for capability guards (e.g. `if not config.mcp_enabled: return`). A guarded tool is Tier 2; an unguarded tool is Tier 1.

For slash commands: read the handler source for capability guards. Tier 1 if no guard; Tier 2 if guarded by an optional subsystem; Tier 3 if credential-gated.

Announce total entry-point count, tier breakdown, and scan path.

---

## Phase 1 — Test Catalog

Read the foundational support files before opening test files:
- `tests/_settings.py` — `SETTINGS`, `SETTINGS_NO_MCP`, `make_settings()`
- `tests/_timeouts.py` — timeout constants
- `tests/_ollama.py` — `ensure_ollama_warm` contract (must run before any `asyncio.timeout` block)
- `tests/_co_harness.py` — pytest plugin

Read every `test_*.py` in full (no excerpts, no Explore subagent). For each test function record:

```yaml
- test: <file_path>::<function_name>
  prod_imports: [co_cli modules imported]
  entry_called: [entry-point function(s) the test exercises]
  assertion_kind: behavioral | structural | content_gate | schema_check
  gates_on: [return_value | persisted_state | raised_error | side_effect | string_content]
  pipeline_position: terminal | internal | standalone
  uses_real_llm: true | false
  fixtures: [pytest fixtures]
  legacy_signals: [import_path_mismatch | assertion_stale | compat_alias_only]  # optional; omit if none
```

**Field definitions:**

`assertion_kind` — classify the test's most demanding assertion. Use the highest-fidelity category that applies:
- `behavioral`: asserts on an observable outcome that can only pass if the production code does the right thing — return value correctness, state change, error raised at the right condition, side effect executed. A test with any behavioral assertion is `behavioral` regardless of other weaker assertions also present.
- `structural`: asserts only on Python or import-system facts — dataclass defaults, field existence, module importability, class attribute presence, type annotations, registration table membership. Applies only when there is NO behavioral assertion.
- `content_gate`: asserts that specific string content exists in a file or function output (e.g. "section heading in assembled prompt", "lint finds no TODO in skill file") — tests the content of an asset. Applies when the string check is the only meaningful assertion and no behavioral outcome is verified.
- `schema_check`: asserts only on the shape/structure of a data object (field names, count of items, ordering) without checking that the values are functionally correct. Applies when there is no behavioral or content assertion.

`gates_on` — what observable the assertion is sensitive to:
- `return_value`: the function's return value must equal or contain something specific.
- `persisted_state`: a side effect (file write, DB row, env var) must have happened.
- `raised_error`: a specific exception must be raised under a specific condition.
- `side_effect`: an external observable (span emitted, callback called, process spawned) must occur.
- `string_content`: a specific substring must appear in a string output.

`pipeline_position` — where does `entry_called` sit in production call chains?
- `terminal`: the function is a public entry point that orchestrates a complete user-facing operation (e.g. `run_turn`, `knowledge_search`, `dispatch`). Or it is a pure utility with no callers in the production pipeline.
- `internal`: the function is called by another production function that is itself tested. The function is a non-terminal step in a pipeline (e.g. `enforce_request_size` inside the processor chain, `_gather_session_todos` inside compaction, `_repair_json_args` inside `before_tool_validate`).
- `standalone`: helper that is NOT reachable from any terminal function that has a test. Before assigning `standalone`, verify by reading the function's callers: confirm no tested terminal entry reaches it (e.g. bootstrap startup functions called once during `run_bootstrap`, OTEL span emitters called only from bootstrap). If the function IS reachable from a tested terminal, classify as `internal` instead.

`legacy_signals` — optional; populate only when one or more of these hold:
- `import_path_mismatch`: the imported symbol or module path does not match the current canonical location in source. Verify: `grep -rn "<symbol>" co_cli/ --include="*.py"` — if the symbol appears only in an `__init__.py` re-export and not in any implementation file, the test is hitting a compat shim.
- `assertion_stale`: the assertion value does not match what the current implementation produces. Signals: test comment references an issue, PR, or "old behavior"; running the test in isolation shows actual ≠ expected.
- `compat_alias_only`: test exists solely to verify a compat layer (e.g. `__all__` export kept only for old callers). Verify: `grep -rn "<symbol>" . --include="*.py" | grep -v test_` — if no non-test callers, the alias is dead.

`entry_called` is derived from `prod_imports` and call sites in the test body.

---

## Phase 2 — Rule Audit + Overlap Detection

Apply every rule from `agent_docs/testing.md` AND the rules below to each test. Classify violations:

- **Blocking**: must fix or delete before PASS.
- **Minor**: fix inline where trivial; note otherwise.

For each classified violation, apply the deletion test before recording it: **"If this test is deleted, will a real regression go undetected by any remaining test?"** If yes — the test is valuable, drop the finding. If no — the finding stands. Also check: is the violation covered by a conftest fixture? Is the "duplicate" testing a meaningfully different failure mode? Only findings that survive the deletion test are recorded.

---

### R-Series: Standard Rules (from testing.md)

- **Blocking**: mock/patch usage (`monkeypatch`, `unittest.mock`, `pytest-mock`, hand-assembled domain objects that substitute for production code paths rather than serving as realistic inputs).
- **Blocking**: `assert True` or truthy-only assertion (`assert result`, `assert result.flag`).
- **Blocking**: no behavioral assertion — test body drives code but asserts nothing meaningful.
- **Blocking**: stale call signature — test calls a function with arguments that don't exist in current source.
- **Blocking**: fixture not wired — `tmp_path` or injected fixture in signature but never passed to a production function.
- **Blocking** *(not in testing.md — enforced here)*: `ensure_ollama_warm` called inside an `asyncio.timeout` block — it must precede all timeout blocks; move it outside.
- **Blocking** *(not in testing.md — enforced here)*: multiple sequential LLM `await`s wrapped in a single `asyncio.timeout` — each individual `await` to an LLM or external service must have its own `asyncio.timeout(N)`, never grouped.
- **Minor**: naming convention mismatch (`test_flow_` prefix missing).
- **Minor**: `asyncio.timeout` missing on an `await` that calls a real LLM or external service.
- **Minor**: test name describes arrangement rather than behavior — name should state what the code does under what condition, not what the file or function is called (e.g. `test_search_returns_empty_when_store_missing` not `test_disk_scan_fallback`).

---

### S-Series: Structural / Schema Tests

**S1 — Structural assertion** (Blocking): `assertion_kind == structural`. A test whose assertion would still pass if the production function body were replaced with `pass` or `return None`. Examples: testing a dataclass default value, testing that a field is assignable, testing module importability. Delete unless the test can be rewritten to assert on a behavioral outcome.

**S2 — Schema check** (Blocking): `assertion_kind == schema_check` where the schema check is the *only* assertion and no behavioral outcome is verified. Example: asserting `len(result) == 6` without asserting what those 6 items are. Delete or strengthen to `behavioral`.

---

### U-Series: Unit Test Hygiene

**U1 — Subsumed private function** (Blocking): test calls a `_private()` function while a public entry covering that helper already has a test with the same or broader failure modes.

Algorithm:
1. Find tests where `entry_called` contains a name starting with `_`.
2. For each such test `U` calling helper `_h`, find public-entry tests `W` from the same module.
3. Read `W`'s entry point source body once — scan for literal `_h(` (direct call only). If absent, skip.
4. If `_h(` is present AND `U`'s `gates_on` set is a subset of `W`'s `gates_on` set — flag `U` for deletion.

**U2 — Pipeline-internal unit test** (Blocking): `pipeline_position == internal` AND a `terminal` test for the same pipeline exists AND the failure mode is reachable through that terminal test with realistic inputs.

Algorithm:
1. Find tests where `pipeline_position == internal`.
2. For each such test `I` calling internal function `f`, find terminal-entry tests `T` from the same module or the pipeline's orchestrating module.
3. Verify `T` exercises `f` by reading `T`'s entry-point source and confirming `f(` (or a caller of `f`) appears in the reachable call chain. If absent, skip — `I` is the only guard; do not flag.
4. If `f` IS reachable from `T`: determine whether `I`'s specific failure modes can be triggered through `T` using production-realistic inputs.
   - **Flag for deletion (Blocking)**: the failure mode is reachable through `T` with realistic inputs. Example: `enforce_request_size` called from the processor chain — oversized results from real tool calls can be passed through the chain.
   - **Do not flag**: the failure mode requires a synthetic boundary input that cannot be deterministically produced through `T`. Examples: `_repair_json_args` tests with specific malformed JSON patterns the LLM produces but a behavioral test can't trigger reliably; `_check_ollama_num_ctx_floor` raising ValueError on a misconfigured model unavailable in CI.

---

### B-Series: Backward-Compatibility Tests

Detect using the `legacy_signals` field populated in Phase 1. A test with no `legacy_signals` entries is not a B-series candidate.

**B1 — Legacy symbol import** (Blocking): `legacy_signals` contains `import_path_mismatch`. Fix: update the import to the canonical path, then verify the test still exercises the same behavior. If the canonical path no longer exists, delete the test.

**B2 — Legacy behavior assertion** (Blocking): `legacy_signals` contains `assertion_stale`. Fix: if the test covers a real failure mode (the production logic can still fail in the way the test expects), update the expected value to match current correct behavior. If the failure mode no longer exists, delete the test.

**B3 — Dead compat alias test** (Blocking): `legacy_signals` contains `compat_alias_only`. Delete the test and verify the compat layer itself has no non-test callers before removing it.

---

### L-Series: Low-ROI Tests

**L1 — Content gate with higher-fidelity neighbor** (Blocking): `assertion_kind == content_gate` AND a behavioral test anywhere in the suite already covers the same production code path with broader assertions. The content gate adds no additional failure mode. Delete.

Signal: the content gate asserts `"string" in output` while a behavioral test — in any file — asserts on the return value, persisted state, or error behavior of the same function with the same or broader inputs. The behavioral test would catch any code regression that would also break the content gate. The neighbor does not need to be in the same file; cross-file behavioral coverage counts.

---

### N-Series: Noise Tests

**N1 — Tautological assertion** (Blocking): the test's assertion passes regardless of what the production code returns, because its truth value is determined solely by Python semantics with no reference to production output. Specific case: `assert x == x` or any expression that is true by construction. (Truthy-only assertions are covered by R-series; default-value assertions by S1; degenerate LLM assertions by N3. N1 applies only to mathematically tautological assertions.)

**N2 — Dead test** (Blocking): the test imports a symbol that no longer exists, or calls a function with a signature that no longer matches current source. Would fail at import or collection time. Fix the stale call signature or delete.

**N3 — Trivially-passing LLM test** (Minor): an LLM-backed test whose assertion passes whether or not the model followed the prompt (e.g. the test body comments "if the model defied the prompt… accept it", or asserts only `result.outcome == "continue"` which is always true). The test provides no regression signal. Record in terminal summary under "Degenerate LLM tests"; do not auto-delete (the test structure may be salvageable by tightening the assertion).

---

### I-Series: Isolation Violations

**I1 — Unguarded filesystem write** (Blocking): the test writes to a path not derived from `tmp_path` (e.g. hardcodes `~/.co-cli/`, uses `USER_DIR` directly, or constructs a path from `CO_HOME` without overriding it via `tmp_path`). These writes bleed state across test runs and make the suite non-deterministic. Fix: derive all paths from `tmp_path`; set `CO_HOME` to `tmp_path` if the production code reads it at call time.

**I2 — Unrestored os.environ mutation** (Blocking): the test sets or deletes `os.environ` keys without a `try/finally` that restores the original value (or `None` if the key was absent). A test failure mid-run leaves stale env vars that silently corrupt subsequent tests. Fix: snapshot before mutation, restore in `finally`.

**I3 — Shared mutable module-level state** (Minor): detection requires three steps: (1) identify module-level objects in the test file, excluding `SETTINGS`, `SETTINGS_NO_MCP`, and cached LLM models (explicitly allowed by testing.md); (2) list each object's public mutation methods by reading its class definition; (3) check whether any test body calls a mutation method without a corresponding reset in teardown (no `yield` fixture or `finally` block that undoes the mutation). Flag only objects where step 3 confirms unrestored mutation.

---

### O-Series: Overlap Detection

**O1 — Cross-file overlap** (Blocking): two tests in different files share at least one entry in `entry_called` AND assert the same behavioral outcome (same `gates_on` observable on the same code path). Keep the one in the canonical file (or the more thorough one); delete the copy.

Algorithm:
1. For each pair of catalog records `(A, B)` where `A.file != B.file`: compute `entry_called(A) ∩ entry_called(B)`. If the intersection is non-empty, the pair is a candidate.
2. For each candidate pair: read the shared function's source and identify which branches each test's inputs activate. Determine whether both tests assert the same `gates_on` observable on the same branches, with no distinct failure mode exercised by one but not the other.
3. If yes: flag the weaker test (fewer failure modes, less specific assertion, non-canonical file) for deletion. If both are equally thorough, delete from the non-canonical file.

**O2 — Same-file near-duplicate** (Blocking): two tests in the same file call the same function with different inputs but activate the same code branches and assert the same observable. Read the production function source, identify which branches each test's inputs activate — if the branch sets are identical and `assertion_kind` is the same, flag the weaker one for deletion.

---

## Phase 3 — Coverage Scan

For each entry point from Phase 0, check if ≥1 catalog entry has it in `entry_called` AND `assertion_kind == behavioral`.

**Classification:**
- **Covered**: ≥1 behavioral test exercises this entry point.
- **Uncovered**: no test, or only structural/content_gate/schema_check tests, or only truthy assertions.

**Tiered gating:**

| Tier | Uncovered → | Action |
|------|-------------|--------|
| 1    | **Blocking** | Promoted to Blocking finding. Waiver requires an explicit note explaining why the entry point cannot be tested without infrastructure unavailable in CI. |
| 2    | Terminal output only | List in summary; does not gate verdict. |
| 3    | Terminal output only | List in summary with "(credential-gated)" annotation. |

Print a coverage table grouped by tier and kind.

---

## Phase 4 — File Consolidation Plan

Derive the canonical filename for each test from its entry point's `co_cli/` path:
- Rule: take the last 1–2 path segments of `co_cli/<pkg>/<module>.py` → `test_flow_<pkg>_<module>.py`
- Examples:
  - `co_cli/tools/memory/recall.py` → `test_flow_memory_recall.py`
  - `co_cli/commands/core.py` → `test_flow_slash_dispatch.py`
  - `co_cli/agents/session_review.py` → `test_flow_session_review.py`
  - `co_cli/tools/web/fetch.py` → `test_flow_web_fetch.py`

**Multi-module exemption**: if a test file's `entry_called` entries resolve to more than two distinct `co_cli/` module paths, the file may be named after the workflow rather than any single module — do not flag for consolidation. If entries resolve to two or fewer distinct module paths, the exemption does not apply.

Flag tests living in a different file than their canonical file (and not covered by the exemption). Plan the file moves. No execution yet.

---

## Phase 5 — Auto-Fix + Suite

Apply fixes in sequence:

**A.** Delete subsumed tests (U1, U2-Blocking findings).  
**B.** Delete structural/schema tests (S1, S2 findings).  
**C.** Delete content gates with higher-fidelity neighbors (L1 findings).  
**D.** Delete same-file near-duplicates and cross-file overlaps (O1, O2 findings — keep canonical, delete copy).  
**E.** Delete tautological assertions (N1 findings).  
**F.** Fix or delete backward-compat tests (B1: update import or delete; B2: update expected value if failure mode still exists, else delete; B3: delete test and verify compat layer has no remaining non-test callers).  

**G — Post-deletion coverage check**: after steps A–F, re-run Phase 3 over the surviving catalog (in memory — no re-read needed). Any Tier-1 entry point newly uncovered by the deletions is a new Blocking finding. Either restore the deleted test, write a replacement, or record a waiver before proceeding to step H.

**H.** Fix remaining Blocking violations (R-series, I-series, N2 findings). Fix principle: minimal correct fix — rewrite to drive a real code path and assert on observable outcome; delete if no real failure mode exists. No mocks under any circumstances. Escalate if fixing requires a public API change or architectural restructuring.  
**I.** Move tests to canonical files (Phase 4 plan — cut/paste + update imports; delete empty source files).  
**J.** Post-fix sweep: for each deleted symbol, `grep -rn "<symbol>" . --include="*.py"` — remove orphaned imports and stale references.  
**K.** Run lint before the suite:

```bash
scripts/quality-gate.sh lint
```

If lint fails, fix all reported issues before proceeding. Do not suppress with `# noqa` without an explanatory comment.

**L.** Run the full suite:

```bash
mkdir -p .pytest-logs
uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-clean-tests.log
```

**Suite failure protocol**: if a test fails:
1. Check whether the failing test has `uses_real_llm: true`.
2. If yes: rerun that test in isolation (`uv run pytest <file>::<test> -v`). If it passes alone, the failure is LLM non-determinism — note it in the terminal summary and continue the full suite run without `-x`. If it fails alone consistently (≥2 solo runs), treat as a real failure and diagnose.
3. If no (non-LLM test fails): diagnose root cause immediately. Fix and re-run with `-x`. Never report PASS until the full suite is green.

---

## Verdict + Terminal Summary

- **PASS**: all Blocking findings fixed + lint clean + suite green.
- **FAIL**: any Blocking finding unfixed OR lint errors OR suite not green.

```
## clean-tests — <date>

Files scanned: N
Entry points scanned: N (Tier-1: A covered / B total, Tier-2: C/D, Tier-3: E/F)
Blocking findings fixed: N
  R-series (rules): N
  S-series (structural): N
  U-series (unit hygiene): N
  B-series (backward-compat): N
  L-series (low-ROI): N
  N-series (noise): N
  I-series (isolation): N
  O-series (overlap): N
Minor findings: N fixed inline / M deferred
Degenerate LLM tests (N3): N flagged — [list test names]
Tests deleted: N total (subsumed: A, structural: B, content-gate: C, backward-compat: D, noise: E, overlap: F)
Files consolidated: M
Tier-1 waivers: N (list each with rationale)
LLM non-determinism events: N (list test + run count)
Suite: N passed, 0 failed
Log: .pytest-logs/<timestamp>-clean-tests.log

Verdict: PASS / FAIL — <one sentence>
```
