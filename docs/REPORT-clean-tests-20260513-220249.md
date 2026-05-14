# REPORT-clean-tests-20260513-220249

## Meta
- Scan path: tests/
- Files: 54
- Started: 2026-05-13T22:02:49
- Status: DONE

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] workflow registry loaded (agent_docs/system-workflows-to-test.md) — 18 sections, ~80 workflows
- [x] file list enumerated (54 files)
- [x] conftest.py — docstring-only, no plumbing
- [x] _settings.py — SETTINGS, SETTINGS_NO_MCP, make_settings() singletons
- [x] _timeouts.py — LLM_NON_REASONING_TIMEOUT_SECS=10, LLM_COMPACTION_SUMMARY_TIMEOUT_SECS=60, LLM_REASONING_TIMEOUT_SECS=30, LLM_TOOL_CONTEXT_TIMEOUT_SECS=50, HTTP_HEALTH_TIMEOUT_SECS=15, FILE_DB_TIMEOUT_SECS=30
- [x] _ollama.py — ensure_ollama_warm must be called OUTSIDE asyncio.timeout

## Phase 2 — File Read Progress
- [!] tests/test_cli_skills_pin.py — Minor: filename not test_flow_* prefix; tests behavioral
- [!] tests/test_cli_skills_usage.py — Minor: filename not test_flow_* prefix; tests behavioral
- [x] tests/test_flow_approval_subject.py — CLEAN
- [x] tests/test_flow_artifact_manage.py — CLEAN
- [x] tests/test_flow_background_tasks.py — CLEAN
- [x] tests/test_flow_bootstrap_budget_span.py — CLEAN
- [x] tests/test_flow_bootstrap_canon.py — CLEAN
- [x] tests/test_flow_bootstrap_config_loading.py — CLEAN
- [x] tests/test_flow_bootstrap_ollama_num_ctx.py — CLEAN
- [x] tests/test_flow_capability_checks.py — CLEAN
- [x] tests/test_flow_chat_loop.py — CLEAN
- [!] tests/test_flow_compaction_boundaries.py — Blocking: test_group_by_turn_multi_turn subsumed unit test
- [x] tests/test_flow_compaction_enforce_request_size.py — CLEAN
- [x] tests/test_flow_compaction_history_processors.py — CLEAN
- [x] tests/test_flow_compaction_proactive.py — CLEAN
- [x] tests/test_flow_compaction_processor_chain.py — CLEAN
- [x] tests/test_flow_compaction_recovery.py — CLEAN
- [x] tests/test_flow_compaction_session_rewrite.py — CLEAN
- [x] tests/test_flow_compaction_slash_commands.py — CLEAN
- [x] tests/test_flow_compaction_summarization.py — CLEAN
- [x] tests/test_flow_delegation_agent.py — CLEAN
- [x] tests/test_flow_delegation_discovery.py — CLEAN
- [x] tests/test_flow_http_error_classifier.py — CLEAN
- [x] tests/test_flow_knowledge_search.py — CLEAN
- [x] tests/test_flow_knowledge_view.py — CLEAN (scope drift: knowledge_view tool not in registry)
- [x] tests/test_flow_llm_call.py — CLEAN
- [!] tests/test_flow_mcp_schema.py — Blocking: test_sanitizer_applied_at_list_tools uses _FakeMCPServer
- [x] tests/test_flow_memory_artifacts_waterfall_cap.py — CLEAN
- [x] tests/test_flow_memory_canon_recall.py — CLEAN
- [x] tests/test_flow_memory_store.py — CLEAN
- [x] tests/test_flow_memory_write.py — CLEAN
- [x] tests/test_flow_observability_redaction.py — CLEAN
- [x] tests/test_flow_orchestrate_400_reformulation.py — CLEAN
- [x] tests/test_flow_orchestrate_interrupted_turn.py — CLEAN
- [x] tests/test_flow_orchestrate_length_retry.py — CLEAN
- [x] tests/test_flow_prompt_assembly.py — CLEAN
- [x] tests/test_flow_session_persistence.py — CLEAN
- [x] tests/test_flow_session_search.py — CLEAN
- [x] tests/test_flow_session_view.py — CLEAN (scope drift: session_view tool not in registry)
- [!] tests/test_flow_skill_bundled_library.py — Blocking: test_bundled_skill_has_phase_section[*] (7×) structural; Minor: test_bundled_skill_has_description[*] attribute check
- [x] tests/test_flow_skill_creator_dispatch.py — CLEAN
- [x] tests/test_flow_skill_index.py — CLEAN
- [x] tests/test_flow_skill_installer_dispatch.py — CLEAN
- [x] tests/test_flow_skill_lint.py — CLEAN
- [x] tests/test_flow_skill_manifest.py — CLEAN
- [!] tests/test_flow_skill_protocol.py — Blocking (×6): file existence, direct file reads, docstring assertions
- [x] tests/test_flow_skill_search.py — CLEAN
- [x] tests/test_flow_skills_manage.py — CLEAN
- [x] tests/test_flow_skills_tools.py — CLEAN
- [x] tests/test_flow_slash_dispatch.py — CLEAN
- [x] tests/test_flow_spill.py — CLEAN
- [x] tests/test_flow_tool_call_dedup.py — CLEAN
- [x] tests/test_flow_tool_call_functional.py — CLEAN
- [x] tests/test_flow_tool_call_limit.py — CLEAN
- [x] tests/test_flow_tool_call_repair.py — CLEAN
- [!] tests/test_skill_usage.py — Minor: filename not test_flow_* prefix; tests behavioral

## Phase 2.5 — Stale Call Grep
- [x] ~15 call sites with non-trivial args checked, 0 stale parameter calls found

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/test_flow_compaction_boundaries.py | test_group_by_turn_multi_turn | Subsumed unit test — group_by_turn end-to-end covered by plan_compaction_boundaries tests; no extra failure mode | Blocking | OPEN |
| tests/test_flow_mcp_schema.py | test_sanitizer_applied_at_list_tools + _FakeMCPServer | Real dependencies only — `_FakeMCPServer` is hand-assembled class simulating MCP server; structural proxy-wiring test | Blocking | OPEN |
| tests/test_flow_skill_bundled_library.py | test_bundled_skill_has_phase_section[*] (×7 parametrized) | Behavior over structure — reads file directly via `path.read_text()`; no production function called | Blocking | OPEN |
| tests/test_flow_skill_protocol.py | test_protocol_file_exists_with_06_prefix | Behavior over structure — file existence + name prefix check; no production code | Blocking | OPEN |
| tests/test_flow_skill_protocol.py | test_protocol_file_has_five_reflex_sections | Behavior over structure — reads file directly via `_PROTOCOL_FILE.read_text()`; no production function | Blocking | OPEN |
| tests/test_flow_skill_protocol.py | test_protocol_file_has_tier_distinction_sentence | Behavior over structure — reads file directly; no production function | Blocking | OPEN |
| tests/test_flow_skill_protocol.py | test_skill_manage_docstring_has_creation_trigger | Behavior over structure — `inspect.getdoc(skill_manage)` docstring assertion; explicitly forbidden | Blocking | OPEN |
| tests/test_flow_skill_protocol.py | test_skill_view_docstring_has_read_before_write | Behavior over structure — docstring assertion on `skill_view` | Blocking | OPEN |
| tests/test_flow_skill_protocol.py | test_skill_search_docstring_has_dedup_guard | Behavior over structure — docstring assertion on `skill_search` | Blocking | OPEN |
| tests/test_flow_skill_bundled_library.py | test_bundled_skill_has_description[*] (×7 parametrized) | Behavior over structure — `SkillConfig.description` attribute presence only; no workflow behavior | Minor | OPEN |
| tests/test_cli_skills_pin.py | module | File naming — `test_cli_*` prefix; convention is `test_flow_<area>.py` | Minor | OPEN |
| tests/test_cli_skills_usage.py | module | File naming — `test_cli_*` prefix; convention is `test_flow_<area>.py` | Minor | OPEN |
| tests/test_skill_usage.py | module | File naming — `test_skill_*` prefix; convention is `test_flow_<area>.py` | Minor | OPEN |

## Phase 4 — Adversarial Review
- test_flow_compaction_boundaries.py/test_group_by_turn_multi_turn — plan_compaction_boundaries tests use multi-turn histories, implicitly covering group_by_turn correctness. No unique failure mode in standalone unit test. **CONFIRMED violation.**
- test_flow_mcp_schema.py/_FakeMCPServer — defined entirely in the test file; simulates MCP server interface. Not real infrastructure. Sanitizer pure-function tests cover all schema transformation. **CONFIRMED violation.**
- test_flow_skill_bundled_library.py/test_bundled_skill_has_phase_section — reads file directly with `path.read_text()`; zero production code path. **CONFIRMED violation.**
- test_flow_skill_protocol.py/6 tests — file-existence check, direct file reads, inspect.getdoc — all bypass production code paths. **CONFIRMED violations.**
- test_flow_skill_bundled_library.py/test_bundled_skill_has_description — checks SkillConfig.description attribute presence, not workflow behavior. Fix is straightforward (delete). **CONFIRMED Minor.**
- Naming violations (3 files) — all behavioral content; rename only, no logic changes. **CONFIRMED Minor.**

## Phase 4.5 — Workflow Coverage
| Workflow | Tests | Failure modes covered | Status | Severity |
|----------|-------|-----------------------|--------|----------|
| 1.1 Settings load | test_flow_bootstrap_config_loading.py | env precedence, invalid config, security check | Covered | — |
| 1.2 Ollama num_ctx | test_flow_bootstrap_ollama_num_ctx.py | floor logic, agentic floor | Covered | — |
| 1.3 Knowledge backend resolution | — | — | Uncovered | Minor |
| 1.4 Knowledge dir sync | test_flow_memory_store.py | hash-skip, partial failure | Covered | — |
| 1.5 Canon sync | test_flow_bootstrap_canon.py | whole-body, no-op conditions | Covered | — |
| 1.6 Skill loading | test_flow_bootstrap_config_loading.py + skill tests | user override, malformed skip | Covered | — |
| 1.7 MCP server discovery | test_flow_mcp_schema.py | schema sanitization only; fail-isolation not tested | Stub-covered | Minor |
| 1.8 Session restore | test_flow_session_persistence.py | latest-by-filename, empty history | Covered | — |
| 1.9 Session index init | — | — | Uncovered | Minor |
| 1.10 Capability discovery | test_flow_capability_checks.py | degradation reported, display sections | Covered | — |
| 2.1 REPL loop | test_flow_chat_loop.py | empty skip, Ctrl+C, EOF | Covered | — |
| 2.2 Built-in slash dispatch | test_flow_slash_dispatch.py + compaction_slash | builtin shadow protected, /clear | Covered | — |
| 2.3 Skill slash dispatch | test_flow_slash_dispatch.py | arg expansion, blocked keys, builtin shadow | Covered | — |
| 2.4 Skill env injection + cleanup | test_flow_slash_dispatch.py | env restore, active_skill_name cleared | Covered | — |
| 2.5 /resume | — | — | Uncovered | Blocking |
| 2.6 /new and /clear | test_flow_compaction_slash_commands.py | compaction state reset | Covered | — |
| 2.7 /compact | — | — | Uncovered | Blocking |
| 2.8 /sessions | — | — | Uncovered | Blocking |
| 2.9 /skills family (install/reload/upgrade) | test_cli_skills_pin.py, test_cli_skills_usage.py | pin/unpin/usage covered; install/reload/upgrade CLI not tested | Stub-covered | Minor |
| 2.10 /memory family | — | — | Uncovered | Blocking |
| 2.11 /approvals | — | — | Uncovered | Minor |
| 2.12 /reasoning | — | — | Uncovered | Blocking |
| 2.13 /background + /tasks + /cancel | test_flow_background_tasks.py | full lifecycle, cancel, SIGKILL | Covered | — |
| 2.14 /history | — | — | Uncovered | Minor |
| 2.15 /tools | — | — | Uncovered | Minor |
| 3.1 run_turn | test_flow_tool_call_functional.py | golden path, denied tool | Covered | — |
| 3.2 Stream segment | test_flow_tool_call_functional.py | via run_turn | Covered | — |
| 3.3 Tool approval loop | test_flow_tool_call_functional.py | deny path | Covered | — |
| 3.4 Approval subject | test_flow_approval_subject.py | all four tool shapes | Covered | — |
| 3.5 Clarify | — | — | Uncovered | Blocking |
| 3.6 Context overflow recovery | test_flow_compaction_recovery.py | PATH 1 and PATH 2, one-shot gate | Covered | — |
| 3.7 HTTP 400 reformulation | test_flow_orchestrate_400_reformulation.py | budget decrement, exhaust | Covered | — |
| 3.8 Interrupt handling | test_flow_orchestrate_interrupted_turn.py | drop trailing, abort marker | Covered | — |
| 3.9 Output limits | test_flow_orchestrate_length_retry.py | length finish, ctx ratio | Covered | — |
| 3.10 Transcript persistence | test_flow_compaction_session_rewrite.py + session_persistence | rewrite path, cursor | Covered | — |
| 3.11 Reasoning display modes | — | — | Uncovered | Blocking |
| 3.12 Doom-loop injection | — | — | Uncovered | Minor |
| 4.1 Static instruction assembly | test_flow_prompt_assembly.py + skill_manifest + skill_protocol | block order, skill protocol in prompt | Covered | — |
| 4.2 Toolset guidance gating | test_flow_prompt_assembly.py | absent/present tool gating | Covered | — |
| 4.3 Category awareness prompt | — | — | Uncovered | Minor |
| 4.4 Dynamic instruction layers | — | — | Uncovered | Minor |
| 5.1 dedup_tool_results | test_flow_compaction_history_processors.py | tail protection, collapse | Covered | — |
| 5.2 evict_old_tool_results | test_flow_compaction_history_processors.py | compactable vs non-compactable, pairing | Covered | — |
| 5.3 enforce_request_size | test_flow_compaction_enforce_request_size.py | largest-first, fallback | Covered | — |
| 5.4 proactive_window_processor | test_flow_compaction_proactive.py | threshold, anti-thrash, commit | Covered | — |
| 5.5 sanitize_surrogate_codepoints | — | — | Uncovered | Minor |
| 6.1 L0 admission cap | test_flow_tool_call_limit.py | cap, reset on run_step, span | Covered | — |
| 6.2 L1 emit-time spill | test_flow_spill.py | threshold, force, stub shape | Covered | — |
| 6.3 Boundary planner | test_flow_compaction_boundaries.py | None cases, multi-turn | Covered | — |
| 6.4 Marker assembly | — (implicitly via compaction_proactive) | shape only via E2E | Stub-covered | Minor |
| 6.5 Summarizer LLM call | test_flow_compaction_summarization.py | prior summary carry-forward | Covered | — |
| 6.6 Token estimation | test_flow_compaction_summarization.py | local vs reported max | Covered | — |
| 6.7 Compaction commit | test_flow_compaction_proactive.py | via proactive E2E | Covered | — |
| 7.1 Native tool registration | — | — | Uncovered | Minor |
| 7.2 MCP tool discovery | — | — | Uncovered | Minor |
| 7.3 Lifecycle hook chain | test_flow_tool_call_dedup.py + repair + spill | dedup, JSON repair, MCP spill | Covered | — |
| 7.4 Sequential locking | — | — | Uncovered | Minor |
| 7.5 Read-before-write | — | — | Uncovered | Minor |
| 7.6 Staleness tracking | — | — | Uncovered | Minor |
| 8.1 file_find | — | — | Uncovered | Minor |
| 8.2 file_read | — | — | Uncovered | Blocking |
| 8.3 file_search | — | — | Uncovered | Blocking |
| 8.4 file_write | test_flow_tool_call_functional.py | deny path only; successful write not tested | Stub-covered | Minor |
| 8.5 file_patch | — | — | Uncovered | Minor |
| 9.1 web_search | — (skip if key absent) | — | Uncovered | Minor |
| 9.2 web_fetch | — | — | Uncovered | Minor |
| 10.1 shell command-shape policy | test_flow_tool_call_functional.py | via functional shell test | Covered | — |
| 10.2 code_execute | — | — | Uncovered | Minor |
| 10.3 Background tasks | test_flow_background_tasks.py | full lifecycle | Covered | — |
| 10.4 Delegation subagents | test_flow_delegation_agent.py + discovery | isolation, scope | Covered | — |
| 11.1 Obsidian | — (gate-conditional) | — | Uncovered | Minor |
| 11.2 Google Drive/Gmail | — (gate-conditional) | — | Uncovered | Minor |
| 12.1 memory_search canon | test_flow_memory_canon_recall.py | full-body inline, kind isolation | Covered | — |
| 12.2 memory_search user priority | test_flow_knowledge_search.py | cap, cross-kind isolation | Covered | — |
| 12.3 memory_search waterfall | test_flow_memory_artifacts_waterfall_cap.py | both caps | Covered | — |
| 12.4 memory_search grep fallback | test_flow_knowledge_search.py | canon exclusion | Covered | — |
| 12.5 memory_search browse mode | test_flow_knowledge_search.py | no-LLM, current-session excluded | Covered | — |
| 12.6 knowledge_manage | test_flow_artifact_manage.py + memory_write | all four actions | Covered | — |
| 12.8 memory_store hash-skip | test_flow_memory_store.py | second-pass skip, mutation re-index | Covered | — |
| 12.9 skills channel removed guard | test_flow_knowledge_search.py | tool_error returned | Covered | — |
| 13.1 Session transcript append | test_flow_session_persistence.py | cursor advance, permissions | Covered | — |
| 13.2 Session transcript rewrite | test_flow_compaction_session_rewrite.py | atomic rewrite | Covered | — |
| 13.3 Session indexing | — | — | Uncovered | Minor |
| 13.4 load_transcript | test_flow_session_persistence.py | malformed skip, cap | Covered | — |
| 13.5 Sessions channel recall | test_flow_session_search.py | dedup, cap, current excluded | Covered | — |
| 14.1 Manual dream | — | — | Uncovered | Blocking |
| 14.2 Auto dream | — | — | Uncovered | Blocking |
| 14.3 Dream phase 1 transcript mining | — | — | Uncovered | Blocking |
| 14.4 Dream phase 2 merge | — | — | Uncovered | Minor |
| 14.5 Dream phase 3 decay | — | — | Uncovered | Minor |
| 14.6 Dream state load/save | — | — | Uncovered | Minor |
| 14.7 Cycle timeout | — | — | Uncovered | Minor |
| 14.8 Restore archived artifact | — | — | Uncovered | Blocking |
| 15.1 Skill containment check | test_flow_bootstrap_config_loading.py | symlink escape blocked | Covered | — |
| 15.2 Skill security scan | test_flow_skills_manage.py + skill_lint | scan-rollback, pattern detection | Covered | — |
| 15.3 skills_list tool | — | — | Uncovered | Minor |
| 15.4 skill_view | test_flow_skills_tools.py | body inline, plugin prefix, file_path error | Covered | — |
| 15.5 skill_manage write | test_flow_skills_manage.py | all actions, bundled protection | Covered | — |
| 15.6 skill-env blocked-key | test_flow_slash_dispatch.py | PATH filtered, safe key kept | Covered | — |
| 16.1 Approval prompt | test_flow_tool_call_functional.py | y/n paths | Covered | — |
| 16.2 Auto-approval session rules | test_flow_approval_subject.py + tool_call_functional | exact-match, no-prompt | Covered | — |
| 16.3 Tool-denial path | test_flow_tool_call_functional.py | deny → no file created | Covered | — |
| 17.1 Span emission | — | — | Uncovered | Minor |
| 17.2 SQLite exporter + redaction | test_flow_observability_redaction.py | PII redaction in persisted row | Covered | — |
| 17.3 Trace viewers | — | — | Uncovered | Minor |
| 17.4 tool_budget.resolved span | test_flow_bootstrap_budget_span.py | span name + all 5 attributes | Covered | — |
| 18.1 Personality static assembly | test_flow_prompt_assembly.py | static instructions with personality | Stub-covered | Minor |
| 18.2 Personality discovery/validation | — | — | Uncovered | Minor |

Scope-drift tests:
- test_flow_knowledge_view.py — `knowledge_view` tool has no registry entry (potential §12 gap)
- test_flow_session_view.py — `session_view` tool has no registry entry (potential §13 gap)
- test_cli_skills_pin.py — pin/unpin not in §2.9 registry entry (registry gap — §2.9 covers install/reload/upgrade)
- test_cli_skills_usage.py — /skills usage not in §2.9 entry (registry gap)

Trim candidates:
- test_flow_compaction_boundaries.py::test_group_by_turn_multi_turn — subsumed
- test_flow_mcp_schema.py::test_sanitizer_applied_at_list_tools + _FakeMCPServer class — fake dep
- test_flow_skill_bundled_library.py::test_bundled_skill_has_phase_section[*] — structural file-read
- test_flow_skill_bundled_library.py::test_bundled_skill_has_description[*] — structural attribute check
- test_flow_skill_protocol.py::test_protocol_file_exists_with_06_prefix — structural
- test_flow_skill_protocol.py::test_protocol_file_has_five_reflex_sections — structural
- test_flow_skill_protocol.py::test_protocol_file_has_tier_distinction_sentence — structural
- test_flow_skill_protocol.py::test_skill_manage_docstring_has_creation_trigger — docstring
- test_flow_skill_protocol.py::test_skill_view_docstring_has_read_before_write — docstring
- test_flow_skill_protocol.py::test_skill_search_docstring_has_dedup_guard — docstring

## Phase 4.6 — Consolidation Plan
| Workflow group | Current files | Target file | Test moves |
|----------------|---------------|-------------|------------|
| §2.9 /skills pin | tests/test_cli_skills_pin.py | tests/test_flow_skills_pin.py | rename (git mv) |
| §2.9 /skills usage | tests/test_cli_skills_usage.py | tests/test_flow_skills_usage.py | rename (git mv) |
| §15 skill usage sidecar | tests/test_skill_usage.py | tests/test_flow_skill_usage.py | rename (git mv) |

## Phase 5 — Fixes Applied
| File | Test | Rule / Action | Status |
|------|------|---------------|--------|
| tests/test_flow_compaction_boundaries.py | test_group_by_turn_multi_turn | Trim — subsumed unit test; removed group_by_turn from import | DONE |
| tests/test_flow_mcp_schema.py | test_sanitizer_applied_at_list_tools + _FakeMCPServer | Delete — hand-assembled fake; removed asyncio, SimpleNamespace, _SanitizingMCPServer imports | DONE |
| tests/test_flow_skill_bundled_library.py | test_bundled_skill_has_phase_section[*] | Delete — structural file-read; no production function called | DONE |
| tests/test_flow_skill_bundled_library.py | test_bundled_skill_has_description[*] | Delete — structural attribute presence check | DONE |
| tests/test_flow_skill_protocol.py | test_protocol_file_exists_with_06_prefix | Delete — file existence check; no production code | DONE |
| tests/test_flow_skill_protocol.py | test_protocol_file_has_five_reflex_sections | Delete — direct file read; no production function | DONE |
| tests/test_flow_skill_protocol.py | test_protocol_file_has_tier_distinction_sentence | Delete — direct file read; no production function | DONE |
| tests/test_flow_skill_protocol.py | test_skill_manage_docstring_has_creation_trigger | Delete — inspect.getdoc assertion; explicitly forbidden | DONE |
| tests/test_flow_skill_protocol.py | test_skill_view_docstring_has_read_before_write | Delete — docstring assertion | DONE |
| tests/test_flow_skill_protocol.py | test_skill_search_docstring_has_dedup_guard | Delete — docstring assertion | DONE |
| tests/test_flow_skill_protocol.py | (module) | Rewrote module — removed unused imports (_RULES_DIR, _PROTOCOL_FILE, _FIVE_REFLEXES, _TIER_SENTENCE, inspect) | DONE |
| tests/test_cli_skills_pin.py | (module) | Rename → tests/test_flow_skills_pin.py (git mv) | DONE |
| tests/test_cli_skills_usage.py | (module) | Rename → tests/test_flow_skills_usage.py (git mv) | DONE |
| tests/test_skill_usage.py | (module) | Rename → tests/test_flow_skill_usage.py (git mv) | DONE |
| docs/specs/compaction.md | spec table lines 722-729 | Fix stale test file references: test_flow_history_processors → test_flow_compaction_history_processors, test_flow_enforce_request_size → test_flow_compaction_enforce_request_size; group_by_turn row updated | DONE |

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Result: 389 passed, 0 failed in 238.67s

## Phase 7 — Final Verdict
CLEAN

All Blocking violations fixed. 389 tests pass.

### ESCALATIONS (user-facing workflows with no test coverage — require new behavioral tests):
- **2.5 /resume** — Entry: `co_cli/commands/resume.py: _cmd_resume`
- **2.7 /compact** — Entry: `co_cli/commands/compact.py`
- **2.8 /sessions** — Entry: `co_cli/commands/sessions.py`
- **2.10 /memory family** — Entry: `co_cli/commands/knowledge.py`
- **2.12 /reasoning** — Entry: `co_cli/commands/reasoning.py`
- **3.5 Clarify** — Entry: `co_cli/tools/clarify.py: clarify`
- **3.11 Reasoning display modes** — Entry: `co_cli/display/stream_renderer.py`
- **8.2 file_read** — Entry: `co_cli/tools/files/read.py: file_read`
- **8.3 file_search** — Entry: `co_cli/tools/files/search.py: file_search`
- **14.1 Manual dream** — Entry: `co_cli/memory/dream.py: run_dream_cycle`
- **14.2 Auto dream** — Entry: `co_cli/main.py: _maybe_run_dream_cycle`
- **14.3 Dream phase 1 transcript mining** — Entry: `co_cli/memory/dream.py: _mine_transcripts`
- **14.8 Restore archived artifact** — Entry: `co_cli/memory/archive.py: restore_artifact`
