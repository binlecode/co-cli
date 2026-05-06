# REPORT-test-hygiene-20260504-000000

## Meta
- Scan path: tests/
- Files: 25
- Started: 2026-05-04T00:00:00
- Status: DONE

## Phase 1 — Load
- [x] rules loaded
- [x] file list enumerated (25 files)
- [x] conftest.py — plumbing-only (docstring)
- [x] _settings.py — SETTINGS, SETTINGS_NO_MCP, make_settings()
- [x] _timeouts.py — timeout constants
- [x] _ollama.py — ensure_ollama_warm (must be called BEFORE asyncio.timeout)
- [x] _co_harness.py — OTel pytest plugin

## Phase 2 — File Read Progress
- [x] tests/test_flow_agent_delegation.py — CLEAN
- [x] tests/test_flow_approval_subject.py — CLEAN
- [x] tests/test_flow_artifacts_waterfall_cap.py — CLEAN
- [x] tests/test_flow_bootstrap_canon.py — [!] _SilentFrontend fake
- [x] tests/test_flow_bootstrap_session.py — [!] monkeypatch.setenv x2
- [x] tests/test_flow_canon_recall.py — [!] _SilentFrontend fake
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
- [x] tests/test_flow_memory_search.py — [!] unnecessary make_settings() call
- [x] tests/test_flow_memory_store_nochunk.py — CLEAN
- [x] tests/test_flow_memory_write.py — CLEAN
- [x] tests/test_flow_observability_redaction.py — CLEAN
- [x] tests/test_flow_orchestrate.py — CLEAN
- [x] tests/test_flow_prompt_assembly.py — CLEAN
- [x] tests/test_flow_session_persistence.py — CLEAN
- [x] tests/test_flow_slash_commands.py — CLEAN
- [x] tests/test_flow_tool_calling_functional.py — CLEAN

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/test_flow_bootstrap_session.py | 115-121 | monkeypatch.setenv — KnowledgeSettings reads os.environ directly (BaseSettings), bypassing _env context | Blocking | OPEN |
| tests/test_flow_bootstrap_session.py | 126-132 | monkeypatch.setenv — same root cause as above | Blocking | OPEN |
| tests/test_flow_bootstrap_canon.py | 21-24 | _SilentFrontend is a hand-assembled fake; TerminalFrontend() is available | Blocking | OPEN |
| tests/test_flow_canon_recall.py | 26-28 | _SilentFrontend same fake in _make_ctx_with_store helper | Blocking | OPEN |
| tests/test_flow_memory_search.py | 31 | Redundant inner make_settings() call — should use SETTINGS.knowledge directly | Minor | OPEN |

## Phase 4 — Adversarial Review
- bootstrap_session monkeypatch: confirmed — KnowledgeSettings reads os.environ directly via BaseSettings, no injection point without monkeypatch. Fix: migrate to BaseModel + KNOWLEDGE_ENV_MAP (established pattern).
- _SilentFrontend: confirmed — TerminalFrontend() is available with no constructor args; confirmed by existing usages in bootstrap_session and slash_commands tests.
- make_settings() inner call: confirmed minor redundancy; make_settings().knowledge == SETTINGS.knowledge.

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| co_cli/config/knowledge.py | — | monkeypatch root cause | Migrated KnowledgeSettings from BaseSettings to BaseModel; added KNOWLEDGE_ENV_MAP (17 fields) | DONE |
| co_cli/config/core.py | — | monkeypatch root cause | Imported KNOWLEDGE_ENV_MAP; added "knowledge" to nested_env_map in fill_from_env | DONE |
| tests/test_flow_bootstrap_session.py | test_knowledge_settings_env_prefix_overrides_default | monkeypatch | Removed monkeypatch param; use load_config(_env={"CO_KNOWLEDGE_CHUNK_SIZE": "42"}) | DONE |
| tests/test_flow_bootstrap_session.py | test_knowledge_settings_env_overrides_json_config | monkeypatch | Removed monkeypatch param; use load_config(_env={"CO_KNOWLEDGE_CHUNK_SIZE": "99"}) | DONE |
| tests/test_flow_bootstrap_canon.py | all | _SilentFrontend fake | Replaced _SilentFrontend class with TerminalFrontend() | DONE |
| tests/test_flow_canon_recall.py | all | _SilentFrontend fake | Replaced _SilentFrontend class with TerminalFrontend() | DONE |
| tests/test_flow_memory_search.py | test_fts5_search_finds_indexed_entry | redundant make_settings() | Use SETTINGS.knowledge.model_copy(...) directly | DONE |

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Log: .pytest-logs/20260504-test-hygiene.log
- Result: 1 failed (pre-existing), 131 passed
- Pre-existing failure: test_flow_tool_calling_functional::test_tool_selection_shell_git_status — LLM takes >40s (hits asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS*2=40)). Confirmed pre-existing by git stash test: same failure on pre-change state, identical 40.41s call duration. Root cause: model timing, unrelated to hygiene changes.

## Phase 7 — Final Verdict
CLEAN (modulo pre-existing LLM-timeout failure in tool-calling test)

Violations fixed: 4 blocking (2× monkeypatch, 2× _SilentFrontend), 1 minor (redundant make_settings call)
Pre-existing failures: 1 (test_tool_selection_shell_git_status — LLM timeout, unrelated to hygiene changes)
