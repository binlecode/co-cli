# REPORT-test-hygiene-20260512-000000

## Meta
- Scan path: tests/
- Files: 50
- Started: 2026-05-12T00:00:00
- Status: IN PROGRESS

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] workflow registry loaded (agent_docs/system-workflows-to-test.md)
- [x] file list enumerated (50 files)
- [x] conftest.py — neutral pytest plumbing only
- [x] _settings.py — SETTINGS, SETTINGS_NO_MCP, make_settings()
- [x] _timeouts.py — LLM_NON_REASONING_TIMEOUT_SECS=10, LLM_COMPACTION_SUMMARY_TIMEOUT_SECS=60, LLM_REASONING_TIMEOUT_SECS=30, LLM_TOOL_CONTEXT_TIMEOUT_SECS=50, HTTP_HEALTH_TIMEOUT_SECS=15, FILE_DB_TIMEOUT_SECS=30
- [x] _ollama.py — ensure_ollama_warm must be outside asyncio.timeout

## Phase 2 — File Read Progress
- [x] tests/test_flow_approval_subject.py — CLEAN (WF 3.4, 16.1, 16.2, 16.3)
- [x] tests/test_flow_artifact_manage.py — CLEAN (WF 12.6)
- [x] tests/test_flow_background_tasks.py — CLEAN (WF 10.3, 2.13)
- [x] tests/test_flow_bootstrap_budget_span.py — CLEAN (WF 17.4)
- [x] tests/test_flow_bootstrap_canon.py — CLEAN (WF 1.5)
- [x] tests/test_flow_bootstrap_config_loading.py — CLEAN (WF 1.1)
- [x] tests/test_flow_bootstrap_ollama_num_ctx.py — CLEAN (WF 1.2)
- [x] tests/test_flow_capability_checks.py — CLEAN (WF 1.10)
- [x] tests/test_flow_chat_loop.py — CLEAN (WF 2.1)
- [x] tests/test_flow_compaction_boundaries.py — CLEAN (WF 6.3)
- [x] tests/test_flow_compaction_enforce_request_size.py — CLEAN (WF 5.3)
- [x] tests/test_flow_compaction_history_processors.py — CLEAN (WF 5.1, 5.2, 5.5)
- [x] tests/test_flow_compaction_proactive.py — CLEAN (WF 5.4)
- [x] tests/test_flow_compaction_processor_chain.py — CLEAN (WF 5.4, 6.6)
- [x] tests/test_flow_compaction_recovery.py — CLEAN (WF 3.6)
- [x] tests/test_flow_compaction_session_rewrite.py — CLEAN (WF 3.10, 13.1, 13.2)
- [x] tests/test_flow_compaction_slash_commands.py — CLEAN (WF 2.6, 2.7)
- [x] tests/test_flow_compaction_summarization.py — CLEAN (WF 6.4, 6.5)
- [x] tests/test_flow_delegation_agent.py — CLEAN (WF 10.4)
- [x] tests/test_flow_delegation_discovery.py — CLEAN (WF 10.4, fork_deps)
- [x] tests/test_flow_http_error_classifier.py — CLEAN (WF 3.7 classifier)
- [x] tests/test_flow_knowledge_search.py — CLEAN (WF 12.1-12.5, 12.9)
- [x] tests/test_flow_knowledge_view.py — CLEAN (WF 12.5)
- [x] tests/test_flow_llm_call.py — CLEAN (WF 6.5 llm_call)
- [!] tests/test_flow_mcp_schema.py — _FakeMCPServer hand-assembled (Minor, see Phase 3)
- [x] tests/test_flow_memory_artifacts_waterfall_cap.py — CLEAN (WF 12.3)
- [x] tests/test_flow_memory_canon_recall.py — CLEAN (WF 12.1)
- [x] tests/test_flow_memory_store.py — CLEAN (WF 12.8, 1.4)
- [x] tests/test_flow_memory_write.py — CLEAN (WF 12.6)
- [x] tests/test_flow_observability_redaction.py — CLEAN (WF 17.2)
- [x] tests/test_flow_orchestrate_400_reformulation.py — CLEAN (WF 3.7)
- [x] tests/test_flow_orchestrate_interrupted_turn.py — CLEAN (WF 3.8)
- [x] tests/test_flow_orchestrate_length_retry.py — CLEAN (WF 3.9; model_settings override is legitimate — needed to trigger length-finish failure mode)
- [x] tests/test_flow_prompt_assembly.py — CLEAN (WF 4.1, 4.2, 4.3)
- [x] tests/test_flow_session_persistence.py — CLEAN (WF 1.8, 1.9, 13.1, 13.4)
- [x] tests/test_flow_session_search.py — CLEAN (WF 13.5)
- [x] tests/test_flow_session_view.py — CLEAN (WF 13.3, 13.5)
- [x] tests/test_flow_skill_bundled_library.py — CLEAN (WF 15.2, 15.5 bundled coverage)
- [x] tests/test_flow_skill_index.py — CLEAN (SkillIndex primitive layer)
- [x] tests/test_flow_skill_lint.py — CLEAN (lint_skill R1-R10 failure mode coverage)
- [x] tests/test_flow_skill_manifest.py — CLEAN (WF 4.3 skill manifest)
- [x] tests/test_flow_skill_search.py — CLEAN (WF 15.x skill_search tool)
- [x] tests/test_flow_skills_manage.py — CLEAN (WF 15.5, 15.6)
- [x] tests/test_flow_skills_tools.py — CLEAN (WF 15.4 skill_view)
- [x] tests/test_flow_slash_dispatch.py — CLEAN (WF 2.2, 2.3, 2.4)
- [x] tests/test_flow_spill.py — CLEAN (WF 6.2, 7.3 MCP spill)
- [x] tests/test_flow_tool_call_dedup.py — CLEAN (WF 7.3 dedup)
- [x] tests/test_flow_tool_call_functional.py — CLEAN (WF 3.1, 3.3, 16.2, 16.3; ensure_ollama_warm outside timeout ✓)
- [x] tests/test_flow_tool_call_limit.py — CLEAN (WF 6.1)
- [x] tests/test_flow_tool_call_repair.py — CLEAN (WF 7.3 repair)

## Phase 2.5 — Stale Call Grep
- [x] 0 call sites with stale CoDeps parameters (model_capabilities removed — no references remain; memory_db_path removed from CoDeps — all test usages are on MemoryStore/SkillIndex constructors, correct); probe_ollama_model returns int|None — no tuple unpack in tests.

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/test_flow_mcp_schema.py | 96-109 | Real dependencies only — no fakes (`_FakeMCPServer` hand-assembled) | Minor | ACCEPTED (see Phase 4) |

## Phase 4 — Adversarial Review
- tests/test_flow_mcp_schema.py:96-109 `_FakeMCPServer` — The test exercises MCP schema normalization edge cases (duplicate descriptions, malformed schemas, missing fields). A real MCP server cannot be controlled to emit malformed/missing schema data. Monkeypatching the schema result would be an equivalent fake. The `_FakeMCPServer` is the minimal controllable fixture for this specific failure mode — downgraded to ACCEPTED DEVIATION.
- tests/test_flow_orchestrate_length_retry.py model_settings override — Reviewed. `model_settings` is a legitimate param of `run_turn()`. The override is required to reliably trigger the `finish_reason='length'` failure mode in a test environment. No production config violation — accepted.

## Phase 4.5 — Workflow Coverage
| Workflow | Tests | Failure modes covered | Status | Severity |
|----------|-------|-----------------------|--------|----------|
| 1.1 Settings load | test_flow_bootstrap_config_loading.py | env precedence, invalid config | Covered | — |
| 1.2 Ollama num_ctx probe | test_flow_bootstrap_ollama_num_ctx.py | num_ctx cap | Covered | — |
| 1.3 Knowledge backend degradation | (none direct) | — | Uncovered | Minor |
| 1.4 Knowledge dir sync hash-skip | test_flow_memory_store.py | hash-skip, stale rows | Covered | — |
| 1.5 Canon sync | test_flow_bootstrap_canon.py | no_chunk, whole-body | Covered | — |
| 1.6 Skill loading two-pass | test_flow_skills_manage.py, test_flow_skill_search.py | user overrides bundled | Covered | — |
| 1.7 MCP server discovery | test_flow_mcp_schema.py | schema shape, degradation | Stub-covered (fake server) | Minor |
| 1.8 Session restore | test_flow_session_persistence.py | latest path, empty history | Covered | — |
| 1.9 Session index init | test_flow_session_persistence.py | current session excluded | Covered | — |
| 1.10 Capability discovery | test_flow_capability_checks.py | degradation reporting | Covered | — |
| 2.1 REPL loop | test_flow_chat_loop.py | Ctrl+C, empty input | Covered | — |
| 2.2 Slash dispatch (built-in) | test_flow_slash_dispatch.py (builtin shadow test) | wrong handler | Covered | — |
| 2.3 Slash dispatch (skill) | test_flow_slash_dispatch.py | arg expansion, blocked keys | Covered | — |
| 2.4 Skill env injection + cleanup | test_flow_slash_dispatch.py | env restore, active_skill_name | Covered | — |
| 2.5 /resume | (none) | — | Uncovered | Blocking |
| 2.6 /new and /clear | test_flow_compaction_slash_commands.py | compaction state reset | Covered | — |
| 2.7 /compact | test_flow_compaction_slash_commands.py | marker shape, thrash reset | Covered | — |
| 2.8 /sessions listing | (none) | — | Uncovered | Minor |
| 2.9 /skills family | (none) | — | Uncovered | Minor |
| 2.10 /memory family | (none) | — | Uncovered | Minor |
| 2.11 /approvals view + clear | (none) | — | Uncovered | Minor |
| 2.12 /reasoning mode toggle | (none) | — | Uncovered | Minor |
| 2.13 /background and /tasks | test_flow_background_tasks.py | lifecycle, cancel | Covered | — |
| 2.14 /history | (none) | — | Uncovered | Minor |
| 2.15 /tools listing | (none) | — | Uncovered | Minor |
| 3.1 run_turn | test_flow_tool_call_functional.py | golden path, approval | Covered | — |
| 3.2 Stream segment execution | test_flow_tool_call_functional.py | real streaming | Covered | — |
| 3.3 Tool approval loop | test_flow_tool_call_functional.py | approve/deny paths | Covered | — |
| 3.4 Approval subject resolution | test_flow_approval_subject.py | per tool shape, rule match | Covered | — |
| 3.5 Clarify | (none) | — | Uncovered | Minor |
| 3.6 Context overflow recovery | test_flow_compaction_recovery.py | PATH 1/2, gate | Covered | — |
| 3.7 HTTP 400 reformulation | test_flow_orchestrate_400_reformulation.py | budget decrement | Covered | — |
| 3.8 Interrupt handling | test_flow_orchestrate_interrupted_turn.py | drop trailing, abort marker | Covered | — |
| 3.9 Output limit checks | test_flow_orchestrate_length_retry.py | length finish | Covered | — |
| 3.10 Transcript persistence | test_flow_compaction_session_rewrite.py | rewrite path | Covered | — |
| 3.11 Reasoning display modes | (none) | — | Uncovered | Minor |
| 3.12 Doom-loop injection | (none) | — | Uncovered | Minor |
| 4.1 Static instruction assembly | test_flow_prompt_assembly.py | block order | Covered | — |
| 4.2 Toolset guidance gating | test_flow_prompt_assembly.py | absent/present tool | Covered | — |
| 4.3 Category awareness prompt | test_flow_prompt_assembly.py | deferred categories | Covered | — |
| 4.4 Dynamic instruction layers | (none direct; partially in prompt_assembly) | — | Stub-covered | Minor |
| 5.1 dedup_tool_results | test_flow_compaction_history_processors.py | tail protection | Covered | — |
| 5.2 evict_old_tool_results | test_flow_compaction_history_processors.py | compactable vs not | Covered | — |
| 5.3 enforce_request_size | test_flow_compaction_enforce_request_size.py | largest-first spill | Covered | — |
| 5.4 proactive_window_processor | test_flow_compaction_proactive.py | gates, anti-thrash | Covered | — |
| 5.5 sanitize_surrogate_codepoints | test_flow_compaction_history_processors.py | lone surrogate | Covered | — |
| 6.1 L0 admission cap | test_flow_tool_call_limit.py | reject above cap, reset | Covered | — |
| 6.2 L1 emit-time spill | test_flow_spill.py | threshold, stub shape | Covered | — |
| 6.3 Boundary planner | test_flow_compaction_boundaries.py | multi-turn, None cases | Covered | — |
| 6.4 Marker assembly | test_flow_compaction_summarization.py | has_tail variants | Covered | — |
| 6.5 Summarizer LLM call | test_flow_compaction_summarization.py | carry-forward | Covered | — |
| 6.6 Token estimation | test_flow_compaction_processor_chain.py | max(local,reported) | Covered | — |
| 6.7 Compaction commit | test_flow_compaction_summarization.py | sole writer | Covered | — |
| 7.1 Native tool registration | test_flow_capability_checks.py | tool_index presence | Covered | — |
| 7.2 MCP tool discovery | test_flow_mcp_schema.py | schema merge | Stub-covered | Minor |
| 7.3 Lifecycle hook chain | test_flow_tool_call_dedup.py, test_flow_tool_call_repair.py, test_flow_spill.py | all four hooks | Covered | — |
| 7.4 Sequential locking | (none) | — | Uncovered | Minor |
| 7.5 Read-before-write enforcement | (none) | — | Uncovered | Minor |
| 7.6 Staleness tracking | (none) | — | Uncovered | Minor |
| 8.1-8.5 File tools | (none dedicated) | — | Uncovered | Blocking |
| 9.1-9.2 Web tools | (none; skip when key absent is acceptable) | — | Uncovered | Minor |
| 10.1 Shell command policy | test_flow_tool_call_functional.py (partial) | real approval | Stub-covered | Minor |
| 10.2 code_execute | (none) | — | Uncovered | Minor |
| 10.3 Background tasks | test_flow_background_tasks.py | lifecycle | Covered | — |
| 10.4 Delegation subagents | test_flow_delegation_agent.py, test_flow_delegation_discovery.py | isolation, scope | Covered | — |
| 11.1-11.2 External integrations | (skip when unconfigured — correct) | — | Skipped | — |
| 12.1 memory_search canon pass | test_flow_memory_canon_recall.py, test_flow_knowledge_search.py | full body inline | Covered | — |
| 12.2 memory_search user pass | test_flow_knowledge_search.py | cap, kind isolation | Covered | — |
| 12.3 memory_search waterfall | test_flow_memory_artifacts_waterfall_cap.py | dual cap | Covered | — |
| 12.4 memory_search grep fallback | test_flow_knowledge_search.py | canon exclusion | Covered | — |
| 12.5 memory_search browse mode | test_flow_knowledge_search.py | current-session exclusion | Covered | — |
| 12.6 knowledge_manage | test_flow_artifact_manage.py, test_flow_memory_write.py | all actions | Covered | — |
| 12.8 sync_dir hash-skip | test_flow_memory_store.py | second-pass skip | Covered | — |
| 12.9 skills channel guard | test_flow_knowledge_search.py | tool_error on channel=skills | Covered | — |
| 13.1 Session transcript append | test_flow_session_persistence.py | cursor advance | Covered | — |
| 13.2 Session transcript rewrite | test_flow_compaction_session_rewrite.py | truncate+write | Covered | — |
| 13.3 Session indexing | test_flow_session_view.py | chunk shape | Covered | — |
| 13.4 load_transcript | test_flow_session_persistence.py | malformed skip, 50MB cap | Covered | — |
| 13.5 Sessions channel recall | test_flow_session_search.py | dedup, cap, exclusion | Covered | — |
| 14.1-14.8 Dream cycle | (none) | — | Uncovered | Minor |
| 15.1 Skill containment check | test_flow_skills_manage.py (implicit) | — | Stub-covered | Minor |
| 15.2 Skill security scan | test_flow_skills_manage.py, test_flow_skill_bundled_library.py | destructive rollback | Covered | — |
| 15.3 skills_list model-callable | (none direct) | hidden skill exclusion | Uncovered | Minor |
| 15.4 skill_view | test_flow_skills_tools.py | inline body, plugin prefix, file_path error | Covered | — |
| 15.5 skill_manage write ops | test_flow_skills_manage.py | per action, bundled protection | Covered | — |
| 15.6 skill-env blocked-key filter | test_flow_slash_dispatch.py | PATH blocked | Covered | — |
| 16.1 Approval prompt collection | test_flow_approval_subject.py, test_flow_tool_call_functional.py | y/n/a paths | Covered | — |
| 16.2 Auto-approval via session rules | test_flow_tool_call_functional.py | rule match | Covered | — |
| 16.3 Tool-denial path | test_flow_tool_call_functional.py | denied result | Covered | — |
| 17.1 Span emission | test_flow_tool_call_limit.py (OTEL) | span attributes | Covered | — |
| 17.2 SQLite span exporter + redaction | test_flow_observability_redaction.py | PII redacted | Covered | — |
| 17.3 Trace viewers | (none) | — | Uncovered | Minor |
| 17.4 tool_budget.resolved span | test_flow_bootstrap_budget_span.py | all five attributes | Covered | — |
| 18.1 Personality static assembly | test_flow_prompt_assembly.py (partial) | load + ordering | Stub-covered | Minor |
| 18.2 Personality discovery/validation | (none direct) | — | Uncovered | Minor |

**ESCALATE list:**
- ✗ ESCALATE: WF 2.5 (/resume) — Uncovered. Entry: co_cli/commands/resume.py:_cmd_resume. Failure modes: transcript silently truncated, 50MB cap not enforced, session_path not updated. Blocking (user-facing REPL command).
- ✗ ESCALATE: WF 8.1-8.5 (File tools: file_find, file_read, file_write, file_patch, file_search) — No dedicated tests. Real filesystem operations are only driven incidentally via LLM functional tests. Blocking (primary user-facing tool surface).
- ✗ ESCALATE: WF 14.1-14.8 (Dream cycle) — Uncovered. Complex multi-phase user-facing memory consolidation. Minor (not gating chat turn, triggered async).
- ✗ ESCALATE: WF 15.3 (skills_list tool) — Uncovered. Entry: co_cli/tools/system/skills.py:skills_list. Failure modes: hidden skills surfaced, blank-description surfaced. Minor.
- Minor uncovered (REPL commands, internal mechanisms): WF 2.8-2.12, 2.14-2.15, 3.11-3.12, 4.4, 7.4-7.6, 9.x, 10.1-10.2, 15.1, 17.3, 18.1-18.2 — all Minor; not auto-fixed (creative test writing out of scope).

## Phase 4.6 — Consolidation Plan
| Workflow group | Current files | Target file | Test moves |
|----------------|---------------|-------------|------------|
| No consolidations needed | — | — | File-to-workflow mapping is already clean; no misplacements or cross-file overlaps found |

## Phase 5 — Fixes Applied
| File | Test | Rule / Action | Status |
|------|------|---------------|--------|
| (none) | (none) | No Blocking violations found; Minor finding adversarially reviewed → accepted deviation; no consolidations needed | — |

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Log: .pytest-logs/20260512-134255-test-hygiene.log
- Result: 356 passed, 0 failed (3m 15s)

## Phase 7 — Final Verdict
CLEAN — no blocking violations, no failing tests.

**Suite health**: 50 files, 356 tests — all pass.

**What was fixed this run (CoDeps cleanup, preceding the hygiene scan):**
- `workspace_dir` default changed from `~/.co-cli/workspace` → `Path.cwd()` — co is a personal agent, not a code-repo tool
- `memory_db_path` removed from `CoDeps` — implementation detail of `MemoryStore`, not a runtime field
- `model_capabilities` removed from `CoDeps` — never read; capability-conditional gating was never implemented; over-impl
- `probe_ollama_model` returns `int | None` (num_ctx only); `_probe_model_ctx` returns `int` only
- `WORKSPACE_DIR` constant removed from `co_cli/config/core.py`

**Phase 2.5 stale call audit**: 0 stale parameter calls found after CoDeps field removals.

**Violations found**: 1 Minor (test_flow_mcp_schema.py `_FakeMCPServer`) → adversarially reviewed → ACCEPTED DEVIATION (no practical real alternative for malformed-schema test data).

**Coverage gaps escalated (not auto-fixed — require new test authoring):**
- WF 2.5 /resume — Blocking (user-facing REPL)
- WF 8.1-8.5 File tools — Blocking (primary tool surface; only incidental coverage via LLM functional tests)
- WF 14.1-14.8 Dream cycle — Minor
- WF 15.3 skills_list tool — Minor
- Various slash commands (WF 2.8-2.15), internal mechanisms (WF 3.11-3.12, 7.4-7.6), personality (WF 18.x) — Minor
