# REPORT-test-hygiene-20260509-150000

## Meta
- Scan path: tests/
- Files: 38
- Started: 2026-05-09T15:00:00
- Status: DONE

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] conftest.py read — minimal, docstring only (clean)
- [x] _settings.py read — SETTINGS / SETTINGS_NO_MCP / make_settings() clean
- [x] _timeouts.py read — 5 constants: LLM_NON_REASONING(10s), LLM_COMPACTION_SUMMARY(60s), LLM_REASONING(30s), LLM_TOOL_CONTEXT(50s), HTTP_HEALTH(15s), FILE_DB(30s)
- [x] _ollama.py read — ensure_ollama_warm must be called OUTSIDE asyncio.timeout
- [x] file list enumerated (38 files)

## Phase 2 — File Read Progress
- [x] tests/test_flow_agent_delegation.py — CLEAN
- [x] tests/test_flow_approval_subject.py — CLEAN
- [x] tests/test_flow_artifacts_waterfall_cap.py — CLEAN
- [x] tests/test_flow_background_tasks.py — CLEAN
- [x] tests/test_flow_bootstrap_budget_span.py — CLEAN
- [x] tests/test_flow_bootstrap_canon.py — CLEAN
- [x] tests/test_flow_bootstrap_ollama_num_ctx.py — CLEAN
- [x] tests/test_flow_bootstrap_session.py — CLEAN
- [x] tests/test_flow_canon_recall.py — CLEAN
- [x] tests/test_flow_capability_checks.py — CLEAN
- [x] tests/test_flow_compaction_boundaries.py — CLEAN
- [!] tests/test_flow_compaction_proactive.py — monkeypatch at line 343 (test_post_compaction_failure_leaves_runtime_clean)
- [x] tests/test_flow_compaction_processor_chain.py — CLEAN
- [x] tests/test_flow_compaction_recovery.py — CLEAN
- [x] tests/test_flow_compaction_summarization.py — CLEAN
- [x] tests/test_flow_delegation_discovery.py — CLEAN
- [x] tests/test_flow_enforce_request_size.py — CLEAN
- [x] tests/test_flow_history_processors.py — CLEAN
- [x] tests/test_flow_http_error_classifier.py — CLEAN
- [x] tests/test_flow_length_retry.py — CLEAN (SimpleNamespace duck-type is minimal input adapter for pure gate logic; full integration test also present)
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
- [x] tests/test_flow_tool_call_limit.py — CLEAN
- [x] tests/test_flow_tool_call_repair.py — CLEAN
- [x] tests/test_flow_tool_calling_functional.py — CLEAN

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| test_flow_compaction_proactive.py | 343 | Mock/fake — monkeypatch.setattr on production estimate_message_tokens | Blocking | ESCALATED |

## Phase 4 — Adversarial Review
- test_flow_compaction_proactive.py:343 — confirmed monkeypatch violation. `estimate_message_tokens` cannot naturally raise on valid ModelMessage content, so the failure path can only be injected. The test guards a real regression (TASK 3 commit ordering). Fix would require splitting `compact_under_budget` into compute + commit phases — architectural change. → ESCALATE

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| test_flow_compaction_proactive.py | test_post_compaction_failure_leaves_runtime_clean | Mock/fake — monkeypatch | ESCALATED — requires splitting compact_under_budget into compute+commit phases | ESCALATED |

## Phase 6 — Test Run
- Command: uv run pytest -x
- Log: .pytest-logs/20260509-004707-test-hygiene-full.log
- Result: 204 passed, 0 failed (169.25s)

## Phase 7 — Final Verdict
ESCALATIONS PENDING — 1 monkeypatch violation in test_post_compaction_failure_leaves_runtime_clean; all other 203 tests clean; suite fully green.
