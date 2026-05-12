# REPORT-test-hygiene-20260508-140000

## Meta
- Scan path: tests/
- Files: 38
- Started: 2026-05-08T14:00:00
- Status: DONE

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] file list enumerated (38 files)
- [x] conftest.py read — docstring-only, neutral
- [x] _settings.py read — SETTINGS, SETTINGS_NO_MCP, make_settings() understood
- [x] _timeouts.py read — LLM_NON_REASONING(10s), LLM_COMPACTION_SUMMARY(60s), LLM_REASONING(30s), LLM_TOOL_CONTEXT(50s), HTTP_HEALTH(15s), FILE_DB(30s)
- [x] _ollama.py read — ensure_ollama_warm must be called OUTSIDE asyncio.timeout blocks

## Phase 2 — File Read Progress
- [x] tests/test_flow_agent_delegation.py — CLEAN
- [x] tests/test_flow_approval_subject.py — CLEAN
- [x] tests/test_flow_artifacts_waterfall_cap.py — CLEAN (make_settings surgical override for FTS5/no-embed config)
- [x] tests/test_flow_background_tasks.py — CLEAN
- [x] tests/test_flow_bootstrap_budget_span.py — CLEAN
- [x] tests/test_flow_bootstrap_canon.py — CLEAN
- [x] tests/test_flow_bootstrap_ollama_num_ctx.py — CLEAN
- [x] tests/test_flow_bootstrap_session.py — CLEAN
- [x] tests/test_flow_canon_recall.py — CLEAN
- [!] tests/test_flow_capability_checks.py — dead module-level vars _TOOL_REG and _AGENT never used
- [x] tests/test_flow_compaction_boundaries.py — CLEAN
- [x] tests/test_flow_compaction_proactive.py — CLEAN (ensure_ollama_warm outside asyncio.timeout ✓)
- [x] tests/test_flow_compaction_processor_chain.py — CLEAN
- [x] tests/test_flow_compaction_recovery.py — CLEAN
- [x] tests/test_flow_compaction_summarization.py — CLEAN
- [x] tests/test_flow_delegation_discovery.py — CLEAN
- [!] tests/test_flow_enforce_request_size.py — test_otel_span_emitted:252 uses monkeypatch (BLOCKING)
- [x] tests/test_flow_history_processors.py — CLEAN
- [x] tests/test_flow_http_error_classifier.py — CLEAN
- [x] tests/test_flow_length_retry.py — CLEAN (constrained_settings to trigger length truncation is justified)
- [x] tests/test_flow_llm_call.py — CLEAN
- [x] tests/test_flow_llm_settings.py — CLEAN
- [x] tests/test_flow_mcp_spill.py — CLEAN
- [x] tests/test_flow_memory_lifecycle.py — CLEAN
- [x] tests/test_flow_memory_recall.py — CLEAN
- [x] tests/test_flow_memory_search.py — CLEAN
- [x] tests/test_flow_memory_store_nochunk.py — CLEAN
- [x] tests/test_flow_memory_write.py — CLEAN
- [x] tests/test_flow_observability_redaction.py — CLEAN
- [x] tests/test_flow_orchestrate.py — CLEAN
- [x] tests/test_flow_prompt_assembly.py — CLEAN
- [x] tests/test_flow_session_persistence.py — CLEAN
- [x] tests/test_flow_slash_commands.py — CLEAN
- [x] tests/test_flow_spill_threshold.py — CLEAN
- [x] tests/test_flow_tool_call_dedup.py — CLEAN
- [x] tests/test_flow_tool_call_limit.py — CLEAN (CoToolLifecycle(_tracer=tracer) is proper injection ✓)
- [x] tests/test_flow_tool_call_repair.py — CLEAN
- [x] tests/test_flow_tool_calling_functional.py — CLEAN

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| test_flow_enforce_request_size.py | 251-278 | Mock/fake — monkeypatch replaces _TRACER module attr | Blocking | OPEN |
| test_flow_capability_checks.py | 16-17 | Dead code — _TOOL_REG, _AGENT built at module scope, never used in tests | Minor | OPEN |

## Phase 4 — Adversarial Review
- test_flow_enforce_request_size.py:252 — confirmed blocking. Pattern already in use for _emit_tool_budget_span. Deleting the test would hide observability regressions. Fix: inject tracer.
- test_flow_capability_checks.py:16-17 — confirmed minor dead code. _AGENT / _TOOL_REG built but no test function references them; _make_deps rebuilds the registry internally.
- test_flow_length_retry.py:constrained_settings — reviewed. The model_settings override is necessary to force a length truncation for the retry path to fire; the settings derive from the production noreason path with only max_tokens clamped. Not a bypass of personality or model identity. PASS.

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| co_cli/context/history_processors.py | enforce_request_size | (production fix) | Added `_tracer=None` kwarg; uses `(_tracer or _TRACER)` internally | DONE |
| test_flow_enforce_request_size.py | test_otel_span_emitted | Mock/fake (monkeypatch) | Removed monkeypatch; removed `hp` import; changed async→sync; inject `_tracer=tracer` | DONE |
| test_flow_capability_checks.py | module scope | Dead code | Removed `_TOOL_REG`, `_AGENT`; removed `build_agent` import | DONE |

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Log: .pytest-logs/20260508-140858-test-hygiene.log
- Result: 199 passed in 167.06s

## Phase 7 — Final Verdict
CLEAN — 2 violations fixed (1 blocking monkeypatch, 1 minor dead code), 199/199 pass.
