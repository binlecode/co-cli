# REPORT-test-hygiene-20260503-000000

## Meta
- Scan path: tests/
- Files: 25
- Started: 2026-05-03
- Status: IN PROGRESS

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] file list enumerated (25 files)
- [x] conftest.py read — neutral plumbing only (one-line docstring)
- [x] _settings.py read — SETTINGS, SETTINGS_NO_MCP, make_settings()
- [x] _timeouts.py read — LLM_NON_REASONING_TIMEOUT_SECS=10, LLM_COMPACTION_SUMMARY_TIMEOUT_SECS=60, LLM_TOOL_CONTEXT_TIMEOUT_SECS=20, HTTP_HEALTH_TIMEOUT_SECS=15, FILE_DB_TIMEOUT_SECS=30

## Phase 2 — File Read Progress
- [x] tests/test_flow_agent_delegation.py — CLEAN
- [x] tests/test_flow_approval_subject.py — CLEAN
- [x] tests/test_flow_artifacts_waterfall_cap.py — CLEAN
- [x] tests/test_flow_bootstrap_canon.py — CLEAN
- [x] tests/test_flow_bootstrap_session.py — CLEAN (monkeypatch.setenv sets real os.environ for env-var pipeline test; no code path bypass)
- [x] tests/test_flow_canon_recall.py — CLEAN
- [x] tests/test_flow_capability_checks.py — CLEAN
- [x] tests/test_flow_compaction_boundaries.py — CLEAN
- [x] tests/test_flow_compaction_proactive.py — CLEAN
- [x] tests/test_flow_compaction_recovery.py — CLEAN
- [x] tests/test_flow_compaction_summarization.py — CLEAN
- [x] tests/test_flow_history_processors.py — CLEAN
- [x] tests/test_flow_http_error_classifier.py — CLEAN
- [x] tests/test_flow_llm_call.py — CLEAN
- [x] tests/test_flow_memory_lifecycle.py — CLEAN
- [x] tests/test_flow_memory_recall.py — CLEAN
- [x] tests/test_flow_memory_search.py — CLEAN (make_settings surgical override for fts5)
- [x] tests/test_flow_memory_store_nochunk.py — CLEAN
- [x] tests/test_flow_memory_write.py — CLEAN
- [x] tests/test_flow_observability_redaction.py — CLEAN
- [x] tests/test_flow_orchestrate.py — CLEAN
- [x] tests/test_flow_prompt_assembly.py — CLEAN
- [x] tests/test_flow_session_persistence.py — CLEAN
- [x] tests/test_flow_slash_commands.py — CLEAN
- [!] tests/test_flow_tool_calling_functional.py — imports SilentFrontend via tests/_frontend.py alias

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/_frontend.py | 1-6 | Non-test helper re-exporting HeadlessFrontend as SilentFrontend — alias adds no value; import directly | Minor | OPEN |

## Phase 4 — Adversarial Review
- tests/test_flow_bootstrap_session.py monkeypatch.setenv (lines 116-133): Challenged as possible rule violation.
  VERDICT: Not a violation. KnowledgeSettings uses pydantic-settings env_prefix="CO_KNOWLEDGE_"; it reads from os.environ directly (not via load_config's _env param). monkeypatch.setenv correctly exercises the real env-var → config pipeline. No production path bypassed. DOWNGRADED to CLEAN.
- tests/_frontend.py: Confirmed violation — re-export alias wrapping HeadlessFrontend as SilentFrontend provides no value; single consumer test can import directly. CONFIRMED MINOR.

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| tests/_frontend.py | (whole file) | Non-test helper re-export | Deleted file; updated test_flow_tool_calling_functional.py to import HeadlessFrontend directly | DONE |
| tests/test_flow_tool_calling_functional.py | all | Non-test helper re-export | `from tests._frontend import SilentFrontend` → `from co_cli.display.headless import HeadlessFrontend as SilentFrontend` | DONE |

## Phase 6 — Test Run
- Command: uv run pytest -x
- Log: .pytest-logs/<timestamp>-test-hygiene.log
- Result: 134 passed, 0 failed (111.97s)

## Phase 7 — Final Verdict
CLEAN — 1 minor fix applied (gratuitous re-export alias removed), 134 tests passing.
Status: DONE
