# REPORT-test-hygiene-20260502-091911

## Meta
- Scan path: tests/test_flow_compaction_proactive.py
- Files: 1
- Started: 2026-05-02T09:19:11
- Status: DONE

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] file list enumerated (1 file)
- [x] conftest.py read — docstring-only, no plumbing violations
- [x] _settings.py read — SETTINGS, SETTINGS_NO_MCP, make_settings() correctly defined
- [x] _timeouts.py read — LLM_COMPACTION_SUMMARY_TIMEOUT_SECS = 60
- [x] _ollama.py read — ensure_ollama_warm must be called before asyncio.timeout (confirmed)

## Phase 2 — File Read Progress
- [!] tests/test_flow_compaction_proactive.py — 1 blocking violation, 1 minor violation

## Phase 3 — Audit Findings

| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| test_flow_compaction_proactive.py | 103, 141, 250 | `build_model` called inside test function body — must cache at module scope | Blocking | OPEN |
| test_flow_compaction_proactive.py | 85, 163, 259 | `RunContext(model=None)` — `RunContext.model` typed as `Model`, not `Model | None` | Minor | OPEN |

## Phase 4 — Adversarial Review

### Finding 1 (Blocking): build_model inside test body
- Tests 2, 3, 9 all call `settings = _tight_settings(); model = build_model(settings.llm)` inside the test function.
- Rule: "Cache module-level models at module scope rather than rebuilding per call."
- Challenge: `_tight_settings()` returns a different `llm.num_ctx` (200 vs default). Is a different module-level cached model needed?
  - `build_model` uses `llm.model`, `llm.host`, `llm.reasoning_model_settings()`, `llm.noreason_model_settings()`, `llm.reasoning_context_window()`. `num_ctx` feeds into these methods. Yes — a separate `_TIGHT_MODEL` at module scope is needed.
  - For test 3 (anti-thrash gate), the model is never called (gate prevents LLM); any truthy model suffices. Using `_TIGHT_MODEL` is consistent.
- **Confirmed violation**: fix by adding `_TIGHT_MODEL = build_model(_tight_settings().llm)` at module scope.

### Finding 2 (Minor): RunContext(model=None) type mismatch
- `RunContext.model` is typed `Model` (non-Optional) in pydantic_ai's dataclass.
- `model=None` is passed in all 9 tests.
- Production code in `compaction.py` never accesses `ctx.model` — uses `ctx.deps.model` instead. No behavioral risk.
- Challenge: is there any path that would dereference ctx.model? grep confirms zero references to `ctx.model` in compaction.py.
- **Confirmed minor**: no behavioral impact but violates pydantic_ai's type contract. Downgrade from blocking.

## Phase 5 — Fixes Applied

| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| test_flow_compaction_proactive.py | tests 2, 3, 9 | build_model at module scope | Added `_TIGHT_MODEL = build_model(_tight_settings().llm)` at module scope (line 70); removed inline `settings = _tight_settings(); model = build_model(settings.llm)` from all three test bodies | DONE |
| test_flow_compaction_proactive.py | all 9 tests | RunContext(model=None) | Replaced `model=None` with `_LLM_MODEL.model` or `_TIGHT_MODEL.model` in all RunContext constructions | DONE |

## Phase 6 — Test Run
- Command: `uv run pytest -x -v tests/test_flow_compaction_proactive.py`
- Log: `.pytest-logs/20260502-091911-test-hygiene.log`
- Result: 28 passed, 0 failed (11.02s)

## Phase 7 — Final Verdict
CLEAN — 2 violations fixed (1 blocking: build_model inside test bodies; 1 minor: RunContext(model=None) type mismatch). All 28 tests pass. No bloat, no duplicates, no structural tests, no mocks. Every test defends a real failure mode.
