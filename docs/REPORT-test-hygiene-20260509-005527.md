# REPORT-test-hygiene-20260509-005527

## Meta
- Scan path: tests/
- Files (before): 40 (39 at session start + 1 added by commit 9972efd mid-session)
- Files (after): 34
- Started: 2026-05-09T00:55:27
- Status: DONE
- Mandate: scan, group by flow/core, rename, merge, delete dead/low-ROI/structural/SDK-API tests

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] file list enumerated (39 files)
- [x] support files read (conftest.py, _settings.py, _timeouts.py, _ollama.py, _co_harness.py)

## Phase 2 — File Read Progress

### Bootstrap (5)
- [x] test_flow_bootstrap_budget_span.py (38) — CLEAN, behavioral OTEL span assertion
- [x] test_flow_bootstrap_canon.py (68) — CLEAN, real FTS5
- [x] test_flow_bootstrap_ollama_num_ctx.py (23) — CLEAN, validation logic
- [!] test_flow_bootstrap_session.py (167) — grab-bag: 1 bootstrap + 7 config_loading + 3 security + 1 skill_loading
- [x] test_flow_capability_checks.py (57) — CLEAN, real deps + tool

### LLM call / settings / observability (5)
- [x] test_flow_llm_call.py (59) — CLEAN, 3 LLM tests
- [!] test_flow_llm_settings.py (39) — single-test file, MERGE into llm_call
- [x] test_flow_http_error_classifier.py (75) — CLEAN, pure logic
- [x] test_flow_length_retry.py (167) — CLEAN, unit + LLM
- [x] test_flow_observability_redaction.py (35) — CLEAN, real SQLite

### Compaction / history (6)
- [!] test_flow_compaction_boundaries.py (133) — group_by_turn_multi_turn is low-ROI helper test
- [x] test_flow_compaction_proactive.py (340) — CLEAN
- [x] test_flow_compaction_processor_chain.py (171) — CLEAN
- [x] test_flow_compaction_recovery.py (209) — CLEAN
- [x] test_flow_compaction_summarization.py (149) — CLEAN
- [x] test_flow_history_processors.py (229) — CLEAN

### Memory / artifacts / canon (7)
- [!] test_flow_memory_lifecycle.py (42) — single-test file, MERGE into memory_write
- [x] test_flow_memory_recall.py (245) — CLEAN
- [!] test_flow_memory_search.py (49) — single-test file, MERGE into memory_store_nochunk
- [x] test_flow_memory_store_nochunk.py (118) — rename target → test_flow_memory_store.py
- [x] test_flow_memory_write.py (294) — CLEAN
- [x] test_flow_canon_recall.py (132) — CLEAN
- [x] test_flow_artifacts_waterfall_cap.py (129) — CLEAN

### Tool calling / spill / mcp (7)
- [x] test_flow_tool_call_dedup.py (130) — CLEAN
- [x] test_flow_tool_call_limit.py (264) — CLEAN
- [x] test_flow_tool_call_repair.py (109) — CLEAN
- [x] test_flow_tool_calling_functional.py (158) — CLEAN
- [x] test_flow_enforce_request_size.py (354) — CLEAN
- [x] test_flow_mcp_spill.py (113) — CLEAN
- [x] test_flow_spill_threshold.py (72) — CLEAN

### Agent / delegation / orchestration (4)
- [!] test_flow_agent_delegation.py (83) — redundant subset tests (depth chain, beyond_max_depth, 3x near-identical max_depth)
- [x] test_flow_delegation_discovery.py (83) — CLEAN
- [x] test_flow_orchestrate.py (89) — CLEAN
- [x] test_flow_background_tasks.py (133) — CLEAN

### Approval / commands / sessions / prompt (4)
- [x] test_flow_approval_subject.py (110) — CLEAN
- [x] test_flow_slash_commands.py (37) — CLEAN, single test but real functional value
- [x] test_flow_session_persistence.py (163) — CLEAN, absorbs restore_session test from bootstrap_session
- [x] test_flow_prompt_assembly.py (37) — CLEAN

## Phase 3 — Audit Findings

### Group A — File merges (single-test files into related larger ones)
| File | Tests | Action | Status |
|------|-------|--------|--------|
| test_flow_memory_search.py | 1 (FTS5 search) | merge into memory_store_nochunk → rename to test_flow_memory_store.py | OPEN |
| test_flow_memory_lifecycle.py | 1 (mutate replace) | merge into test_flow_memory_write.py (also tests mutate_artifact) | OPEN |
| test_flow_llm_settings.py | 1 (reasoning settings) | merge into test_flow_llm_call.py (sibling LLM call test) | OPEN |

### Group B — File restructuring (grab-bag split)
| File | Issue | Action | Status |
|------|-------|--------|--------|
| test_flow_bootstrap_session.py | 9 tests across 4 unrelated areas (1 session restore, 7 config, 1 skill) | rename → test_flow_config_loading.py; move test_restore_session_picks_most_recent → test_flow_session_persistence.py | OPEN |

### Group C — Test deletions (low-ROI / redundant)
| File | Test | Reason | Status |
|------|------|--------|--------|
| test_flow_agent_delegation.py | test_fork_deps_depth_propagates_through_chain | subsumed by fork_deps_increments_agent_depth (trivial induction) | OPEN |
| test_flow_agent_delegation.py | test_reason_raises_model_retry_beyond_max_depth | subsumed by at_max_depth (which uses `>=`) | OPEN |
| test_flow_agent_delegation.py | 3x test_*_raises_model_retry_at_max_depth | consolidate via parametrize (3 separate functions, identical mechanism) | OPEN |
| test_flow_compaction_boundaries.py | test_group_by_turn_multi_turn | helper exercised by every planner test, trivial 2-group assertion | OPEN |

## Phase 4 — Adversarial Review

- `test_fork_deps_depth_propagates_through_chain` initially flagged for deletion (subset of `test_fork_deps_increments_agent_depth`). Reversed: catches the bug class where production sets `depth=1` constant rather than incrementing — single-level test alone passes that bug. Folded into a single combined test that checks both child and grandchild.
- `test_group_by_turn_multi_turn` borderline for deletion (helper exercised by planner tests). Kept: it pins the contract "n turn pairs → n groups" that planner tests rely on.
- `test_flow_compact_command.py` discovered mid-session (added by commit 9972efd after initial scan). Single-test file paralleling `test_flow_slash_commands.py` (/clear); merged both slash-command tests into the latter.

## Phase 5 — Fixes Applied

### Merges (5)
| Source | Target | Action |
|--------|--------|--------|
| test_flow_llm_settings.py | test_flow_llm_call.py | merged 1 reasoning-settings test alongside 3 noreason tests; deleted source |
| test_flow_memory_lifecycle.py | test_flow_memory_write.py | merged mutate_artifact replace test (same module); deleted source |
| test_flow_memory_search.py | test_flow_memory_store_nochunk.py | merged FTS5 search test (same MemoryStore); renamed target → test_flow_memory_store.py; deleted source |
| test_flow_mcp_spill.py + test_flow_spill_threshold.py | test_flow_spill.py | created unified spill file covering helper + lifecycle layers; deleted both sources |
| test_flow_compact_command.py | test_flow_slash_commands.py | merged /compact test alongside /clear test; deleted source |

### Restructure (1)
| Source | Target | Action |
|--------|--------|--------|
| test_flow_bootstrap_session.py | test_flow_session_persistence.py + test_flow_config_loading.py | moved restore_session test → session_persistence; renamed remainder (config + security + skills) → config_loading |

### Test deletions / consolidations (1 file)
| File | Action |
|------|--------|
| test_flow_agent_delegation.py | deleted `test_reason_raises_model_retry_beyond_max_depth` (subsumed); folded `test_fork_deps_depth_propagates_through_chain` into `test_fork_deps_increments_agent_depth` (combined chain assertion); parametrized 3x near-identical max_depth tests for reason/knowledge_analyze/web_research into one |

### Spec doc updates (2)
- `docs/specs/compaction.md` — replaced 4 references to deleted `test_flow_spill_threshold.py` with `test_flow_spill.py`; removed stale row pointing to long-deleted `test_flow_spill_otel.py`; added new MCP-lifecycle row.
- `docs/specs/memory.md` — replaced refs to deleted `test_flow_memory_search.py`, `test_flow_memory_lifecycle.py`, `test_flow_bootstrap_session.py`, and renamed `test_flow_memory_store_nochunk.py` to point at the new homes.

## Phase 6 — Test Run
- Command: `uv run pytest -x -v`
- Log: `.pytest-logs/20260509-010531-test-hygiene.log`
- Result: **202 passed** in 155.85s (2m 35s)

## Phase 7 — Final Verdict

**CLEAN — files reduced 40 → 34 (−15%); 202 tests pass; spec docs updated; no behavioral regressions.**
