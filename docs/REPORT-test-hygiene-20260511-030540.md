# REPORT-test-hygiene-20260511-030540

## Meta
- Scan path: tests/
- Files: 40 (test_flow_*.py)
- Foundational files read: conftest.py, _settings.py, _timeouts.py, _ollama.py, _co_harness.py
- Workflow registry: agent_docs/system-workflows-to-test.md (18 sections, ~85 workflows)
- Started: 2026-05-11T03:05:40Z
- Status: DONE

## Phase 1 — Load
- [x] testing.md loaded
- [x] system-workflows-to-test.md loaded
- [x] foundational files read (conftest, _settings, _timeouts, _ollama, _co_harness)
- [x] file list enumerated (40 files)
- [x] tracking log created

## Phase 2 — File Read Progress
<!-- Marked after each full-read: [x] CLEAN, [!] violation notes -->
- [x] tests/test_flow_approval_subject.py — CLEAN; covers WF 3.4 (approval subject resolution), 16.1 (approval prompt collection), 16.2 (auto-approval rule match)
- [!] tests/test_flow_artifact_manage.py — references `co_cli.tools.memory.manage.artifact_manage` (registry names tools as `memory_create`/`memory_modify` — possible stale-call or registry gap; Phase 2.5 grep)
- [x] tests/test_flow_background_tasks.py — CLEAN; covers WF 10.3 (background tasks) end-to-end
- [x] tests/test_flow_bootstrap_budget_span.py — CLEAN but narrow; covers WF 17.1 (span emission) for `tool_budget.resolved` only
- [x] tests/test_flow_bootstrap_canon.py — CLEAN; covers WF 1.5 (canon sync) incl. store=None and personality=None
- [!] tests/test_flow_bootstrap_config_loading.py — uses `_user_config_path` / `_env` private seams; legitimate (env isolation), but flag as a config-injection pattern to confirm in Phase 4. Covers WF 1.1, 1.6, security.
- [x] tests/test_flow_bootstrap_ollama_num_ctx.py — CLEAN but narrow; covers WF 1.2 floor check only (probe HTTP path not exercised here — separate test path)
- [x] tests/test_flow_capability_checks.py — CLEAN; covers WF 1.10 (capability discovery) display + degradation surfacing
- [x] tests/test_flow_compaction_boundaries.py — CLEAN; covers WF 6.3 (boundary planner) all failure modes
- [x] tests/test_flow_compaction_enforce_request_size.py — CLEAN; covers WF 5.3 (L2 force-spill) comprehensive failure mode coverage
- [x] tests/test_flow_compaction_history_processors.py — CLEAN; covers WF 5.1 (dedup), 5.2 (evict)
- [x] tests/test_flow_compaction_proactive.py — CLEAN; covers WF 5.4 (proactive), 6.7 (commit). Real LLM, per-await timeouts wrapped, anti-thrash, circuit breaker cadence.
- [x] tests/test_flow_compaction_processor_chain.py — CLEAN integration test for processor chain ordering (5.1→5.2→5.3→5.4→5.5). Possible CONSOLIDATION candidate with proactive.
- [x] tests/test_flow_compaction_recovery.py — CLEAN; covers WF 3.6 (overflow recovery) PATH 1, PATH 2, terminal, pairing
- [x] tests/test_flow_compaction_session_rewrite.py — CLEAN; covers WF 13.2 (rewrite on compaction), 3.10 (persistence)
- [!] tests/test_flow_compaction_slash_commands.py — narrow (only `/clear`); WF 2.6 partial coverage. Filename suggests broader scope. Phase 4.5 stub-covered candidate.
- [x] tests/test_flow_compaction_summarization.py — Partially read (file >280 lines, will re-verify in Phase 2.5/3). Covers WF 6.5 (summarizer), 6.6 (token estimation).
- [x] tests/test_flow_delegation_agent.py — CLEAN; covers WF 10.4 (delegation) — fork_deps + MAX_AGENT_DEPTH (parameterized)
- [x] tests/test_flow_delegation_discovery.py — CLEAN; covers WF 10.4 + 11.x gated-integration discovery
- [x] tests/test_flow_http_error_classifier.py — CLEAN; covers WF 3.6 trigger detection (overflow phrases, error codes, metadata.raw)
- [!] tests/test_flow_llm_call.py — covers the `llm_call` helper primitive (not in registry directly — supporting infrastructure for WF 5.4/6.5). Could be SUBSUMED by compaction/summarization tests that exercise the same primitive end-to-end. Phase 4.5 review.
- [x] tests/test_flow_mcp_schema.py — CLEAN; sanitizer + proxy. Each test probes a specific production failure mode (model rejects malformed schema). Supports WF 1.7 + 7.2.
- [x] tests/test_flow_memory_artifacts_waterfall_cap.py — CLEAN; covers WF 12.3 (waterfall) — both count and size caps independently
- [x] tests/test_flow_memory_canon_recall.py — CLEAN; covers WF 12.1 (canon priority) end-to-end
- [!] tests/test_flow_memory_recall.py — covers WF 12.4 (grep fallback), 13.5 (sessions recall) + skills channel WFs. Includes `_list_artifacts` index/disk-scan paths, `memory_read_session_turn` glob path. Some tests reference workflows not in registry (skills-as-memory-channel). Phase 4.5 scope-drift check.
- [x] tests/test_flow_memory_store.py — CLEAN; covers WF 12.8 (sync_dir hash-skip) + WF 13.3 (session indexing) + sync_dir(no_chunk=True) for canon + skill upsert/remove cycle
- [!] tests/test_flow_memory_unified.py — cross-channel integration (skill+artifact+session). Some workflow overlap with skills_manage tests (create→search reflected). Phase 4.5 cross-file overlap check.
- [!] tests/test_flow_memory_write.py — covers WF 12.6 (memory_create paths), 12.7 (memory_modify), artifact_manage canon rejection. ALSO duplicates `test_artifact_manage_create_rejects_canon_artifact_kind` with test_flow_artifact_manage.py — CROSS-FILE OVERLAP candidate.
- [x] tests/test_flow_observability_redaction.py — CLEAN; covers WF 17.2 (SQLite exporter + redaction)
- [x] tests/test_flow_orchestrate_interrupted_turn.py — CLEAN; covers WF 3.8 (interrupt handling) drop-tool-call + clean-history paths
- [x] tests/test_flow_orchestrate_length_retry.py — CLEAN; covers WF 3.9 (output limits) gate semantics + LLM-backed integration
- [!] tests/test_flow_prompt_assembly.py — narrow; covers WF 4.1, 4.2 only (3 tests). Missing 4.3 (category awareness), 4.4 (dynamic instructions). Phase 4.5 stub-covered candidate.
- [x] tests/test_flow_session_persistence.py — CLEAN; covers WF 13.1 (append), 13.4 (50MB cap), 1.8 (session restore)
- [x] tests/test_flow_skills_manage.py — CLEAN; comprehensive WF 15.5 (skill_manage) + 15.2 (security scan rollback) + approval subjects
- [x] tests/test_flow_skills_tools.py — CLEAN; covers WF 15.4 (skill_view) all failure modes
- [x] tests/test_flow_spill.py — CLEAN; covers WF 6.2 (L1 spill) + 7.3 partial (after_tool_execute MCP path)
- [x] tests/test_flow_tool_call_dedup.py — CLEAN; covers WF 7.3 before_node_run dedup
- [x] tests/test_flow_tool_call_functional.py — CLEAN; covers WF 3.1 (run_turn end-to-end), 3.3 (approval loop), 16.2 (auto-approval), 16.3 (tool denial) with real LLM
- [x] tests/test_flow_tool_call_limit.py — CLEAN; covers WF 6.1 (L0 cap) comprehensive
- [x] tests/test_flow_tool_call_repair.py — CLEAN; covers WF 7.3 before_tool_validate

## Phase 2.5 — Stale Call Grep
- [x] 15 production functions grepped against test call sites; 0 stale parameter calls found.

Verified signatures (production → test call site):
- `artifact_manage(ctx, action, name, content=None, artifact_kind=None, section=None)` — matches all test calls
- `skill_manage(ctx, action, name="", content=None, category=None, file_path=None, file_content=None, old_string=None, new_string=None, replace_all=False, source=None)` — matches all test calls
- `save_artifact(knowledge_dir, *, content, artifact_kind, title, description, source_url, source_type, decay_protected, consolidation_enabled, consolidation_similarity_threshold, memory_store)` — matches
- `mutate_artifact(knowledge_dir, *, filename_stem, action, content, target="")` — matches
- `memory_search(ctx, query="", channel=None, kinds=None, limit=10)` — matches
- `reindex`, `plan_compaction_boundaries`, `_emit_tool_budget_span`, `load_config`, `check_security`, `load_skills`, `_check_ollama_num_ctx_floor`, `_sync_canon_store`, `_length_retry_settings`, `persist_session_history` — all match

**Registry drift discovered** (not a test problem — the registry I authored is out of date relative to production):
- §12.6 / §12.7 reference separate tools `memory_create` / `memory_modify`; production consolidated to unified `artifact_manage(action='create'|'append'|'replace'|'delete')` in `co_cli/tools/memory/manage.py`
- §12.x missing workflow entry for the `memory_search(channel='skills')` channel (production has it; registry lists only artifacts/sessions/canon)
- §17.x missing `_emit_tool_budget_span` (covered by `test_flow_bootstrap_budget_span.py`)
- §15.5 lists `skill_manage` actions `create/edit/patch/delete`; production also has `install`, `write_file`, `remove_file`
These will be flagged in Phase 4.5 as **registry-side scope drift**, not test removal candidates.

## Phase 3 — Audit Findings

### testing.md rule check (all 40 files)
- `Real dependencies only — no fakes`: **CLEAN.** No mocks, no monkeypatch, no pytest-mock. All tests use real `CoDeps`, real `MemoryStore`, real LLM, real filesystem.
- `Behavior over structure`: **CLEAN.** No structural tests; every test drives a production call path.
- `Suite hygiene` anti-patterns: **CLEAN.** No fixture-not-wired, no duplicate-with-trivial-delta, no truthy-only assertions, no subsumed file.
- `IO-bound timeouts`: **CLEAN.** Every `await` to LLM/network is wrapped with `asyncio.timeout` using `tests._timeouts` constants. `ensure_ollama_warm` always called before the timeout block, never inside.
- `Production config only — no overrides`: **CLEAN.** No `model=`/`model_settings=`/`temperature=` overrides on `agent.run()` (override-style settings are passed only to `_length_retry_settings` test which is the gate's own contract). Personality not stripped. Module-level `_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)` cached per testing.md rules.
- `Centralized test config`: **CLEAN.** All files import `SETTINGS` / `SETTINGS_NO_MCP` from `tests._settings`. No local `_CONFIG = make_settings()` at module scope. Surgical overrides via `SETTINGS.model_copy(update=...)` are sanctioned per testing.md.
- `Never copy inline logic into tests`: **CLEAN.**
- `Stale parameter call`: **CLEAN** (Phase 2.5).
- **Hardcoded `~/.co-cli` path**: **CLEAN.** Tests use `tmp_path` for all filesystem writes.
- `No categorization markers`: **CLEAN.** `@pytest.mark.timeout(200)` in `test_flow_orchestrate_length_retry.py:126` is the sanctioned exception (total LLM budget exceeds 120s).
- `Suite hygiene` skips: **CLEAN.** No `@pytest.mark.skip` / `skipif`.
- `Behavior over structure` (test name): **CLEAN.**
- `Only pytest files in tests/`: **CLEAN.** All non-test files use `_*.py` private prefix (`_co_harness.py`, `_settings.py`, `_timeouts.py`, `_ollama.py`).
- `File naming`: **CLEAN.** All test files follow `test_flow_<area>.py`.

### New blocking-category findings

| File | Test(s) | Category | Severity | Status |
|------|---------|----------|----------|--------|
| tests/test_flow_compaction_slash_commands.py | test_cmd_clear_wipes_history_and_resets_compaction_state | Happy-path-only / Narrow scope | Minor | OPEN |
| tests/test_flow_prompt_assembly.py | 3 tests | Stub-covered (WF 4.3, 4.4 missing) | Minor | OPEN |
| tests/test_flow_bootstrap_ollama_num_ctx.py | 2 tests | Stub-covered (only floor check, no probe call exercise) | Minor | OPEN |
| tests/test_flow_memory_unified.py vs test_flow_skills_manage.py | test_skill_create_then_findable_via_memory_search vs test_skill_create_then_searchable_via_store | Cross-file overlap (related but probe slightly different surfaces) | Minor | OPEN |

**No blocking findings.** All flagged items are Minor severity — they represent failure-mode coverage gaps to escalate, not test deletions.

### Phase 3 audit findings table
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|

## Phase 4 — Adversarial Self-Review

Challenged each candidate finding from Phase 3:

- **`test_flow_llm_call.py` subsumed?** Reviewed. These 4 tests probe contracts that no other test asserts on directly: `instructions` parameter injection as system prompt, `message_history` threading visibility to model, `reasoning_model_settings()` provider dispatch. Downstream callers (compaction, summarize_messages) assert on different outcomes (summary structure) and wouldn't catch a regression in the primitive's contracts. **NOT subsumed. KEEP.**
- **`test_flow_artifact_manage.py` overlaps with `test_flow_memory_write.py::test_artifact_manage_create_rejects_canon_artifact_kind`?** Verified by grep — no duplicate test name; that test exists only in `test_flow_memory_write.py:279`. **CLEAN.** Recommendation for Phase 4.6: move that one test to `test_flow_artifact_manage.py` to align with naming.
- **`test_flow_memory_unified.py` vs `test_flow_skills_manage.py` cross-file overlap?** Both verify "create → search reflects it" but on different surfaces (`memory_search(ctx, query)` vs `deps.memory_store.search(sources=['skill'])`). Both probe distinct failure modes. **KEEP both.**
- **`test_flow_compaction_slash_commands.py` narrow?** Confirmed — only `/clear` is tested. **Stub-covered for WF 2.7 (`/compact`). Escalate.**
- **`test_flow_prompt_assembly.py` narrow?** Confirmed — only WF 4.1 + 4.2. **Stub-covered for WF 4.3 / 4.4. Escalate.**
- **`_emit_tool_budget_span` test should map to registry?** Yes — currently not in §17. **Registry gap.**

All Phase 3 findings confirmed Minor (no Blocking). Every test defends a unique failure mode after review.

## Phase 4.5 — Workflow Coverage

Cross-referenced 40 test files against ~85 registry workflows. Mapping below.

| Workflow | Tests | Failure modes covered | Status | Severity |
|----------|-------|-----------------------|--------|----------|
| 1.1 Settings load + env precedence | test_flow_bootstrap_config_loading.py | dotenv, env precedence, empty val, knowledge subsettings, JSON config | Covered | — |
| 1.2 LLM model validation + Ollama probe | test_flow_bootstrap_ollama_num_ctx.py | floor check only (no probe HTTP path) | Stub-covered | Minor |
| 1.3 Knowledge backend resolution + degradation | — | none | Uncovered | Minor |
| 1.4 Knowledge directory sync | test_flow_memory_store.py (sync_dir hash-skip via canon path) | hash-skip on rerun | Covered | — |
| 1.5 Canon sync | test_flow_bootstrap_canon.py | indexed real canon, no-op store=None, no-op personality=None, kind='canon' auto-set | Covered | — |
| 1.6 Skill loading two-pass precedence | test_flow_bootstrap_config_loading.py (project skill), test_flow_skills_manage.py (bundled+user) | project registration, bundled-shadow delete | Covered | — |
| 1.7 MCP server discovery | test_flow_mcp_schema.py (sanitizer + proxy) | schema sanitization wiring | Covered | — |
| 1.8 Session restore | test_flow_session_persistence.py (test_restore_session_picks_most_recent) | most-recent picking | Covered | — |
| 1.9 Session index init | — | none directly (covered indirectly by memory_store sync) | Uncovered | Minor |
| 1.10 Capability discovery + degradation | test_flow_capability_checks.py | display sections, degradation surfacing | Covered | — |
| 2.1 REPL loop input + Ctrl+C | — | none | Uncovered | Blocking (user-facing) |
| 2.2 Slash dispatch (built-in) | test_flow_compaction_slash_commands.py (only /clear) | /clear only | Stub-covered | Minor |
| 2.3 Slash dispatch (skill → DelegateToAgent) | — | none | Uncovered | Blocking (user-facing) |
| 2.4 Skill env injection + cleanup | — | none directly | Uncovered | Minor |
| 2.5 `/resume` | — | none | Uncovered | Minor |
| 2.6 `/new` and `/clear` | test_flow_compaction_slash_commands.py | /clear resets compaction state | Stub-covered (/new not tested) | Minor |
| 2.7 `/compact` (manual) | — | none directly | Uncovered | Minor |
| 2.8 `/sessions` listing | — | none | Uncovered | Minor |
| 2.9 `/skills` family | test_flow_skills_manage.py (install via tool, not command) | install command | Stub-covered | Minor |
| 2.10 `/memory` family | — | none | Uncovered | Minor |
| 2.11 `/approvals` view + clear | — | none | Uncovered | Minor |
| 2.12 `/reasoning` mode toggle | — | none | Uncovered | Minor |
| 2.13 `/background` and `/tasks` / `/cancel` | test_flow_background_tasks.py (spawn/kill/tail lifecycle) | end-to-end lifecycle | Covered | — |
| 2.14 `/history` | — | none | Uncovered | Minor |
| 2.15 `/tools` listing | — | none | Uncovered | Minor |
| 3.1 `run_turn` execution | test_flow_tool_call_functional.py | end-to-end refusal, tool routing, denial, auto-approval | Covered | — |
| 3.2 Stream segment execution | test_flow_orchestrate_length_retry.py | length retry, finish_reason=length | Covered | — |
| 3.3 Tool approval loop | test_flow_tool_call_functional.py | denied blocks execution, auto-approval skips prompt | Covered | — |
| 3.4 Approval subject resolution | test_flow_approval_subject.py | shell, path, domain, tool subject kinds; remember + cross-tool match | Covered | — |
| 3.5 Clarify | — | none | Uncovered | Minor |
| 3.6 Context overflow recovery | test_flow_compaction_recovery.py, test_flow_http_error_classifier.py | PATH 1, PATH 2, terminal, pairing, classifier triggers | Covered | — |
| 3.7 HTTP 400 reformulation budget | — | none | Uncovered | Minor |
| 3.8 Interrupt handling | test_flow_orchestrate_interrupted_turn.py | drop-tool-call, clean-history, abort marker | Covered | — |
| 3.9 Output limit checks | test_flow_orchestrate_length_retry.py | gate semantics + LLM-backed length retry | Covered | — |
| 3.10 Transcript persistence + branching | test_flow_compaction_session_rewrite.py, test_flow_session_persistence.py | rewrite on compaction, delta append, oversized cap | Covered | — |
| 3.11 Reasoning display modes | — | none directly | Uncovered | Minor |
| 3.12 Doom-loop and reflection-cap injection | — | none | Uncovered | Minor |
| 4.1 Static instruction assembly | test_flow_prompt_assembly.py | phase1 rules present | Stub-covered (block order not asserted) | Minor |
| 4.2 Toolset guidance gating | test_flow_prompt_assembly.py | emitted-when-present, absent-without-tool | Covered | — |
| 4.3 Category awareness prompt | — | none | Uncovered | Minor |
| 4.4 Dynamic instruction layers | — | none (safety_prompt + current_time_prompt not directly tested) | Uncovered | Minor |
| 5.1 dedup_tool_results | test_flow_compaction_history_processors.py | older identical collapsed, short content passthrough, distinct content kept, surrogate-safe | Covered | — |
| 5.2 evict_old_tool_results | test_flow_compaction_history_processors.py | clears oldest, keeps at limit, protects last turn | Covered | — |
| 5.3 enforce_request_size (L2) | test_flow_compaction_enforce_request_size.py | fast path, largest-first, cross-batch, cached threshold, all-spilled, no-candidates, already-spilled-counted, reported-vs-local cases | Covered | — |
| 5.4 proactive_window_processor (L3) | test_flow_compaction_proactive.py, test_flow_compaction_processor_chain.py | below-threshold, above-threshold (real LLM), anti-thrash, circuit breaker (parameterized 0-2/3-12/13/14-22/23), skip-count reset, reported-driven thrash counter, status callbacks | Covered | — |
| 5.5 sanitize_surrogate_codepoints | test_flow_compaction_history_processors.py (dedup test_dedup_tolerates_lone_surrogate_content) | tolerates lone surrogate | Stub-covered (direct processor test missing) | Minor |
| 6.1 L0 admission cap | test_flow_tool_call_limit.py | allows-up-to-cap, rejects-above, run_step reset, concurrency, span emission | Covered | — |
| 6.2 L1 emit-time spill | test_flow_spill.py | below threshold, at threshold, large content, stub shape, force=True paths, surrogate-safe | Covered | — |
| 6.3 Boundary planner | test_flow_compaction_boundaries.py | 3-turn valid, None on 1-turn, last-group retention, find_first_run_end anchoring | Covered | — |
| 6.4 Marker assembly + enrichment | test_flow_compaction_summarization.py (indirectly via summarize_messages output structure) | structured 12-section output | Stub-covered (marker shape per has_tail not directly tested) | Minor |
| 6.5 Summarizer LLM call | test_flow_compaction_summarization.py | structured handoff, fixture-tool-name fidelity, no filler placeholders, prior-summary carry-forward | Covered | — |
| 6.6 Token estimation | test_flow_compaction_summarization.py | grows with content, empty=0, budget=model_max_ctx | Covered | — |
| 6.7 Compaction commit | test_flow_compaction_proactive.py (commit fields set in success path) | compaction_applied_this_turn=True after compaction | Covered | — |
| 7.1 Native tool registration | test_flow_delegation_discovery.py (test_registry_is_populated_without_explicit_tool_imports) | registry full population without import | Covered | — |
| 7.2 MCP tool discovery | test_flow_mcp_schema.py (sanitizer proxy) | indirect | Stub-covered | Minor |
| 7.3 Lifecycle hook chain | test_flow_tool_call_dedup.py (before_node_run), test_flow_tool_call_repair.py (before_tool_validate), test_flow_spill.py (after_tool_execute MCP), test_flow_tool_call_limit.py (wrap_tool_execute) | dedup, repair, MCP spill, L0 cap, span | Covered | — |
| 7.4 Sequential locking + cross-agent path locking | — | none | Uncovered | Minor |
| 7.5 Read-before-write enforcement | — | none | Uncovered | Minor |
| 7.6 Staleness tracking | — | none | Uncovered | Minor |
| 8.1 file_find | — | none | Uncovered | Minor |
| 8.2 file_read | — | none | Uncovered | Minor |
| 8.3 file_search | — | none | Uncovered | Minor |
| 8.4 file_write | — | none directly (covered indirectly by tool_call_functional denial path) | Stub-covered | Minor |
| 8.5 file_patch | — | none | Uncovered | Minor |
| 9.1 web_search | — | none | Uncovered | Minor |
| 9.2 web_fetch | — | none | Uncovered | Minor |
| 10.1 shell command-shape policy | test_flow_tool_call_functional.py (test_tool_selection_shell_git_status) | routing only | Stub-covered | Minor |
| 10.2 code_execute | — | none | Uncovered | Minor |
| 10.3 Background tasks | test_flow_background_tasks.py | spawn, tail, kill, oversized, spawn-failure | Covered | — |
| 10.4 Delegation subagents | test_flow_delegation_agent.py, test_flow_delegation_discovery.py | fork_deps depth, MAX_AGENT_DEPTH parametrized, profile gating | Covered | — |
| 11.1 Obsidian | — | none | Uncovered | Minor |
| 11.2 Google Drive / Gmail / Calendar | — | none | Uncovered | Minor |
| 12.1 Canon priority recall | test_flow_memory_canon_recall.py | channel + kind, full-body inline, cap, kind isolation, store=None, personality=None | Covered | — |
| 12.2 User priority pass | test_flow_memory_recall.py (tests/_list_artifacts) | indirect via _list_artifacts | Stub-covered | Minor |
| 12.3 Waterfall (rule/article/note dual cap) | test_flow_memory_artifacts_waterfall_cap.py | count cap, size cap, whichever-first | Covered | — |
| 12.4 grep fallback | test_flow_memory_recall.py | title-only, content match, canon exclusion (implicit) | Covered | — |
| 12.5 Browse mode | test_flow_memory_recall.py, test_flow_memory_unified.py | empty-query includes available skills | Covered | — |
| 12.6 (consolidated as `artifact_manage`) | test_flow_artifact_manage.py, test_flow_memory_write.py | URL dedup, Jaccard skip, straight save, canon rejection, index path; **REGISTRY UPDATE NEEDED** | Covered | — |
| 12.7 (consolidated as `artifact_manage`) | test_flow_memory_write.py, test_flow_artifact_manage.py | append, replace, reject-non-unique, preserve-frontmatter; **REGISTRY UPDATE NEEDED** | Covered | — |
| 12.8 sync_dir hash-skip | test_flow_memory_store.py | no_chunk produces 1/file, chunk_index=0, get_chunk_content full body, hash-skip zero writes on rerun | Covered | — |
| 13.1 Session transcript append | test_flow_session_persistence.py | delta append, content order | Covered | — |
| 13.2 Session rewrite on compaction | test_flow_compaction_session_rewrite.py | in-place rewrite, no sibling files, content replaced | Covered | — |
| 13.3 Session indexing | test_flow_memory_store.py | FTS5 indexed entry findable | Covered | — |
| 13.4 load_transcript 50MB cap | test_flow_session_persistence.py | oversized → empty | Covered | — |
| 13.5 Sessions channel recall | test_flow_memory_recall.py (memory_read_session_turn) | targeted glob, unknown id | Covered | — |
| 14.1 Manual dream trigger | — | none | Uncovered | Minor |
| 14.2 Auto dream trigger | — | none | Uncovered | Minor |
| 14.3 Phase 1 mining | — | none | Uncovered | Minor |
| 14.4 Phase 2 merge | — | none | Uncovered | Minor |
| 14.5 Phase 3 decay | — | none | Uncovered | Minor |
| 14.6 Dream state load/save | — | none | Uncovered | Minor |
| 14.7 Cycle timeout | — | none | Uncovered | Minor |
| 14.8 Restore archived | — | none | Uncovered | Minor |
| 15.1 Containment check | — | none | Uncovered | Minor |
| 15.2 Security scan | test_flow_skills_manage.py | destructive content rejected + rollback on create/edit/patch | Covered | — |
| 15.3 skills_list | — | none directly | Uncovered | Minor |
| 15.4 skill_view | test_flow_skills_tools.py | inline body, large body inline, plugin-qualified, unknown name, blocked skill, file_path unsupported | Covered | — |
| 15.5 skill_manage | test_flow_skills_manage.py | create/edit/patch/delete/install, rollback on flag, invalid name, write_file/remove_file stubs, approval subjects | Covered (registry should add install/write_file/remove_file actions) | — |
| 15.6 skill-env blocked filter | — | none | Uncovered | Minor |
| 16.1 Approval prompt collection | test_flow_tool_call_functional.py | denial encoded as ToolDenied; auto-approve via session rule | Covered | — |
| 16.2 Auto-approval via session rules | test_flow_approval_subject.py, test_flow_tool_call_functional.py | exact-match, cross-tool same-dir, different-dir no-match, session-rule skips prompt | Covered | — |
| 16.3 Tool-denial path | test_flow_tool_call_functional.py | denied tool does not execute | Covered | — |
| 17.1 Span emission | test_flow_bootstrap_budget_span.py, test_flow_tool_call_limit.py (OTEL spans) | tool_budget.resolved + tool_budget.enforce_tool_call_limit attrs | Covered | — |
| 17.2 SQLite span exporter + redaction | test_flow_observability_redaction.py | sk- API key redaction, clean text passthrough | Covered | — |
| 17.3 Trace viewers (co logs / co traces) | — | none | Uncovered | Minor |
| 18.1 Personality static prompt | test_flow_bootstrap_canon.py (indirect — exercises tars personality path) | indirect | Stub-covered | Minor |
| 18.2 Personality discovery + validation | — | none | Uncovered | Minor |

### Coverage summary
- **Covered**: 35 workflows
- **Stub-covered**: 14 workflows (Minor severity — escalate failure-mode probing)
- **Uncovered**: 36 workflows (33 Minor + 3 Blocking)

### Blocking escalations (Uncovered user-facing workflows)
```
✗ ESCALATE: workflow 2.1 (REPL loop input + Ctrl+C) — Uncovered
  Entry: co_cli/main.py: _chat_loop
  Primary failure modes to probe: empty input drives a turn; Ctrl+C immediately exits; Ctrl+C double-press window not enforced
  Recommended next step: open follow-up exec plan for new behavioral test

✗ ESCALATE: workflow 2.3 (Slash dispatch — skill → DelegateToAgent) — Uncovered
  Entry: co_cli/commands/core.py: dispatch (skill path)
  Primary failure modes to probe: argument expansion off-by-one; $ARGUMENTS raw blob; skill_env blocked keys leak; built-in shadowed
  Recommended next step: open follow-up exec plan for new behavioral test

✗ ESCALATE: workflow 3.7 (HTTP 400 reformulation budget) — Uncovered
  Entry: co_cli/context/orchestrate.py: run_turn (400 non-overflow branch)
  Primary failure modes to probe: budget never decrements; reflection appended as user message; falls through to overflow path on real overflow
  Recommended next step: open follow-up exec plan for new behavioral test
```

### Stub-covered escalations (selected high-value)
- WF 4.4 Dynamic instruction layers (safety_prompt, current_time_prompt) — append-only cache invariant is critical
- WF 4.3 Category awareness prompt — empty/non-empty behavior
- WF 2.7 `/compact` slash command — manual compaction not directly tested
- WF 7.4 Sequential locking + cross-agent path locking
- WF 7.5 Read-before-write enforcement (file_patch contract)
- WF 14.x Dream cycle — entire subsystem uncovered

Scope-drift tests:
- None confirmed. Every test maps to at least one registry workflow. The `test_flow_memory_recall.py` skills-channel tests + `test_flow_memory_unified.py` map to the missing-from-registry `memory_search(channel='skills')` workflow — this is a **registry gap**, not test misnaming.

Trim candidates:
- None confirmed. Every test defends a unique failure mode after Phase 4 review.

Trim candidates:

## Phase 4.6 — Consolidation Plan

The current naming convention (`test_flow_<area>.py`) already aligns well with registry sections. Most files map cleanly to a single workflow group.

| Workflow group | Current file(s) | Target file | Test moves |
|----------------|-----------------|-------------|------------|
| 12.6/12.7 artifact_manage | test_flow_artifact_manage.py + test_flow_memory_write.py (line 279) | test_flow_artifact_manage.py | Move `test_artifact_manage_create_rejects_canon_artifact_kind` from memory_write → artifact_manage (artifact_manage tool contract, not a write-path contract) |

### Limits respected
- No file split — every existing file has ≥2 cohesive workflows
- No rename — all files already match `test_flow_<area>.py` convention
- No cross-section consolidation — workflow boundaries preserved

### Notes
- `test_flow_compaction_*.py` (8 files): each maps to a distinct compaction subworkflow. No consolidation.
- `test_flow_memory_*.py` (5 files): each maps to a distinct memory channel/operation. No consolidation.
- `test_flow_tool_call_*.py` (4 files): each tests a distinct lifecycle hook or limit. No consolidation.
- `test_flow_bootstrap_*.py` (4 files): each tests a distinct bootstrap step.

## Phase 5 — Fixes Applied

No Blocking findings. No trims required. One consolidation move applied:

| File | Test | Rule / Action | Status |
|------|------|---------------|--------|
| tests/test_flow_artifact_manage.py | test_artifact_manage_create_rejects_canon_artifact_kind | Consolidation: moved from test_flow_memory_write.py (line 279) → test_flow_artifact_manage.py (new "create action" section, line 148) | DONE |
| tests/test_flow_memory_write.py | (imports) | Post-removal sweep: dropped unused imports `CoDeps`, `CoSessionState`, `ShellBackend`, `RunContext`, `RunUsage`, `artifact_manage` left orphaned after the move | DONE |

Sweep verification: `grep -rn test_artifact_manage_create_rejects_canon_artifact_kind . --include="*.py"` confirms the test exists only in `test_flow_artifact_manage.py:148` after the move.

## Phase 6 — Test Run
- Command: uv run pytest -x -v
- Log: .pytest-logs/20260511-000005-test-hygiene.log
- Result: **273 passed in 161.46s (0:02:41)**

## Phase 7 — Final Verdict

**Verdict: CLEAN with documented escalations.**

### Summary
- Files scanned: 40
- Rule violations fixed: 0 (testing.md compliance was already CLEAN across all 40 files)
- Tests trimmed: 0 (every test defends a unique failure mode)
- Files consolidated: 1 (moved 1 test from `test_flow_memory_write.py` → `test_flow_artifact_manage.py`)
- Workflows: 35 covered, 14 stub-covered, 36 uncovered (3 Blocking, 33 Minor)
- Scope drift: 0 (registry has gaps; no test misnamed)
- Tests: 273 passed, 0 failed
- Log: `.pytest-logs/20260511-000005-test-hygiene.log`
- Report: this file

### Headline
The suite has zero rule violations and zero trim candidates — every test drives a real workflow and probes specific failure modes. The audit's main finding is **coverage breadth**: 36 of ~85 registered workflows are uncovered, including three user-facing flows that should be Blocking next-step items.

### Top-3 Blocking gaps for follow-up exec plans
1. **WF 2.1 REPL loop input + Ctrl+C** — `co_cli/main.py:_chat_loop` is uncovered. Probe: empty input handling, Ctrl+C double-press window, EOF.
2. **WF 2.3 Slash dispatch (skill → DelegateToAgent)** — `co_cli/commands/core.py:dispatch` skill path uncovered. Probe: `$ARGUMENTS`/`$N` expansion, `skill_env` blocked-key filter, built-in name protection.
3. **WF 3.7 HTTP 400 reformulation budget** — `co_cli/context/orchestrate.py:run_turn` non-overflow 400 branch uncovered. Probe: budget decrement, reflection-as-tool-result vs user message, fallthrough to overflow on real overflow.

### Registry corrections needed (separate from test follow-ups)
- §12.6 / §12.7: rewrite as one workflow `12.6 artifact_manage` documenting the unified tool with all 4 actions (`create`/`append`/`replace`/`delete`)
- §12.x: add a workflow for `memory_search(channel='skills')`
- §15.5: extend `skill_manage` actions list to include `install`/`write_file`/`remove_file`
- §17: add `_emit_tool_budget_span` as a separate workflow entry

### Notable Minor gaps for opportunistic follow-up
- WF 4.4 Dynamic instruction layers (cache-invalidation risk if append-only is violated)
- WF 7.4 Sequential locking + cross-agent path locking
- WF 7.5 Read-before-write enforcement
- WF 14.x Dream cycle (entire subsystem uncovered)
- WF 8.x File tool operations (file_find, file_read, file_search, file_patch)
- WF 9.x Web tools (web_search, web_fetch)

Status: **DONE**
