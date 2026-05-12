# REPORT-test-hygiene-20260506-120000

## Meta
- Scan path: tests/
- Files: 31
- Started: 2026-05-06T12:00:00
- Status: DONE

## Phase 1 — Load
- [x] rules loaded
- [x] file list enumerated (31 files)

## Phase 2 — File Read Progress
- [x] tests/test_flow_agent_delegation.py — CLEAN
- [x] tests/test_flow_approval_subject.py — CLEAN
- [x] tests/test_flow_artifacts_waterfall_cap.py — CLEAN
- [x] tests/test_flow_bootstrap_budget_span.py — CLEAN (new file, verified signature matches)
- [x] tests/test_flow_bootstrap_canon.py — CLEAN
- [x] tests/test_flow_bootstrap_session.py — CLEAN
- [x] tests/test_flow_canon_recall.py — CLEAN
- [x] tests/test_flow_capability_checks.py — CLEAN (note: _AGENT built at module scope but unused in tests)
- [x] tests/test_flow_compaction_boundaries.py — CLEAN
- [x] tests/test_flow_compaction_proactive.py — CLEAN
- [x] tests/test_flow_compaction_recovery.py — CLEAN
- [x] tests/test_flow_compaction_summarization.py — CLEAN
- [x] tests/test_flow_history_processors.py — CLEAN
- [x] tests/test_flow_http_error_classifier.py — CLEAN
- [x] tests/test_flow_llm_call.py — CLEAN
- [x] tests/test_flow_llm_settings.py — CLEAN
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
- [!] tests/test_flow_spill_otel.py — test_spill_tool_result_span_attrs_below_threshold is duplicate
- [!] tests/test_flow_spill_threshold.py — test_constants_pinned (structural), test_spill_large_content (duplicate)
- [x] tests/test_flow_tool_call_limit.py — CLEAN
- [x] tests/test_flow_tool_calling_functional.py — CLEAN
- [x] tests/test_flow_turn_budget.py — CLEAN

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/test_flow_spill_threshold.py | 13–16 | Structural test — passes without calling any production function; constant values redundantly guarded by boundary tests test_spill_at_threshold (4_001) and test_force_spill_at/above_preview_size | Blocking | OPEN |
| tests/test_flow_spill_otel.py | 13–22 | Duplicate with trivial delta — same function (spill_if_oversized), same invariant (no-spill below threshold), no unique assertions vs test_no_spill_below_threshold | Blocking | OPEN |
| tests/test_flow_spill_threshold.py | 34–39 | Duplicate with trivial delta — same function, same invariant (PERSISTED_OUTPUT_TAG in result) as test_spill_at_threshold; content size difference (10_000 vs 4_001) tests no additional code path; assertion doesn't distinguish tool_name difference | Blocking | OPEN |

## Phase 4 — Adversarial Review
- test_constants_pinned: Challenged — does pinning SPILL_THRESHOLD_CHARS==4_000 add coverage not in test_spill_at_threshold?
  No: test_spill_at_threshold uses hardcoded 4_001 which fails if threshold changes ≠ 4_000. Confirmed structural.
- test_spill_tool_result_span_attrs_below_threshold: Challenged — does the SPILL_THRESHOLD_CHARS-1 boundary differ from 3_999?
  No: both land on the same `if len(content) <= SPILL_THRESHOLD_CHARS: return content` branch; assertions identical. Confirmed duplicate.
- test_spill_large_content: Challenged — does tool_name="file_read" (vs "shell" in test_spill_at_threshold) create meaningful divergence?
  No: assertion is only PERSISTED_OUTPUT_TAG in result; the head-only vs head+tail preview difference is not asserted. Confirmed duplicate.

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| tests/test_flow_spill_threshold.py | test_constants_pinned | Structural test | Deleted test function + removed now-unused SPILL_THRESHOLD_CHARS and TOOL_RESULT_PREVIEW_CHARS imports | DONE |
| tests/test_flow_spill_otel.py | test_spill_tool_result_span_attrs_below_threshold | Duplicate | Deleted test function | DONE |
| tests/test_flow_spill_threshold.py | test_spill_large_content | Duplicate | Deleted test function | DONE |

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Log: .pytest-logs/20260506-*-test-hygiene.log
- Result: 156 passed in 251s — GREEN

## Phase 7 — Final Verdict
CLEAN — 3 structural/duplicate tests removed; 156 surviving tests all green.
