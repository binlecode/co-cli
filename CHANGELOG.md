# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.20] - 2026-04-04

### Changed
- **Tool result foundation**: renamed modules (`_result.py` → `tool_output.py`, `_errors.py` → `tool_errors.py`, `_display_hints.py` → `tool_display.py`, `_tool_approvals.py` → `tool_approvals.py`, `_subagent_agents.py` → `_subagent_builders.py`) and functions (`make_result` → `tool_output`, `terminal_error` → `tool_error`, `format_tool_result_for_display` → `format_for_display`) to reflect cross-package public roles
- **Zero gateway bypasses**: `run_shell_command` and `read_drive_file` now return `ToolResult` via `tool_output()` instead of raw strings — all tool return paths go through the centralized gateway
- **Gateway `ctx` parameter**: `tool_output()`, `tool_error()`, and `handle_google_api_error()` accept optional `RunContext[CoDeps]` for follow-up size control
- **Shell policy consolidation**: merged `_approval.py` into `_shell_policy.py` (single consumer)

## [0.7.18] - 2026-04-03

### Changed
- **Compaction trigger simplified to token-only**: removed `max_history_messages` config field, `CO_CLI_MAX_HISTORY_MESSAGES` env var, and `DEFAULT_MAX_HISTORY_MESSAGES` constant — compaction now triggers purely on token threshold (85% of budget), matching both peer systems (fork-cc, gemini-cli). Tail-count decoupled from config to `max(4, len(messages) // 2)`.

## [0.7.16] - 2026-04-03

### Added
- **Model-spec-aware compaction budget**: `resolve_compaction_budget()` resolves token budget from model quirks `context_window` (minus output reserve), Ollama `llm_num_ctx` override, or 100K fallback — follows fork-cc's `effectiveContextWindow` pattern instead of hardcoding 100K for all cloud models.
- **`context_window` in model quirks**: Gemini (1M tokens) and Ollama (262K) models now declare their context window in quirks YAML, surfaced through `ResolvedModel.context_window`.
- **`/compact` budget visibility**: compaction summary now shows token estimates and budget (e.g. `est. 85K → 12K of 983K budget`).

### Changed
- **Context module split**: `_history.py` split into `_history.py` (4 pydantic-ai processors) + `_compaction.py` (summarization engine, budget resolution, token estimation). `_transcript.py` split into `_transcript.py` (JSONL I/O) + `_session_browser.py` (session listing/UI). Each module now has a single concern.
- **Public API naming**: removed `_` prefix from `find_first_run_end`, `format_file_size`, `cleanup_skill_run_state` — these were imported as public API outside the context package.
- **`_CompactionBoundaries` moved**: from `_types.py` to `_history.py` (its only consumer).
- **`build_model()` return**: now returns 3-tuple `(model, settings, context_window)` to thread quirks context window through the model registry.

### Fixed
- **`/resume` empty transcript guard**: `_cmd_resume` now handles empty/oversized transcripts gracefully instead of silently clearing history.
- **Duplicate import removed**: `ROLE_TASK` was imported both at module level and inline in `_orchestrate.py`.

## [0.7.14] - 2026-04-02

### Changed
- **LLM HTTP retry delegated to OpenAI SDK**: removed custom retry/backoff logic from `_orchestrate.py` and `_history.py` — the SDK's built-in `max_retries=2` with exponential backoff + jitter is now the sole retry policy. Eliminates double-retry (SDK 2x + app 2x) that caused compounding backoff stalls on transient 429s.
- **Tool-reformulation budget decoupled**: HTTP 400 tool-call reformulation now has an independent budget (`tool_reformat_budget`) — transport failures no longer consume reformulation attempts.

### Removed
- **`model_http_retries` config field**: removed from `Settings`, `CoConfig`, and env var mapping (`CO_CLI_MODEL_HTTP_RETRIES`). No peer CLI tool exposes this; SDK default is the industry standard.
- **`_run_summarization_with_policy()` wrapper**: callers now call `summarize_messages()` directly with try/except fallback to `None`.
- **Merged Ollama evals**: `eval_ollama_openai_noreason_equivalence` + `eval_ollama_openai_summarization` → `eval_ollama_openai_noreason_summarize` (single eval covering both).

## [0.7.12] - 2026-04-02

### Changed
- **Frozen config for knowledge backend**: `deps.config.knowledge_search_backend` now holds the user's configured value, never mutated after `from_settings()`. The actual runtime backend (after degradation) is exposed via `deps.knowledge_store.backend`. Tools, banner, and `/status` read from the store instead of config.
- **Merged discover + init**: `_discover_knowledge_backend` now probes backend availability AND constructs the `KnowledgeStore` in one step; `_init_knowledge_store` removed. Banner shows `(degraded → actual)` when the runtime backend differs from the configured value.

## [0.7.10] - 2026-04-02

### Changed
- **KnowledgeIndex → KnowledgeStore**: renamed class, field (`deps.knowledge_index` → `deps.knowledge_store`), and file (`_index_store.py` → `_store.py`) to reflect its role as a shared storage service.
- **Knowledge init absorbed into `create_deps()`**: `initialize_knowledge()` and `sync_knowledge()` removed as public functions; backend resolution (three-tier fallback) and file sync now run as Steps 6-7 inside `create_deps()`. Config is fully resolved before `CoDeps` construction — no post-construction mutation.
- **Typed knowledge store field**: `deps.knowledge_store` typed as `KnowledgeStore | None` (was `Any | None`).

## [0.7.8] - 2026-04-01

### Changed
- **Pure-config bootstrap**: `create_deps()` is now zero-IO (config + validate + minimal services shell). Knowledge backend resolution (embedder/reranker HTTP probes, `KnowledgeIndex` construction) moved to new `initialize_knowledge(deps, frontend)` called in `_chat_loop` after `initialize_session_capabilities()`. `ModelRegistry` construction also moved to `_chat_loop`.
- **Eliminated `startup_statuses`**: the `list[str]` tuple return from `create_deps()` and its threading through `_chat_loop` and `display_welcome_banner()` is removed. Each bootstrap step now reports status directly via `frontend.on_status()`. Banner derives degraded state from `deps`.
- **Flat per-tool loading policy**: tool loading uses per-tool `always_load`/`should_defer` flags on `ToolConfig` instead of taxonomy-based family grouping. `tool_catalog` renamed to `tool_index`.

### Removed
- Stale evals and scripts: `eval_bootstrap_e2e.py`, `eval_coding_toolchain.py`, `eval_jeff_learns_finch.py`, `eval_thinking_subagent.py`, `eval_tool_chains.py`, `_checks.py`, `_report.py`, `_trace.py`, sample output files, `generate_quality_samples.py`, `trace_report_personality.py`.

## [0.7.6] - 2026-03-31

### Added
- **Tool taxonomy — two-axis catalog**: `ToolConfig` frozen dataclass (name, source, family, approval, integration) added to `deps.py`; `CoCapabilityState` and `AgentCapabilityResult` now carry a `tool_catalog: dict[str, ToolConfig]` field giving every registered tool explicit source (`native`/`mcp`) and family metadata.

### Changed
- **Explicit family registration**: `_reg()` in `agent.py` now accepts `family=` and `integration=` parameters; every native tool call declares its family at registration time. `_build_filtered_toolset()` returns a 3-tuple `(toolset, approvals, catalog)`.
- **Connectors normalization**: Obsidian and Google native tools are classified as `family="connectors"` with per-integration identifiers (`obsidian`, `google_gmail`, `google_drive`, `google_calendar`). `discover_mcp_tools()` returns a 3-tuple and populates MCP catalog entries with `source="mcp", family="connectors"`.
- **Family-aware diagnostics**: `check_runtime()` now returns `family_counts` and `source_counts` derived from the catalog. `check_capabilities()` computes MCP tool count from the catalog instead of the fragile `len(tool_names) - len(tool_approvals)` formula.
- **Canonical tool renames**: `recall_article`→`search_articles`, `read_article_detail`→`read_article`, `todo_write`→`write_todos`, `todo_read`→`read_todos`, `run_coder_subagent`→`run_coding_subagent`, `run_thinking_subagent`→`run_reasoning_subagent`, `list_emails`→`list_gmail_emails`, `search_emails`→`search_gmail_emails`, `create_email_draft`→`create_gmail_draft`. All callers, tests, evals, prompts, and DESIGN docs updated.

## [0.7.4] - 2026-03-31

### Changed
- **Prompt assembly simplification**: static instruction assembly is now a single linear function (`build_static_instructions()`) with explicit 7-section ordering (soul seed → character memories → mindsets → rules → soul examples → counter-steering → critique). Previously split across `_build_system_prompt()` and `assemble_prompt()` with an out-of-band critique append step. Names updated throughout to match pydantic-ai SDK usage: `CoConfig.system_prompt` → `CoConfig.static_instructions`, `_build_system_prompt()` → `build_static_instructions()`. Dead code (`_manifest.py`) removed. Static-vs-dynamic instruction boundary now explicitly labeled in `agent.py`.

## [0.7.2] - 2026-03-31

### Changed
- **Real provider token counts in compaction**: `truncate_history_window()` and `precompute_compaction()` now use `ModelResponse.usage.input_tokens` (provider-reported) as the primary token count source, falling back to the `chars/4` estimate only when no usage data is available. Budget is `llm_num_ctx` for Ollama OpenAI-compat; `100,000` tokens otherwise. Compaction threshold remains 85% of budget.
- **ThinkingPart in boundary logic**: `_find_first_run_end()` now accepts a `ThinkingPart`-only `ModelResponse` as a valid first-run anchor, preventing extended-thinking turns from being silently dropped from the head during compaction.
- **UnexpectedModelBehavior handling**: `run_turn()` catches `pydantic_ai.exceptions.UnexpectedModelBehavior` (e.g., `IncompleteToolCall` mid-stream), surfaces a user-facing status message, sets `outcome="error"`, and returns cleanly — no Python traceback in the chat REPL.
- **Processor side-effect documentation**: `detect_safety_issues()` and `inject_opening_context()` now carry explanatory comment blocks documenting the intentional deviation from pydantic-ai's pure-transformer contract and the invariants that make cross-segment state safe.

## [0.7.0] - 2026-03-31

### Changed
- **Session-scoped background tasks**: replaced the file-backed `TaskStorage`/`TaskRunner` persistence layer with an in-memory model (`BackgroundTaskState` in `CoSessionState`). Tasks live for the duration of a `co chat` session and are cancelled on exit. No files written for task state or output. Removed five background persistence config fields (`tasks_dir`, `background_max_concurrent`, `background_task_retention_days`, `background_auto_cleanup`, `background_task_inactivity_timeout`) and the `CoServices.task_runner` field. `_background.py` reduced from 406 lines to 78.
- **Delegation identity in history**: pydantic-ai's native `run_id` is now threaded through every subagent call (`_run_subagent_attempt`) and surfaced in all four subagent `ToolResult` payloads. `truncate_tool_returns` preserves all identity keys (`run_id`, `role`, `model_name`, `requests_used`, `request_limit`, `scope`) when truncating — only `display` is shortened.
- **`/history` and `/tasks` slash commands**: `/history` scans ToolReturnParts for delegation tool names and renders a Rich table of `run_id`, role, requests, and scope. `/tasks` lists `CoSessionState.background_tasks` with status and start time. Both read in-memory state only.

## [0.6.8] - 2026-03-30

### Changed
- **Approval display enrichment**: file-write approval prompts now show path + byte count (`write_file`) and path + search/replacement snippets + replace\_all flag (`edit_file`), derived from deferred args already available at approval time — no changes to tool bodies.
- **Approval scope wording**: replaced developer-legible bracket hints (`[always → session: git *]`) with user-legible noun phrases (`(allow all git commands this session?)`) across all four scope types (shell, path, domain, tool).

## [0.6.6] - 2026-03-30

### Changed
- **History and memory typing**: replaced all six `"session_summary"` inline magic string comparisons across `memory.py` and `_commands.py` with `ArtifactTypeEnum.SESSION_SUMMARY`. Introduced `ArtifactTypeEnum` and `MemoryKindEnum` (`StrEnum`) inlined in `co_cli/knowledge/_frontmatter.py`. Write path (`persist_memory`) now rejects unknown `artifact_type` values with a `ValueError`; load path (`_load_memories` via `validate_memory_frontmatter`) tolerates unknown values with a `logger.warning` — backward-safe for stored files. Removed dead code: `_touch_memory()` function and its `# Gravity` block comment. `MemoryEntry.artifact_type` field type remains `str | None` (read-tolerant); enum constants are used only at comparison and write callsites.

## [0.6.4] - 2026-03-30

### Changed
- **Foreground turn contract tightening**: replaced all loose `Any` in `_finalize_turn()`, `_run_foreground_turn()`, and `_chat_loop()` with concrete types (`TurnResult`, `Frontend`, `list[ModelMessage]`, `dict`/`dict | None`). Remaining `Any` fields in `_orchestrate.py` (`TurnResult.output/usage`, `_TurnState.latest_usage`, `_merge_turn_usage` param) annotated with inline comments documenting the pydantic-ai SDK constraint. Ownership boundaries confirmed explicit: `_chat_loop()` is control-plane only, `_run_foreground_turn()` owns turn wrapper sequencing, `run_turn()` owns turn execution state. `StreamRenderer` audit confirmed clean — instantiated only inside `_execute_stream_segment()`, not passed through any helper. No behavioral changes.

## [0.6.3] - 2026-03-30

### Changed
- **Agent runtime boilerplate reduction**: removed the ROLE_REASONING two-step fallback chain from `/compact` and `/new` — if ROLE_SUMMARIZATION is unconfigured, both commands now use `None` directly rather than silently inheriting the reasoning model. Converted `_signal_detector.py` and `_history.py` per-call `Agent` construction to module-level singletons with model deferred to `.run(model=...)`, matching the existing `_consolidator.py` pattern. Deduplicated the 4 subagent factory constructors in `_subagent_agents.py` via a private `_make_base_agent` helper. Added a one-line comment at the `inner.filtered()` callsite in `agent.py` documenting the pydantic-ai 1.73 API. No behavioral changes.

## [0.6.2] - 2026-03-30

### Added
- **Pytest suite audit — coverage gaps closed**: 3 new shell-policy denial tests (control characters, heredoc, `VAR=$(...)` env-injection); 7 new web tests (`web_fetch` HTML→Markdown, `_html_to_markdown` tag/link conversion, `_is_content_type_allowed` MIME matrix); 6 new approval-scoping unit tests (shell/path/domain/tool subject resolution, remember idempotency, auto-approve after remember, deny-no-persist); 2 new `_build_interrupted_turn_result` unit tests (dangling tool call drop, clean history retention).
- **`LLM_DEFERRED_TURN_TIMEOUT_SECS`** in `tests/_timeouts.py`: 60s constant for deferred-approval denial flows where both agent.run() segments pay full tool-context KV-fill cost (~20s each) with no tool execution between them. Fixes web_search_fastapi test that was budgeted at `LLM_MULTI_SEGMENT_TIMEOUT_SECS` (40s) and structurally needed 3×.
- **Pytest harness deferred-approval filter**: `_extract_tool` in `tests/_co_harness.py` now skips spans with `duration_ms < 5ms` so deferred-approval bookkeeping spans (pydantic-ai internal resolution, ~1–4ms) are not counted as real tool executions in the harness `tools=` summary line.
- **`ensure_ollama_warm` elapsed-time reporting**: prints `[ollama] <model> ready (N.Ns)` after warmup completes for visibility into infrastructure prep time separate from test timeout windows.

## [0.6.0] - 2026-03-30

### Changed
- **pydantic-ai upgrade to 1.73.0**: bumped pin from 1.70.0 to 1.73.0. Zero private SDK imports in tests (`pydantic_ai._run_context` → public `pydantic_ai.RunContext`). All `Agent()` constructors in `co_cli/` standardized on `instructions=`. Single `InstrumentationSettings` import path (`pydantic_ai.agent`) across `co_cli/`, `evals/`, and `tests/`. No user-visible behavior change.

## [0.5.31] - 2026-03-29

### Added
- **Task agent for approval resume turns**: approval resume turns now route through a lightweight task agent (`ROLE_TASK`, `reasoning_effort: none`, short 2-sentence system prompt) instead of the full main agent (~30K tokens). Eliminates thinking overhead on resume turns. Main agent first-turn behavior is unchanged.
- **Tool-schema context filtering**: `FilteredToolset` + `active_tool_filter` on `CoDeps.runtime` gates which tool schemas are sent to the model per hop. On approval resume, only the deferred tools plus always-on tools are included in the schema payload — reduces token overhead for multi-tool agents.
- `ROLE_TASK` config role with `DEFAULT_OLLAMA_TASK_MODEL` (`reasoning_effort: none`) and `CO_MODEL_ROLE_TASK` env var override.

## [0.5.29] - 2026-03-29

### Changed
- **Test suite policy audit**: removed 12 policy-violating tests (private-attribute access, internal builder probing, guard-branch assertions, direct `agent.run()` orchestration); migrated all LLM-calling tests to `run_turn()` via a new `tests/_frontend.py` `SilentFrontend`; closed `check_security()` wildcard gap with real `shell_safe_commands` `"*"` detection and 4 new tests.

## [0.5.27] - 2026-03-29

### Added
- **User-global skill directory**: skills placed in `~/.config/co-cli/skills/` are now loaded across all projects without copying — three-layer load order: bundled (lowest) → user-global → project-local (highest). `/skills reload`, `/skills check`, `/skills install`, and bootstrap all reflect the new tier.

### Changed
- **Bundled skill scan skipped at runtime**: package-default skills are version-controlled and scanned at CI; runtime re-scan removed (`scan=False` for bundled pass). User-global and project-local passes still scan. `/skills reload` display loop now covers only user-global and project-local.

### Fixed
- **Symlink path containment**: symlinks inside any skill directory pointing outside their load root are now silently rejected with a `logger.warning` instead of loading arbitrary files into the skill registry.

## [0.5.25] - 2026-03-29

### Changed
- **History compaction ownership**: clean aspect cut moving all background compaction lifecycle out of `main.py` and into `HistoryCompactionState` (`_history.py`). `main.py` now holds zero references to `precomputed_compaction`, background task handles, or boundary calculations. Extracted `_CompactionBoundaries` type and `_compute_compaction_boundaries()` helper to eliminate duplicated boundary logic between `truncate_history_window()` and `precompute_compaction()`. `_cmd_compact()` now returns `ReplaceTranscript` directly, removing the `name == "compact"` string inference from `dispatch()`.

## [0.5.23] - 2026-03-29

### Changed
- **Tool context reduction**: approval-resume turns now send only the deferred tool schemas plus three always-on tools (`check_capabilities`, `todo_read`, `todo_write`) instead of the full 38-tool set, cutting KV-fill cost from ~12s to <2s per resume segment. Implemented via `FilteredToolset` wrapping `FunctionToolset` with a per-request `active_tool_filter` on `CoRuntimeState`; main-agent turns are unaffected (filter is `None`).
- **Test suite policy audit**: removed all policy-violating tests — private helper imports, fake service doubles, inline timeout literals, direct result-model construction, and implementation-detail assertions. Replaced with functional equivalents where behavior coverage was lost.

## [0.5.21] - 2026-03-29

### Fixed
- **Reasoning display test coverage**: completed the remaining verification gaps for the reasoning-display feature. Added `active_status_text() -> str | None` inspection API to `TerminalFrontend` (consistent with existing `active_surface()` / `active_tool_messages()` pattern). Added four focused `StreamRenderer` tests in `test_display.py` covering: no emit on no sentence boundary, last complete sentence extraction, 80-char truncation, and whitespace-only input.

## [0.5.19] - 2026-03-29

### Changed
- **State class naming and functional cleanup**: dissolved `_ChatTurnState` (replaced with explicit return values in `main.py`) and `StreamRenderState` (inlined into `StreamRenderer` instance variables). Renamed `OpeningContextState` → `MemoryRecallState` and moved from `CoRuntimeState` to `CoSessionState`. Added `CoRuntimeState.reset_for_turn()` to encapsulate three scattered per-turn resets. Made `TurnResult.outcome` and `TurnResult.interrupted` required fields (no silent defaults). Renamed `CoToolState` → `CoCapabilityState` and promoted `skill_commands`/`skill_registry` out of `CoSessionState` into it. Typed `CommandContext.deps` and `.agent` (removed `Any`). System-wide suffix conformance: `WebRetryDecision` → `WebRetryResult`, `StatusInfo` → `StatusResult`, `ModelEntry` → `ModelConfig`, `RuntimeCheck` → `RuntimeCheckResult`, `SecurityFinding` → `SecurityCheckResult`, `ShellDecision` → `ShellDecisionEnum`, `TaskStatus` → `TaskStatusEnum`, `ApprovalKindEnum` introduced.

## [0.5.17] - 2026-03-29

### Changed
- **Approval scope simplification**: reduced approval subject taxonomy from 5 kinds to 4 — removed `mcp_tool` kind; MCP tools and all generic tools now fall through to `tool` (session-scoped, rememberable). Path approvals now use a bare parent directory as the key, shared across `write_file` and `edit_file` for the same directory. The `mcp_prefixes` prefix-matching machinery is fully removed.

## [0.5.15] - 2026-03-29

### Changed
- **Skill state ownership**: replaced module-global `SKILL_COMMANDS` with `CoSessionState.skill_commands` (`dict[str, SkillConfig]`), extracted `SkillConfig` to `co_cli/commands/_skill_types.py` to break circular import cleanly. Removed hidden session mutations from `dispatch()`. `active_skill_env` field eliminated; `active_skill_name` moved to `CoRuntimeState` (transient per-turn state). All read paths in `_commands.py` and `main.py` now flow through `ctx.deps.session.skill_commands`.

## [0.5.14] - 2026-03-28

### Changed
- **Startup capability ownership**: `build_agent()` now returns `AgentCapabilityResult` instead of a bare 3-tuple, making the agent capability surface explicit in the type system. MCP discovery and skill loading are extracted from `_chat_loop()` into `initialize_session_capabilities()` in `_bootstrap.py`, replacing ~25 lines of inline assembly with a single named boundary that returns `SessionCapabilityResult`.

## [0.5.12] - 2026-03-28

### Changed
- **Slash dispatch model**: replaced `DispatchResult` with an explicit `SlashOutcome` union (`LocalOnly | ReplaceTranscript | DelegateToAgent`), making command intent visible in the type system and eliminating optional-field guessing in `_chat_loop()`. `/compact` bookkeeping is now carried in `ReplaceTranscript.compaction_applied` rather than a separate flag.

## [0.5.10] - 2026-03-28

### Changed
- **Core loop polish**: extracted finish-reason and context-overflow diagnostics from `run_turn()` into `_check_output_limits()`, making the success path read as a clean sequence ending at `return TurnResult(...)`. Inlined `_spawn_bg_compaction()` — a 3-line single-call-site wrapper — directly into `_finalize_turn()`.

## [0.5.8] - 2026-03-28

### Changed
- **Core loop simplified**: `_run_foreground_turn()` extracted as the single foreground turn owner, replacing an inline four-statement chain in `_chat_loop()`. `_ChatTurnState` dataclass groups the three co-evolving REPL locals (`message_history`, `session_data`, `next_turn_compaction_task`) — loop body is now pure control-plane routing.
- **Approval loop extracted**: `_run_approval_loop()` pulled out of `run_turn()` in `_orchestrate.py`, and `_merge_turn_usage()` absorbed into `_execute_stream_segment()` — the turn/segment boundary is now explicit and `run_turn()` reads as a clean orchestration sequence.
- **`Frontend` renamed**: `FrontendProtocol` renamed to `Frontend` across all call sites — the `Protocol` suffix was redundant noise; the type is the public contract name.
- **Approval UX self-labeling**: `y=once / a=session / n=deny` labels added to the approval prompt hint so users see the scope of each choice without consulting docs.

### Fixed
- **Google Cloud tests guard**: `test_google_cloud.py` now skips via `pytestmark` when Google credentials lack Drive/Gmail/Calendar scopes — guards against Google's ADC policy change that blocks the default client ID from requesting these scopes.

## [0.5.6] - 2026-03-28

### Changed
- **Usage limits removed**: `UsageLimits(request_limit=N)` ceiling removed from the main turn loop — loop stability is still enforced by `doom_loop_threshold` (identical tool-call streak detection) and `max_reflections` (consecutive shell error cycles). Removes a premature resource guard that constrained long agent turns without providing meaningful safety.
- **`ToolApprovalDecisions` type alias**: `DeferredToolResults` aliased as `ToolApprovalDecisions` in `_orchestrate.py` to make the SDK type's semantic role self-documenting — approval decisions, not executed tool output.
- **`_collect_deferred_tool_approvals()` docstring**: States the True/`ToolDenied` return contract explicitly and distinguishes approval decisions from `ToolReturnPart` output.
- **`tool_progress_callback` ownership documented**: Three-party contract (StreamRenderer installs/clears, tool invokes, `run_turn()` safety-net nulls) recorded as a comment on the `CoRuntimeState` field.

### Fixed
- **Stale test comments**: 6 comment occurrences of `_run_stream_segment()` in `test_orchestrate.py` updated to `_execute_stream_segment()`.

## [0.5.4] - 2026-03-28

### Changed
- **Prompt assembly extracted**: `_collect_rule_files()`, `assemble_prompt()`, and `_build_system_prompt()` moved from `co_cli/prompts/__init__.py` and `co_cli/agent.py` into `co_cli/prompts/_assembly.py`. `__init__.py` is now docstring-only.
- **Personality helper relocated**: `co_cli/tools/personality.py` moved to `co_cli/prompts/personalities/_injector.py` — it was never a registered agent tool, only a prompt helper.
- **Private helper renamed**: `load_always_on_memories` → `_load_always_on_memories` in `co_cli/tools/memory.py` to signal it is an internal helper, not a public tool.

## [0.5.2] - 2026-03-28

### Changed
- **Core loop simplification**: Unified per-turn usage ownership — `deps.runtime.turn_usage` is now the single authoritative accumulator, reset by `run_turn()` and merged via `_merge_turn_usage()` after every segment (foreground and sub-agent paths).
- **`main.py` extracted helpers**: Post-turn lifecycle split into `_finalize_turn()`, `_restore_skill_env()`, and `_spawn_bg_compaction()`. `_chat_loop()` now reads as input → dispatch → turn → finalize.
- **`display.py` → `display/` package**: `co_cli/display.py` converted to a package; content in `_core.py`, `StreamRenderer` in `_stream_renderer.py`. All import sites updated to `co_cli.display._core`.
- **`StreamRenderer` extracted**: Text/thinking buffering, flush policy, and progress-callback lifecycle moved from `_execute_stream_segment()` into `co_cli/display/_stream_renderer.py`. Orchestration now only routes events.
- **`_display_hints.py` extracted**: Tool display metadata (`TOOL_START_DISPLAY_ARG`, `get_tool_start_args_display`, `format_tool_result_for_display`) moved from the orchestrator to `co_cli/tools/_display_hints.py`.
- **Approval legibility**: `/approvals list` shows human-readable "Scope" and "Approved For" columns instead of raw `kind`/`value` strings. `resolve_approval_subject()` annotated with per-branch comments.
- **`_TurnState` phase-owner comments**: Each field annotated with its lifecycle phase (pre-turn, in-turn, cross-turn). `_adopt_segment_result()` and `_prepare_approval_resume()` helpers centralize approval-resume state transitions.
- **Background compaction renamed**: `bg_compaction_task` → `next_turn_compaction_task` for clarity.
- **`__init__.py` policy hardened**: Rule updated to "must be docstring-only — never add imports or code"; module-to-package conversions must put content in named private submodules.

## [0.5.0] - 2026-03-28

### Changed
- **`run_turn()` is the single turn entrypoint**: Removed `run_turn_with_fallback()` — its three responsibilities (status display, `co.turn` span ownership, config plumbing) are now inlined into `run_turn()`. Signature simplified: `max_request_limit` and `http_retries` removed as explicit params, read from `deps.config` directly. `main.py` calls `run_turn()` without the wrapper layer.
- **Model role governance**: Unified model selection behind `model_roles` role chains. `create_deps()` now resolves summarization via `get_role_head(settings.model_roles, "summarization")` instead of a standalone setting.
- **Model role env vars**: Standardized on `CO_MODEL_ROLE_<ROLE>` (`REASONING`, `SUMMARIZATION`, `CODING`, `RESEARCH`, `ANALYSIS`). Removed the legacy `CO_CLI_SUMMARIZATION_MODEL` path.
- **Role validation hardening**: Added explicit role-key validation (`VALID_MODEL_ROLES`) and reject-unknown behavior for `model_roles`.
- **MCP init guard**: `discover_mcp_tools` is now skipped when `stack.enter_async_context(agent)` fails — prevents tool discovery against a broken MCP connection.
- **Observability doc renamed**: `DESIGN-logging-and-tracking.md` replaced by `DESIGN-observability.md` with an expanded span table, span hierarchy description, and accurate `co.turn` ownership.
- **Workflow simplified to 2 gates**: `DELIVERY-*.md` files and Gate 3 eliminated. `orchestrate-dev` appends delivery summary to `TODO`, `review-impl` appends PASS/FAIL verdict, Gate 2 reads the TODO and ships. `TODO` deleted after PASS.

### Added
- **RAG backend span attribute**: `search_knowledge` and `search_memories` now stamp `rag.backend` on the active OTel span (`"fts5"`, `"hybrid"`, or `"grep"`) so retrieval path is visible in traces.
- **Degraded banner indicator**: Welcome banner now shows `✓ Ready  (degraded)` when startup fallbacks are active (hybrid→fts5 degradation, reranker unavailability).
- **Config validation attribution**: `load_config()` now wraps `ValidationError` with a `ValueError` that names the config files that triggered the error. `get_settings()` catches it, prints cleanly to stderr, and exits — no raw traceback on bad config.
- **Session save resilience**: `restore_session()` wraps `save_session()` in an `OSError` guard — session continues without persistence rather than crashing on read-only filesystems.
- **Role governance tests**: Added `tests/test_model_roles_config.py` coverage for unknown role rejection, role-env parsing, and deps summarization-model derivation from `model_roles["summarization"]`.

### Fixed
- **Stale import removed**: `FinishReason` was imported in `_orchestrate.py` but never used — removed.

## [0.4.5] - 2026-03-01

### Added
- **Knowledge index dependency updates**: Added `sqlite-vec` and `pysqlite3` runtime dependencies to support hybrid semantic search with sqlite extension loading on environments where stdlib `sqlite3` lacks extension APIs.
- **Knowledge system functional coverage**: Added dedicated functional test suites for knowledge indexing and article tools (`tests/test_knowledge_index.py`, `tests/test_save_article.py`) and expanded Obsidian FTS regression coverage.

### Fixed
- **Obsidian FTS folder scoping**: Fixed folder filtering to use boundary-safe path matching so sibling folders with common prefixes (for example `Work` vs `Workbench`) do not leak into results.
- **Obsidian FTS tag filtering**: Fixed type mismatch in FTS path (`tags` now passed as list) so tag-constrained searches return correct results.
- **Forget/de-index consistency**: `/forget` now evicts deleted memory files from the search index immediately via `KnowledgeIndex.remove()`, preventing ghost recall results.
- **Approval gating parity**: `update_memory` and `append_memory` now honor `all_approval=True` in eval/strict approval mode.
- **Grep fallback source contract**: `search_knowledge` grep fallback now returns empty for non-memory sources when FTS is unavailable, instead of returning mislabeled memory results.
- **LLM E2E/tool-calling test stability**: Updated assertions to match current tool surface (`search_knowledge` primary retrieval path) and removed brittle failure-path checks causing nondeterministic provider-specific failures.

### Changed
- **Knowledge roadmap docs sync**: `docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md` now reflects the current shipped state (Phase 1 + Phase 2 complete, Phase 3 reranker pending).
- **No-skip test policy enforcement for touched suites**: Removed skip decorators from updated knowledge/web/tool-calling tests and converted them to deterministic pass/fail assertions. Current full suite: `274 passed`.

---

## [0.4.3] - 2026-02-26

### Added
- **`test_tool_calling_functional.py`**: New functional test file replacing `eval_tool_calling.py` dimensions — tool selection, arg extraction, refusal, intent routing, and error recovery, all exercised through the real agent pipeline.
- **`eval_personality_behavior.py`**: Consolidated personality eval covering 1-turn and multi-turn consistency (heuristic-scored), replacing the removed `eval_personality_adherence.py` and `eval_personality_cross_turn.py`.
- **`qwen3-coder-next` quirks file** (`prompts/quirks/ollama/qwen3-coder-next.md`): Counter-steering for the new coder model variant.
- **New agentic Modelfiles**: `Modelfile.qwen3-30b-a3b-thinking-2507-q8_0-agentic` and `Modelfile.qwen3-coder-next-q4_k_m-agentic`.
- **`scripts/trace_report_personality.py`**: Trace report script for personality behavior analysis.

### Fixed
- **Test suite fully green (193/193)**: Fixed 4 stale test assertions — `test_rules_token_budget` (threshold 5000→6000 chars to match expanded rules), `test_compose_personality_contains_mandate` (removed stale `"Match expression depth to context"` phrase), `test_build_agent_registers_all_tools` (added `todo_write`/`todo_read` to canonical tool inventory), `test_commands_registry_complete` (added `depth` to expected commands set).
- **`web_search` display**: Added `"Web search results for '{query}':\n\n"` prefix to the `display` field so tool output is self-describing in conversation context.

### Changed
- **Eval consolidation**: Removed standalone `eval_tool_calling.py`, `eval_personality_adherence.py`, `eval_personality_cross_turn.py` and their associated data/result files; consolidated shared infrastructure into `evals/_common.py`.
- **Ollama model cleanup**: Removed stale `glm-4.7-flash` Modelfiles and quirks file; removed base (non-agentic) `qwen3-30b-a3b` and `qwen3-30b-a3b-q4` Modelfiles.
- **`DESIGN-llm-models.md`**: Major update — reflects current model lineup, Modelfile setup guide, and corrected `num_ctx` default (2048→4096).
- **`config.py` / `model_quirks.py`**: Doc comment updates — corrected num_ctx default token count and updated doc reference to `DESIGN-llm-models.md`.

---

## [0.4.1] - 2026-02-25

### Added
- **Auto-triggered signal detection** (`_signal_analyzer.py`): Two-stage post-turn filter — keyword precheck gate (O(1)) short-circuits to an LLM mini-agent that classifies the latest user message. High-confidence corrections (explicit `don't/stop/never/avoid`) are saved automatically; low-confidence preferences prompt user approval before saving. No LLM call when the precheck gate finds nothing.
- **Signal analyzer system prompt** (`prompts/agents/signal_analyzer.md`): Structured classification rules for high/low/none signal tiers, guardrails for hypotheticals, teaching moments, capability questions, and sensitive content.
- **3 new eval scripts**: `eval_signal_analyzer.py` (12-case classification accuracy), `eval_signal_detector_approval.py` (4-case approval dispatch), `eval_memory_signal_detection.py` (end-to-end detection + contradiction).
- **`_save_memory_impl()`** (`tools/memory.py`): Shared write path extracted so both the `save_memory` tool and the signal detector's auto-save call the same implementation without triggering decay logic on auto-saves.
- **DESIGN-14 memory lifecycle doc** and **DESIGN-16 prompt design doc**: Full architecture documentation for signal detection and agentic loop prompt layers.

### Fixed
- **Signal analyzer credential guardrail**: LLM incorrectly flagged `"my API key is sk-1234, please don't save that anywhere"` as a high-confidence behavioral signal. Fixed by strengthening the guardrail description and adding an explicit counter-example to the prompt table.
- **Truncation detection heuristic**: Replaced 95%-of-max-tokens estimate with exact `finish_reason == "length"` check — eliminates false positives on long but complete responses.

---

## [0.4.0] - 2026-02-14

### Added
- **Agentic loop & prompting architecture** (Phases 1, 2, 4 from `TODO-co-agentic-loop-and-prompting.md`): Ground-up redesign of the orchestration layer, history processors, prompt composition, and safety guardrails. Peer-system research across Claude Code, Codex, Aider, Gemini CLI, and OpenCode informed every design decision.
- **Doom loop detection** (`_history.py`): Hash-based consecutive identical `ToolCallPart` detection. When the model repeats the same tool call N times (configurable `doom_loop_threshold`, default 3), a `SystemPromptPart` is injected telling it to try a different approach. Once per turn — `SafetyState.doom_loop_injected` flag prevents re-injection.
- **Shell reflection cap** (`_history.py`): Counts consecutive `run_shell_command` errors. At `max_reflections` (default 3), injects a system message telling the model to ask the user for help. Success resets the counter. Non-shell tool errors are ignored.
- **Opening context injection** (`_history.py`): Async processor that recalls relevant memories on every new user turn — no heuristic gate. recall_memory is grep-based (zero LLM cost); returns empty when nothing matches. Session-scoped `OpeningContextState` tracks `recall_count`, `model_request_count`, `last_recall_user_turn`.
- **Abort marker on Ctrl-C** (`_orchestrate.py`): When `KeyboardInterrupt`/`CancelledError` interrupts a turn, an abort marker (`"The user interrupted the previous turn..."`) is injected into message history so the next turn has awareness of the interruption. `TurnResult.interrupted = True`.
- **Grace turn on usage limit** (`_orchestrate.py`): When `UsageLimitExceeded` fires, instead of crashing, the orchestrator runs one final "grace" request asking the model to summarize progress and suggest `/continue`. Status message: `"Turn limit reached — summarising progress..."`.
- **Finish reason detection** (`_orchestrate.py`): Heuristic check after each response — if `output_tokens >= 95% of max_tokens`, a status warning fires: `"Response may be truncated (hit output token limit). Use /continue to extend."`.
- **Auto-compaction token trigger** (`_history.py`): `truncate_history_window` now triggers on EITHER message count exceeding `max_history_messages` OR estimated token count exceeding 85% of 100k budget (4 chars/token heuristic). Dual trigger prevents silent context overflow for long conversations with short message counts.
- **Typed turn outcomes** (`_orchestrate.py`): `TurnOutcome = Literal["continue", "stop", "error", "compact"]` replaces ad-hoc string returns. `TurnResult` dataclass with `outcome`, `output`, `messages`, `interrupted` fields.
- **Current date in system prompt** (`agent.py`): `@agent.system_prompt` decorator injects `"Today is {date}."` so the model can reason about time correctly.
- **Per-tool auto-approve** (`_orchestrate.py`, `deps.py`, `display.py`): The "a" (auto-approve) choice now tracks per tool name (`deps.auto_approved_tools: set[str]`) instead of blanket `auto_confirm`. Approval prompt shows `[y/n/a]` hint.
- **5 companion rules rewritten** (`prompts/rules/01-05`): Identity (core traits, anti-sycophancy, thoroughness), Safety (credentials, source control, approval, memory constraints), Reasoning (verification, fact authority, two kinds of unknowns), Tools (preamble messages, strategy), Workflow (three-way intent classification, execution, when NOT to over-plan). All aligned with §10.1-10.5 of the agentic loop design.
- **Memory linking** (`tools/memory.py`): `MemoryEntry.related` field (list of slugs). `save_memory()` accepts `related` parameter. `recall_memory()` performs one-hop traversal — surfaces linked memories in a "Related memories" section even if they don't match the query directly.
- **9 E2E eval scripts** (`scripts/eval_e2e_*.py`): One script per Tier 2 verification flow — doom loop (4 deterministic tests), shell reflection (4 deterministic tests), abort marker, memory linking, compaction prompt quality, project instructions, opening context, grace turn, finish reason. All verified passing.
- **Personality enhancement TODO** (`docs/TODO-co-personality-enhancements.md`): Future work for personality system improvements.
- **Seed memory files** (`.co-cli/knowledge/memories/`): Initial memory entries.

### Changed
- **`max_request_limit` default raised** from 25 to 50 — aligns with agentic loop requirements for multi-step tool chains.
- **4 history processors** registered on agent (was 2): `inject_opening_context`, `truncate_tool_returns`, `detect_safety_issues`, `truncate_history_window`. Order matters — opening context first (cheapest), safety last before window trim.
- **`SafetyState` is turn-scoped**: Created fresh per turn by `run_turn()`, stored on `CoDeps._safety_state`. Prevents stale doom loop / reflection flags from prior turns.
- **`OpeningContextState` is session-scoped**: Initialized once per session in `create_deps()`, stored on `CoDeps._opening_ctx_state`. Persists across turns for topic-shift detection.
- **Compaction prompt improved**: Handoff-style, first-person voice (`"I asked you..."`). Anti-injection security rule for summarizer. Both `truncate_history_window` and `/compact` use shared `summarize_messages()`.
- **Web tool docstrings**: Chain hints added to `web_search` and `web_fetch` for better model tool sequencing.
- **Drive `search_drive_files` docstring**: Explicit pagination guidance so the model knows to paginate when complete results are needed.
- **Shell tool DESIGN-09 doc**: Fully rewritten to match current `ShellBackend` implementation (Docker sandbox dropped).

### Removed
- **Docker sandbox**: Dropped `DockerSandbox`, `SandboxProtocol`, `Dockerfile.sandbox`, `_sandbox_env.py`. `ShellBackend` (approval-gated subprocess) is the sole execution model. Simplifies architecture — approval gate is the security layer.
- **`eval_e2e_streaming.py`**: Superseded by test suite coverage.
- **`auto_confirm` field on CoDeps**: Replaced by `auto_approved_tools: set[str]` for per-tool granularity.

### Fixed
- **Model lacks date awareness**: Agent said January 2026 dates were "future" on February 14. Fixed by injecting current date into system prompt via `@agent.system_prompt`.
- **Flaky `test_approval_approve`**: Ollama model sometimes returned text instead of calling the shell tool. Fixed with directive prompt ("Do NOT describe what you would do — call the tool now") and retry loop (up to 3 attempts).

---

## [0.3.10] - 2026-02-11

### Fixed
- **CoDeps flat settings injection**: Eliminated hybrid config access pattern — removed `settings: Settings` object from `CoDeps`, flattened 8 memory/history fields as scalars. Tools now use `ctx.deps.memory_max_count` (not `ctx.deps.settings.memory_max_count`). `truncate_history_window` uses DI via `ctx.deps` instead of importing global singleton. Prevents divergence traps where `deps.X` and `deps.settings.X` could hold different values at runtime.

### Added
- **Ollama agentic Modelfiles** (`ollama/`): Pre-built Modelfiles for GLM-4.7-Flash (q4_k_m, q8_0) and Qwen3-30B-A3B with verified inference parameters from official model cards. Parameters: `num_ctx` (native context), `num_predict`, `temperature`, `top_p`, `repeat_penalty`.
- **`docs/GUIDE-ollama-local-setup.md`**: Comprehensive guide for configuring Ollama as a local LLM backend for agentic systems — Modelfile configuration, client-level settings, RAM-to-context sizing guide, model recommendations with tool-calling verification, server tuning (flash attention, keep-alive, memory management), Apple Silicon specifics.
- **`ollama_num_ctx` config setting**: Client-side context window override (default 202752) with `OLLAMA_NUM_CTX` env var. Sent with every Ollama request as a consistency guarantee alongside server-level Modelfile config.
- **Model inference database** (`model_quirks.py`): Per-model inference parameters sourced from official profiles — GLM-4.7-Flash Terminal/SWE-Bench profile (temp 0.7, top_p 1.0, num_ctx 202752), Qwen3 thinking profile (temp 0.6, top_p 0.95, top_k 20, num_ctx 262144). Agent factory reads params from quirk DB at call time.
- **Soul-first prompt assembly** (`co_cli/prompts/`): Soul seed + 5 behavioral rules composed into system prompt. Personality modulates voice, never overrides safety or reasoning.
- **Memory lifecycle system** (`DESIGN-14`): Persistent knowledge across sessions via markdown files with YAML frontmatter. `save_memory`, `recall_memory`, `list_memories` tools. Proactive signal detection, dedup-on-write, size-based decay.
- **Personality system**: Finch & Jeff personalities from 2021 film, plus 3 base personalities. Composer assembles personality + counter-steering.

### Changed
- **DESIGN-07 renamed** from "Conversation Memory" to "Context Governance" — aligns with peer system terminology (Claude Code, Codex, Gemini CLI) where "memory" means persistent cross-session knowledge and "context/history" means conversation management.
- **Agent factory refactored**: Inference parameters pulled from quirk database per model instead of hardcoded values. `/model` command returns `ModelSettings` for switched models.
- **Prompt assembly**: Replaced monolithic `system.md` with rule-based composition. Model quirks add counter-steering for known model issues.

### Fixed
- **Ollama silent context truncation**: Default `num_ctx` of 4096 caused Ollama to silently drop input prompts. Now sends native context length (202752 for GLM-4.7-Flash, 262144 for Qwen3) via both Modelfile and client-side override.

## [0.3.8] - 2026-02-08

### Added
- **Orchestration extraction** (`co_cli/_orchestrate.py`): Extracted the ~170-line streaming + approval state machine from `main.py` into a standalone module. `FrontendProtocol` (`@runtime_checkable`) decouples display from orchestration — `TerminalFrontend` (Rich) for the CLI, `RecordingFrontend` for tests. `TurnResult` dataclass returned by `run_turn()`. `_stream_events()`, `_handle_approvals()`, and `_patch_dangling_tool_calls()` moved here.
- **`_StreamState` dataclass** and extracted stream helpers in `_orchestrate.py`: `_flush_thinking()`, `_append_thinking()`, `_append_text()`, `_commit_text()`, `_flush_for_tool_output()`, `_handle_part_start_event()`, `_handle_part_delta_event()` — explicit state management replacing closure-based approach. `FinalResultEvent` and `PartEndEvent` are explicit no-ops for rendering, preventing premature text commits mid-stream.
- **Provider error classification** (`co_cli/_provider_errors.py`): `ProviderErrorAction` enum (REFLECT/BACKOFF_RETRY/ABORT) and `classify_provider_error()` for `ModelHTTPError`/`ModelAPIError`. HTTP 429 parses `Retry-After` header. Exponential backoff capped at 30s. All retries bounded by `settings.model_http_retries`.
- **Tool error classification** (`co_cli/tools/_errors.py`): `ToolErrorKind` enum (TERMINAL/TRANSIENT/MISUSE), `classify_google_error()` inspects HttpError status codes and string patterns, `handle_tool_error()` dispatches to `terminal_error()` or `ModelRetry`.
- **`TerminalFrontend`** in `display.py`: Implements `FrontendProtocol` with Rich `Live`, `Panel`, `Markdown`, `Prompt`. SIGINT handler swap for synchronous approval prompts.
- **`RecordingFrontend`** in `tests/test_orchestrate.py`: Records `(event_type, payload)` tuples for assertions. Configurable `approval_policy`.
- **`docs/DESIGN-04-streaming-event-ordering.md`**: First-principles RCA of streaming event ordering, boundary-safe rendering design, and regression coverage.
- **`tests/test_errors.py`**: 25 functional tests — `classify_google_error` (9 cases), `handle_tool_error` (3), `classify_provider_error` (8), `_parse_retry_after` (5).
- **`tests/test_orchestrate.py`**: 11 functional tests — protocol compliance, event recording, approval policies, `_stream_events` regression coverage, `_patch_dangling_tool_calls`, `FinalResultEvent` mid-stream no-op.
- **`tests/test_display.py`**: Display helper tests.
- **Eval improvement**: `er-drive-01` error recovery case now passes 3/3 (was 0/3) after `terminal_error()` fix — model no longer loops on unconfigured Drive API.

### Changed
- **Web policy model simplified**: Replaced `web_permission_mode` with per-tool `web_policy` (`search` and `fetch`, each `allow|ask|deny`). Approval wiring in `build_agent()` is now per tool, and deny enforcement moved to `ctx.deps.web_policy.search/fetch` checks in web tools. Added env overrides `CO_CLI_WEB_POLICY_SEARCH` and `CO_CLI_WEB_POLICY_FETCH`. Updated reference config and web design doc.
- **Thinking display**: Replaced bordered `Panel` with plain dim italic `Text` in `on_thinking_delta()` and `on_thinking_commit()` — lighter weight, codex-style.
- **`co_cli/main.py`**: LLM turn block collapsed from ~60 lines to a single `run_turn()` call. Removed `_stream_agent_run`, `_handle_approvals`, `_patch_dangling_tool_calls`, `_CHOICES_HINT`, `_RENDER_INTERVAL`.
- **`co_cli/tools/google_drive.py`, `google_gmail.py`, `google_calendar.py`**: Replaced ad-hoc `except Exception` blocks with `classify_google_error()` + `handle_tool_error()`.
- **`co_cli/tools/shell.py`**: Split single `except Exception` into `RuntimeError` (timeout/permission) vs generic, with specific hints.
- **`co_cli/tools/slack.py`**: Added `_classify_slack_error()` mapping Slack error codes to `ToolErrorKind`. All `except SlackApiError` blocks use `handle_tool_error()`.
- **`tests/test_approval.py`**: Import updated from `co_cli.main` to `co_cli._orchestrate`.
- **DESIGN doc rename**: `DESIGN-co-cli.md` → `DESIGN-00-co-cli.md` — all references updated across CLAUDE.md, README.md, index.md, TODO docs.
- **GitHub Pages book**: Replaced `core.md`, `infrastructure.md`, `quickstart.md`, `tools.md` with `BOOK-` prefixed helper pages; updated `_config.yml` excludes.
- **`docs/DESIGN-02-chat-loop.md`**: Rewritten for `FrontendProtocol`, `run_turn()`, provider error table, tool error classification, updated all function/diagram references.
- **`docs/DESIGN-00-co-cli.md`**: Added `_orchestrate.py`, `_provider_errors.py`, `tools/_errors.py` to module table. Updated error handling and tool convention docs.
- **`docs/DESIGN-11-tool-google.md`**: Updated error handling section for `terminal_error()` vs `ModelRetry` strategy.
- **`docs/TODO-web-tool-hardening.md`**: Split out unfinished test items (redirect-to-private, truncation edge cases); added `application/x-yaml` and `application/yaml` to content-type allowlist.
- **`docs/todo-roi.md`**: Moved agent tool-call hardening to Done section.
- **`evals/eval_tool_calling-data.json`, `eval_tool_calling-result.md`**: Updated results — overall accuracy 100% (26/26).
- **`scripts/eval_tool_calling.py`**: Error recovery request_limit bumped to 5.

### Removed
- **`docs/TODO-agent-toolcall-recursive-flow.md`**: All Phase C+D items implemented.
- **`docs/TODO-approval-interrupt-tests.md`**: All items implemented in `tests/test_approval.py`.
- **`docs/FIX-streaming-leading-token-drop.md`**: Superseded by `DESIGN-04-streaming-event-ordering.md`.
- **`docs/DESIGN-co-cli.md`**: Renamed to `DESIGN-00-co-cli.md`.

---

## [0.3.6] - 2026-02-08

### Added
- **Ollama tool-call resilience (Phase 1)**: Chat loop catches `ModelHTTPError` with status 400 (malformed tool-call JSON from small models) and reflects the error back to the model as a `UserPromptPart` for self-correction — reflection, not blind retry. Aligned with converged pattern from Codex, Aider, Gemini-CLI, and OpenCode. New config: `model_http_retries` (default 2, env `CO_CLI_MODEL_HTTP_RETRIES`).
- **`docs/TODO-ollama-tool-call-resilience.md`**: RCA for small-model tool-call failures, peer-system research (4 repos), two-phase solution spec.

### Changed
- **`docs/DESIGN-02-chat-loop.md`**: New "HTTP 400 Error Reflection" section documenting the reflection loop. `model_http_retries` added to config table.
- **`docs/DESIGN-01-agent.md`**: `model_http_retries` added to config table with cross-reference.
- **`docs/DESIGN-00-co-cli.md`**: "HTTP 400 reflection" note added to cross-cutting Tools section.

---

## [0.3.4] - 2026-02-08

### Added
- **Web tools**: `web_search` (Brave Search API) and `web_fetch` (HTML→markdown via `html2text`). New `co_cli/tools/web.py` module. Read-only, no approval. Brave API key configurable via `BRAVE_SEARCH_API_KEY` env var or `brave_search_api_key` setting. Known limitation: no private IP/SSRF protection in MVP.
- **Eval framework for tool-calling quality**: Statistical eval suite measuring tool-calling accuracy across all enabled tools. 26 golden JSONL cases across 4 dimensions (`tool_selection`, `arg_extraction`, `refusal`, `error_recovery`). Dual-agent architecture — deferred agent (all tools return `DeferredToolRequests`) for selection/args/refusal, normal agent for error recovery. Majority-vote scoring, absolute/relative gates, model comparison reports with per-dimension delta table.
- **`all_approval` parameter on `build_agent()`**: When `True`, all tools (including read-only) register with `requires_approval=True` — returns `DeferredToolRequests` without executing. Used by eval to avoid ModelRetry loops from missing credentials.
- **Eval baselines**: `evals/baseline-gemini.json` and `evals/baseline-ollama.json` — both models at 100% (26/26) on current golden set.
- **`docs/TODO-eval-tool-calling.md`**: Eval framework design doc — 12 sections covering JSONL format, scoring, CLI interface, golden cases, and key patterns.
- **Web tool tests** (`tests/test_web.py`): Validation (empty query, invalid URL scheme), missing API key, functional search/fetch integration.
- **`docs/DESIGN-13-tool-web-search.md`**: Web tools design doc — architecture, tool contracts, constants, error handling, security (SSRF limitation), config, file index.
- **LLM E2E web tests** (`tests/test_llm_e2e.py`): Web search tool selection and arg extraction tests.

### Changed
- **`co_cli/agent.py`**: `build_agent()` accepts `all_approval: bool = False`. Read-only tools registered with `requires_approval=all_approval`.
- **`CLAUDE.md`**: Added `docs/TODO-eval-tool-calling.md` to TODO inventory. Added `DESIGN-13-tool-web-search.md` to design docs index. Removed `TODO-tool-web-search.md` from TODO list (feature complete). Amended testing policy to document skip exception for API-dependent tests (Brave, Slack).

### Removed
- **`docs/TODO-tool-web-search.md`**: Replaced by `docs/DESIGN-13-tool-web-search.md` — feature fully implemented.

---

## [0.3.3] - 2026-02-07

### Fixed
- **History processor naming**: Renamed `trim_old_tool_output` → `truncate_tool_returns` and `sliding_window` → `truncate_history_window` — consistent `truncate_<target>` verb+noun pattern for chained processors sharing the same `list[ModelMessage]` signature. Updated across code, tests, and all design docs.

### Added
- **Agent factory tests** (`tests/test_agent.py`): 3 functional tests — tool registration completeness, approval flag verification, history processor attachment.
- **Slack test resilience** (`tests/test_slack.py`): Skip Slack API tests when `SLACK_BOT_TOKEN` is not set instead of failing. Non-API tests (validation, error paths) always run.

---

## [0.3.2] - 2026-02-07

### Added
- **Streaming output**: Replaced `agent.run()` + post-hoc `_display_tool_outputs()` with `agent.run_stream_events()` in new `_stream_agent_run()` helper. Tool calls/results display in real time, text streams token-by-token with `rich.Live` + `rich.Markdown` at 20 FPS throttle. Both the main chat loop and `_handle_approvals` resume path use the same streaming codepath.
- **E2E streaming tests**: `scripts/e2e_streaming.py` — two tests (plain text streaming, Markdown rendering via Live) that exercise the full streaming pipeline against a real LLM.
- **`docs/DESIGN-streaming-output.md`**: Streaming design doc — pydantic-ai API comparison (4 APIs evaluated), decision rationale for `run_stream_events()`, peer CLI analysis (Aider, Codex, Gemini CLI, OpenCode), Markdown rendering approach.
- **`docs/TODO-tool-naming.md`**: Tool naming standardisation TODO.

### Fixed
- **`usage_limits` hardcoded in `_stream_agent_run`**: Was reading `settings.max_request_limit` directly. Now accepts `usage_limits` as a parameter; `_handle_approvals` also threads it through. The `settings` read happens only in `chat_loop`.

### Changed
- **Tool naming standardised**: `search_drive` → `search_drive_files`, `draft_email` → `create_email_draft`, `post_slack_message` → `send_slack_message`, `get_slack_channel_history` → `list_slack_messages`, `get_slack_thread_replies` → `list_slack_replies`. Converged on `verb_noun` pattern. Updated agent registration, tests, and docstrings.
- **`_display_tool_outputs` removed**: Superseded by inline display in `_stream_agent_run` — tool output now appears in real time during streaming instead of post-hoc.
- **`_handle_approvals` resumes via streaming**: Was calling `agent.run()` (non-streaming). Now calls `_stream_agent_run()` so post-approval tool results and LLM follow-up also stream.
- **`README.md`**: Rewritten for v0.3.0+ — REPL slash commands table, Docker+subprocess sandbox with hardening details, automatic context governance, 4-layer config precedence, accurate module/tools inventory.
- **`docs/TODO-approval-flow-extraction.md`**: Updated line references, coupling table, and added Issues section reflecting streaming design tensions (`DisplayCallback` protocol needed for extraction).
- **`docs/DESIGN-00-co-cli.md`**: Updated for streaming architecture and tool renames.
- **`CLAUDE.md`**: Updated docs inventory — `TODO-streaming-tool-output.md` → `DESIGN-streaming-output.md`, added `TODO-tool-naming.md`.

### Removed
- **`docs/TODO-streaming-tool-output.md`**: Replaced by `docs/DESIGN-streaming-output.md` (broader scope — covers full streaming architecture, not just tool output).
- **`docs/REVIEW-sidekick-cli-good-and-bad.md`**: Patterns fully absorbed into codebase — review no longer relevant.

---

## [0.3.0] - 2026-02-07

### Added
- **Automatic context governance**: Two `history_processors` registered on the agent (`co_cli/_history.py`): `truncate_tool_returns` (sync, truncates large `ToolReturnPart.content` in older messages) and `truncate_history_window` (async, drops middle messages and replaces with LLM summary). Prevents silent context overflow without manual intervention.
- **`summarize_messages()` shared utility**: Disposable `Agent(model, output_type=str)` with zero tools — used by both `truncate_history_window` (automatic) and `/compact` (user-initiated). Configurable summarisation model via `summarization_model` setting.
- **3 new config fields**: `tool_output_trim_chars` (default 2000), `max_history_messages` (default 40), `summarization_model` (default `""` = primary model). Env vars: `CO_CLI_TOOL_OUTPUT_TRIM_CHARS`, `CO_CLI_MAX_HISTORY_MESSAGES`, `CO_CLI_SUMMARIZATION_MODEL`.
- **`docs/DESIGN-conversation-memory.md`**: Full design doc — peer landscape (Aider, Codex, Claude Code, Gemini CLI), gap analysis table, processor architecture, summarisation agent details (prompts, callsites, error handling), configuration reference with model resolution and disable semantics, session persistence roadmap.
- **18 functional tests** (`tests/test_history.py`): 13 pure tests (trim processor edge cases, static marker, find_first_run_end) + 5 LLM tests (summarise, sliding window compaction, structural validity, /compact end-to-end). No mocks — real `RunContext`, real `SubprocessBackend`, real LLM calls.

### Changed
- **`/compact` refactored**: Now calls `summarize_messages()` with primary model and builds a minimal 2-message history (summary `ModelRequest` + ack `ModelResponse`). Previously used `agent.run()` which could trigger tools and returned full history with summary appended.
- **`docs/DESIGN-00-co-cli.md`**: Updated §7.4 (history processors architecture), Settings class diagram, env var table, and module summary with `_history.py`.
- **`CLAUDE.md`**: Reorganised docs inventory — `DESIGN-conversation-memory.md` in Design section, new TODO entries, removed completed review.
- **`docs/REVIEW-sidekick-cli-good-and-bad.md`**: Rewritten to reflect current co-cli state — pattern-by-pattern adopted/partial/pending status instead of aspirational recommendations.

### Removed
- **`docs/TODO-conversation-memory.md`**: Replaced by `docs/DESIGN-conversation-memory.md`.
- **`docs/PYDANTIC-AI-CLI-BEST-PRACTICES.md`**: Content consolidated elsewhere.
- **`docs/REVIEW-co-cli-design-team-view.md`**: All P0/P1/P2 findings resolved; remaining items tracked in dedicated docs.

---

## [0.2.16] - 2026-02-07

### Added
- **No-sandbox subprocess fallback**: Shell tool no longer hard-requires Docker. New `SubprocessBackend` runs commands via `asyncio.create_subprocess_exec` with sanitized environment when Docker is unavailable. Automatic fallback with `sandbox_backend=auto` (default), explicit selection via `sandbox_backend` setting or `CO_CLI_SANDBOX_BACKEND` env var.
- **`SandboxProtocol` abstraction** (`co_cli/sandbox.py`): Runtime-checkable protocol with `isolation_level`, `run_command()`, `cleanup()`. `DockerSandbox` (full isolation) and `SubprocessBackend` (no isolation) both satisfy it. Zero caller changes — `tools/shell.py` and `agent.py` untouched.
- **Environment sanitization** (`co_cli/_sandbox_env.py`): `restricted_env()` allowlist (10 safe vars only) blocks CVE-2025-66032 pager/editor hijacking vectors. Forces `PAGER=cat`, `GIT_PAGER=cat`. `kill_process_tree()` sends SIGTERM→200ms→SIGKILL via `os.killpg()` process group.
- **Partial output on subprocess timeout**: After killing a timed-out subprocess, reads any buffered stdout before raising `RuntimeError` — matches Docker backend's behavior, gives the LLM context for self-correction.
- **Safe-command guard on isolation level**: `_is_safe_command()` auto-approval disabled when `isolation_level == "none"` — approval becomes the security layer without a sandbox.
- **19 new functional tests**: Subprocess backend execution, timeout, exit code, pipe, env sanitization, dangerous env blocking, stderr merge, workspace dir, isolation level, protocol conformance, config field, env var override, factory function, cleanup no-op, variable expansion, custom workspace dir.

### Changed
- **`co_cli/sandbox.py`**: Renamed `Sandbox` → `DockerSandbox`. All `import docker` moved inside class methods (lazy import — module loads without `docker` package). Backward-compatible `Sandbox = DockerSandbox` alias preserved.
- **`co_cli/deps.py`**: `sandbox: Sandbox` → `sandbox: SandboxProtocol`.
- **`co_cli/main.py`**: New `_create_sandbox()` factory with auto-detection (try Docker ping, fall back to subprocess with warning). `_handle_approvals()` checks `deps.sandbox.isolation_level != "none"` before auto-approving safe commands.
- **`co_cli/status.py`**: `StatusInfo.docker` field → `sandbox` field. `get_status()` reports active backend: `"Docker (full isolation)"` / `"subprocess (no isolation)"` / `"unavailable"`. `render_status_table()` shows "Sandbox" row with status and backend detail.
- **`co_cli/banner.py`**: `info.docker` → `info.sandbox`.
- **`co_cli/config.py`**: Added `sandbox_backend: Literal["auto", "docker", "subprocess"]` with `CO_CLI_SANDBOX_BACKEND` env var.
- **`docs/DESIGN-tool-shell.md`**: Status updated to reflect MVP complete. Future enhancements table: subprocess fallback, env sanitization, process group kill all marked Done. Integration section updated with `_create_sandbox()` factory.
- **`docs/DESIGN-00-co-cli.md`**: Added `sandbox_backend` to Settings class diagram and env var mapping table. Updated `CoDeps.sandbox` type to `SandboxProtocol`.
- **`docs/DESIGN-llm-models.md`**: "Docker sandbox" → "sandboxed environment" in profile description.
- **`docs/DESIGN-tool-google.md`**: `Sandbox.ensure_container()` → `DockerSandbox.ensure_container()` in analogy references.
- **`docs/DESIGN-tool-slack.md`**: `deps.py` import reference updated from `Sandbox` to `SandboxProtocol`.
- **`docs/TODO-shell-safety.md`**: Removed — all MVP items complete, only aspirational post-MVP enhancements remained.
- **`settings.example.json`** → **`settings.reference.json`**: Renamed to reflect its role as a user-facing schema reference with default values. Not loaded by code — copy to `~/.config/co-cli/settings.json` and customize.

---

## [0.2.14] - 2026-02-07

### Added
- **Shell safe-command whitelist**: `co_cli/_approval.py` with `_is_safe_command()` — auto-approves read-only shell commands (e.g. `ls`, `cat`, `git status`) matching `shell_safe_commands` prefixes without shell operators (`;`, `&`, `|`, `>`, `<`, `` ` ``, `$(`). UX convenience on top of Docker sandbox isolation.
- **`shell_safe_commands` setting**: New `config.py` field with 30 conservative defaults, `CO_CLI_SHELL_SAFE_COMMANDS` env var (comma-separated), and `CoDeps` field for injection into the approval flow.
- **`render_status_table()`**: Extracted status table rendering from `main.py` and `_commands.py` into `status.py`. Uses semantic style names (`accent`, `info`, `success`) instead of hardcoded colors.
- **`set_theme()`**: Runtime theme switching in `display.py` (called from `--theme` flag). Expanded theme palettes with `error`, `success`, `warning`, `hint` semantic styles.
- **`build_agent()` returns `tool_names`**: 3-tuple return `(agent, model_settings, tool_names)` — eliminates private `_function_toolset` access throughout codebase.
- **Approval flow tests**: 10 new tests in `tests/test_commands.py` — `_is_safe_command` unit tests covering prefix matching, multi-word prefixes, chaining/redirection/backgrounding rejection, exact match, partial-name rejection, empty safe list.
- **Shell hardening tests**: 20+ new functional tests in `tests/test_shell.py` — timeout, pipe, non-root, network isolation, capability drop, redirect, variable expansion, subshell, heredoc, stderr merge, Python script lifecycle, special chars, large output, empty output, workspace mapping.
- **`/release` skill**: `.claude/skills/release/release.md` — versioned release workflow invokable as `/release <version|feature|bugfix>`.

### Changed
- **`_commands.py`**: `CommandContext.tool_count` → `tool_names: list[str]`. `/status` and `/tools` use `render_status_table()` and sorted `tool_names` respectively.
- **`_approval.py`**: Hardened rejection list — added `&` (backgrounding), `>` / `<` (redirection), `\n` (embedded newlines) alongside original chaining operators.
- **`main.py`**: Adapted to 3-tuple `build_agent()`, uses `set_theme()` for `--theme` flag, uses `render_status_table()` for `co status`.
- **`docs/DESIGN-00-co-cli.md`**: Updated sandbox diagram, `CoDeps` class diagram, config table, dependency flow.
- **`docs/DESIGN-tool-google.md`**: Rewritten auth architecture — lazy credential resolution via `get_cached_google_creds()`.
- **`CLAUDE.md`**: Added design principles section, reference repos table, updated doc references.

### Renamed
- `docs/DESIGN-tool-shell-sandbox.md` → `docs/DESIGN-tool-shell.md` — broadened scope (sandbox backends, security model).
- `docs/TODO-approval-flow.md` → `docs/TODO-shell-safety.md` — refocused on shell execution safety (safe-prefix done, no-sandbox fallback scoped).

### Removed
- **`docs/TODO-tool-call-stability.md`** (previous commit): All items implemented.

---

## [0.2.12] - 2026-02-07

### Added
- **Project-level configuration**: `.co-cli/settings.json` in cwd overrides user config (`~/.config/co-cli/settings.json`). Shallow per-key merge via dict `|=`. `find_project_config()` checks cwd only — no upward directory walk. `project_config_path` module-level variable exposed for status display.
- **Project config in `co status`**: New `project_config` field in `StatusInfo`. Status table shows "Project Config: Active" row when `.co-cli/settings.json` is detected.
- **New tests**: `tests/test_config.py` — 6 functional tests covering project-overrides-user, env-overrides-project, missing config no-op, path detection, and malformed file handling.

### Fixed
- **Env var precedence**: `fill_from_env` model validator now always overrides file values. Previously env vars only filled missing fields, contradicting the documented precedence (`env vars > settings.json > defaults`).

### Changed
- **`DESIGN-00-co-cli.md`**: Updated §4.2 class diagram (added `sandbox_*` fields, `shell_safe_commands`, `Functions` class), §9.1 security diagram (env vars override, project config layer), §10.1 XDG directory structure (project config path).
- **`CLAUDE.md`**: Config precedence updated to 4-layer model.

---

## [0.2.10] - 2026-02-07

### Added
- **Slash commands**: 7 REPL commands (`/help`, `/clear`, `/status`, `/tools`, `/history`, `/compact`, `/yolo`) that bypass the LLM and execute instantly. New `co_cli/_commands.py` module with `CommandContext` / `SlashCommand` dataclasses, `COMMANDS` registry, and `dispatch()` function.
- **Tab completion**: `WordCompleter` with `complete_while_typing=False` for slash command names in the REPL. Triggered by Tab press — correct UX for a natural language input loop.
- **`/compact` command**: Summarizes conversation via LLM to reduce context window usage. Calls `agent.run()` with a summarization prompt, replaces history with compacted messages.
- **`/yolo` command**: Toggles `deps.auto_confirm` — same effect as picking `a` in the approval prompt, but available as a standalone toggle.
- **New tests**: `tests/test_commands.py` — 13 functional tests covering dispatch routing, all 7 handlers, yolo toggle, compact with LLM, and registry completeness.

### Changed
- **Banner hint**: Updated from `"Type 'exit' to quit"` to `"Type /help for commands, 'exit' to quit"`.
- **`DESIGN-00-co-cli.md`**: Added REPL Input Flow diagram, slash command architecture section (§4.5), and `_commands.py` to module summary (§13).

---

## [0.2.8] - 2026-02-06

### Added
- **Slack read tools**: `list_slack_channels`, `get_slack_channel_history`, `get_slack_thread_replies`, `list_slack_users` — four read-only tools (no approval) with `dict[str, Any]` + `display` return convention. Shared helpers: `_get_slack_client`, `_format_message`, `_format_ts`. Refactored `post_slack_message` to use `_get_slack_client`. New scopes: `channels:read`, `channels:history`, `users:read`.
- **Sandbox hardening**: Non-root execution (`user=1000:1000`), network isolation (`network_mode="none"` default, configurable to `"bridge"`), resource limits (`mem_limit=1g`, 1 CPU, `pids_limit=256`), privilege hardening (`cap_drop=["ALL"]`, `no-new-privileges`). Three new config settings: `sandbox_network`, `sandbox_mem_limit`, `sandbox_cpus` with env var overrides.
- **Custom sandbox image (`co-cli-sandbox`)**: New `Dockerfile.sandbox` based on `python:3.12-slim` with dev tools pre-installed (curl, git, jq, tree, file, less, zip/unzip, nano, wget). Default `docker_image` setting changed from `python:3.12-slim` to `co-cli-sandbox`.
- **Shell `sh -c` wrapping**: `Sandbox.run_command()` now executes via `["sh", "-c", cmd]` instead of raw `exec_run(cmd)`. Enables shell builtins (`cd`), pipes, redirects, and aliases that previously failed with "executable file not found".
- **Status module (`co_cli/status.py`)**: Extracted environment/health checks into `StatusInfo` dataclass + `get_status()`. Banner and `co status` command consume pure data — no duplicated probe logic.
- **Chained approval loop**: `_handle_approvals()` now loops (`while`, not `if`) so resumed agent runs that trigger additional deferred tool calls get their own approval round.
- **`ToolCallPart.args` type handling**: Approval prompt formatter handles `str | dict | None` args (was crashing on JSON-string args from some model providers).
- **Deferred approval flow**: Migrated from inline `_confirm.py` prompts to pydantic-ai `DeferredToolRequests` + `requires_approval=True`. Side-effectful tools (`run_shell_command`, `draft_email`, `post_slack_message`) now go through centralized `_handle_approvals()` in the chat loop with `[y/n/a(yolo)]` prompt and `ToolDenied` on rejection.
- **Obsidian search: folder and tag filtering**: `search_notes` accepts `folder` (restrict to subfolder) and `tag` (match YAML frontmatter or inline `#tag`). New `_extract_frontmatter_tags()` parser handles `tags: [a, b]` and list formats.
- **Obsidian snippet improvements**: `_snippet_around()` helper breaks at word boundaries instead of fixed character offsets.
- **Shell error propagation**: `Sandbox.run_command()` raises `RuntimeError` on non-zero exit code (was silently returning error string). `run_shell_command` tool wraps errors in `ModelRetry` so the LLM can self-correct.
- **Config `max_request_limit`**: New setting (default 25) with `CO_CLI_MAX_REQUEST_LIMIT` env var, used as `UsageLimits(request_limit=...)` in the chat loop.
- **Google auth lazy caching**: `get_cached_google_creds()` resolves credentials once on first call (module-level cache). Replaced eager `build_google_service()` — Google API clients are now built per-call in each tool, avoiding stale service objects.
- **Agent unknown-provider error**: `build_agent()` raises `ValueError` for unrecognized `llm_provider` values instead of silently falling through to Ollama.
- **New tests**: `test_search_notes_folder_filter`, `test_search_notes_tag_filter`, `test_search_notes_snippet_word_boundaries`, `test_shell_nonzero_exit_raises_model_retry`.
- **New E2E script**: `scripts/e2e_ctrl_c.py` — PTY-based test that sends SIGINT during approval prompt and during `agent.run()`, asserts process survives and returns to `Co ❯` prompt.
- **New TODO docs**: `TODO-conversation-memory.md`, `TODO-cross-tool-rag.md`.

### Fixed
- **Banner version stale after bump**: `VERSION` now reads `pyproject.toml` directly via `tomllib` (was `importlib.metadata`, which required reinstall to reflect changes).
- **Ctrl-C exits process instead of returning to prompt**: `asyncio.run()` in Python 3.11+ delivers SIGINT as `asyncio.CancelledError`, not `KeyboardInterrupt`. Chat loop now catches both. Approval prompt (`Prompt.ask()`) temporarily restores the default SIGINT handler so Ctrl-C can interrupt synchronous `input()`. Safety-net `except KeyboardInterrupt` wraps `asyncio.run()` for edge cases. See `DESIGN-00-co-cli.md` §8.

### Changed
- **`DESIGN-00-co-cli.md`**: Added complete tool return type reference table (all 16 tools) to §5.1.1 with `_display_tool_outputs()` transport-layer separation explanation. Expanded tool architecture graph, cloud tool summary, and module summary to include all Slack, Gmail, and Calendar tools.
- **`DESIGN-tool-slack.md`**: Expanded from single-tool doc to full five-tool reference with shared helpers, setup guide, scope table, and test inventory.
- **`DESIGN-tool-shell-sandbox.md`**: Added container hardening documentation — non-root, network isolation, resource limits, privilege dropping, and configurable settings.
- **`TODO-tool-call-stability.md`**: Marked sandbox hardening phases 1–3 as done.
- **`TODO-slack-tooling.md`**: Marked Phase 1 (core reads) as done.
- **Obsidian tool return type**: `search_notes` and `list_notes` now return `dict[str, Any]` with `display`, `count`, `has_more` fields (was `list[dict]` / `list[str]`). `search_notes` returns empty dict on no results instead of raising `ModelRetry`.
- **Agent system prompt**: Added "Tool Output" section instructing the LLM to show `display` verbatim and respect `has_more`.
- **Config env override logic**: Fixed `Settings.from_file()` to check `field not in data or data[field] is None` (was `not data.get(field)`, which treated `0` and `""` as missing).
- **CoDeps**: Removed `google_drive`, `google_gmail`, `google_calendar` fields — replaced by `google_credentials_path`. Added comment clarifying `auto_confirm` purpose.
- **Tests**: Removed all `@pytest.mark.skipif` guards (Docker, GCP, Slack) per testing policy. Simplified test context setup — no more per-test credential/service building.
- **CLAUDE.md**: Streamlined — removed inline module/tool tables (covered by DESIGN docs), tightened coding standards.
- **Theming: Rich `Theme` migration**: Replaced manual `_c(role)` color resolver with idiomatic `Console(theme=Theme(...))`. Semantic style names (`"status"`, `"accent"`, `"shell"`, etc.) are now resolved natively by Rich. Added `"shell"` semantic style for shell output panel borders.
- **Design docs**: Updated `DESIGN-00-co-cli.md`, `DESIGN-tool-obsidian.md`, `DESIGN-tool-shell-sandbox.md` to reflect new approval flow, obsidian features, and shell error handling. Restructured `DESIGN-00-co-cli.md`: promoted §7.5+§7.6 (interrupt recovery + signal handling) into new **§8 Interrupt Handling**, renumbered §8–§12 → §9–§13.
- **TODO docs**: Trimmed `TODO-approval-flow.md` and `TODO-tool-call-stability.md` — removed completed items, kept only remaining work.

### Removed
- **`docs/TODO-structured-output.md`**: Problem already solved by `_display_tool_outputs()` transport-layer separation; proposed `CoResponse` union carried Gemini compatibility risk for no gain.
- **`co_cli/tools/_confirm.py`**: Inline approval prompt — superseded by `DeferredToolRequests` in chat loop.
- **`docs/TODO-obsidian-search.md`**: Merged into `TODO-cross-tool-rag.md`.
- **`docs/FIX-general-issues-team-work-codex-claude-code.md`**: All tracked issues resolved or moved to standalone docs.

---

## [0.2.7] - 2026-02-06

### Fixed
- **Telemetry SQLite lock contention**: `SQLiteSpanExporter` now uses WAL journal mode and `busy_timeout=5000ms` on every connection, preventing `database is locked` errors when `co tail`/Datasette read while the chat session writes spans. Export uses `executemany` (single batch write) with 3-attempt exponential backoff retry for transient lock failures.
- **Banner version stale**: `display_welcome_banner()` and `main.py` `service.version` now read from `importlib.metadata` instead of hardcoded strings, keeping the version in sync with `pyproject.toml` automatically.

### Changed
- **`docs/DESIGN-otel-logging.md`**: Added "Concurrent Access (WAL Mode)" section documenting WAL rationale, pragma settings, retry strategy, and batch insert design.

---

## [0.2.5] - 2026-02-06

### Added
- **`co tail` command**: Real-time span viewer (`co_cli/tail.py`, 260 lines) — tail agent spans live from a second terminal with `--tools-only`, `-v` verbose, and `-n`/`-l` non-follow modes.
- **`docs/DESIGN-tail-viewer.md`**: Tail viewer design doc with span attribute reference and troubleshooting guide.
- **`docs/TODO-tool-call-stability.md`**: Comprehensive stability doc — ModelRetry design principle, retry budget, shell error propagation, system prompt, Obsidian display migration, loop guard, sandbox hardening.

### Changed
- **Inference params**: GLM-4.7-Flash switched from general conversation profile (`temp=1.0, top_p=0.95`) to Terminal/SWE-Bench Verified profile (`temp=0.7, top_p=1.0`) for better tool-call accuracy.
- **Agent retry budget**: `retries=settings.tool_retries` set at agent level (was default 1).
- **`README.md`**: Expanded usage section with `co` command explanation, added `co tail`/`co traces` docs.
- **`AGENTS.md`**: Updated commands, testing guidance, and security tips to reflect current state.
- **`docs/TODO-approval-flow.md`**: Expanded with post-session-yolo context.
- **`docs/TODO-streaming-tool-output.md`**: Expanded with event_stream_handler design.
- **`docs/DESIGN-00-co-cli.md`**: Updated architecture with display/banner/tail modules.

### Removed
- **`docs/TODO-retry-design.md`**: Merged into `TODO-tool-call-stability.md`.
- **`docs/TODO-session-yolo.md`**: Superseded by implemented session-yolo in v0.2.0.

---

## [0.2.4] - 2026-02-06

### Added
- **Theming**: Light/dark color themes with `--theme`/`-t` flag, `CO_CLI_THEME` env var, and `theme` setting in `settings.json`. Light theme uses blue accents and dark orange status; dark theme uses cyan accents and yellow status.
- **ASCII art banner**: Theme-aware welcome banner — block characters (`█▀▀`) for dark, box-drawing characters (`┌─┐`) for light — rendered as a Rich Panel with model info and version.
- **`co_cli/display.py`**: Shared `Console` instance, `_COLORS` theme dict, `_c()` color resolver, Unicode indicators (`❯ ▸ ✦ ✖ ◈`), and display helpers (`display_status`, `display_error`, `display_info`).
- **`co_cli/banner.py`**: `display_welcome_banner()` with per-theme ASCII art selection.
- **`docs/DESIGN-theming-ascii.md`**: Comprehensive design doc covering architecture, color semantics, module layout, and a-cli reference.

### Changed
- **`main.py`**: Uses shared `console` from `display.py` (was local `Console()`), themed welcome banner (was two inline `console.print` lines), `Co ❯` prompt (was `Co > `).
- **`tools/_confirm.py`**: Uses shared `console` from `display.py` (was private `_console = Console()`).
- **`config.py`**: Added `theme` field (default: `"light"`) with `CO_CLI_THEME` env var mapping.
- **`CLAUDE.md`**: Added `display.py`/`banner.py` to Core Flow, color semantics to Coding Standards, updated design doc list.

---

## [0.2.2] - 2026-02-06

### Fixed
- **search_drive empty-result crash**: `search_drive` no longer raises `ModelRetry` on zero results — returns `{"count": 0}` instead. Previously, two empty searches could exhaust the retry budget and crash the agent with `UnexpectedModelBehavior`.
- **Google test skip bug**: `HAS_GCP` now checks all three credential sources (explicit path, `google_token.json`, ADC) instead of only `settings.google_credentials_path`. Google tests no longer skip when credentials exist.
- **Stale test assertions**: Removed `try/except ModelRetry` workarounds in Drive tests that masked the old empty-result behavior.

### Added
- **`test_drive_search_empty_result`**: Functional test hitting real Drive API with a nonsense query, asserting `count=0` dict return (no exception).
- **`docs/TODO-retry-design.md`**: Design doc covering ModelRetry semantics (retry vs return empty), industry best practices across pydantic-ai/Anthropic/OpenAI/LangGraph, and full tool audit.

### Removed
- **`tests/test_agent.py`**: Unit tests checking model types and settings values with monkeypatch — replaced by LLM E2E tests.
- **`tests/test_batch1_integration.py`**: Unit tests checking CoDeps construction with monkeypatch.

### Changed
- **`docs/DESIGN-llm-models.md`**: Updated Testing and Files sections to reference `test_llm_e2e.py` instead of deleted `test_agent.py`.
- **`CLAUDE.md`**: Added credential resolution note to Testing Policy; added `TODO-retry-design.md` to Design Docs list.

---

## [0.2.0] - 2026-02-05

### Added
- **Gmail inbox tools**: `list_emails` and `search_emails` for reading and searching Gmail (Gmail was previously write-only via `draft_email`).
- **Calendar search**: `search_calendar_events` tool with keyword search, configurable date range, and max results.
- **Google auth auto-setup**: `ensure_google_credentials()` in `google_auth.py` — automatically runs `gcloud auth application-default login` on first use if no token exists.
- **Design docs**: `DESIGN-tool-google.md` (Google tools architecture + setup guide) and `DESIGN-tool-slack.md`.
- **Research doc**: `RESEARCH-cli-agent-tools-landscape-2026.md` — 10-agent competitive analysis, tool roadmap (Batches 7-12), and agentic patterns survey.

### Changed
- **RunContext migration (Batch 3-4)**: All Google and Slack tools migrated from `tool_plain()` to `agent.tool()` with `RunContext[CoDeps]` pattern.
- **File layout**: Extracted `comm.py` junk drawer into separate `google_drive.py`, `google_gmail.py`, `google_calendar.py`, `slack.py` modules.
- **Calendar tool refactored**: Extracted `_get_calendar_service`, `_format_events`, `_handle_calendar_error` helpers for reuse across `list_calendar_events` and `search_calendar_events`.
- **Google auth centralized**: Single `google_auth.py` module with shared credentials and service builder (was duplicated across tool files).
- **CoDeps expanded**: Added `google_drive`, `google_gmail`, `google_calendar`, `slack_client` fields — all API clients built once at startup via `create_deps()`.

### Fixed
- **API-not-enabled errors**: All Google tools now detect "API not enabled" (`accessNotConfigured`) errors and return actionable `ModelRetry` messages with the exact `gcloud services enable` command for each API.
- **Google setup docs**: Added step-by-step guide covering token acquisition, GCP project discovery, API enablement, and troubleshooting table with 7 common failure scenarios.

---

## [0.1.0] - 2026-02-03

### Added
- **Core CLI**: Interactive chat loop using `typer`, `rich`, and `prompt_toolkit`.
- **Intelligence**: Dual-engine LLM support using `pydantic-ai`.
    - **Local**: Ollama (Llama 3 default) for privacy-first operations.
    - **Cloud**: Google Gemini (via `google-genai`) for complex reasoning.
- **Configuration**:
    - Centralized `settings.json` following XDG standards (`~/.config/co-cli/`).
    - Robust fallback to environment variables (`.env`) for backward compatibility.
- **Sandboxing**: Docker-based execution environment for safe shell command running (`python:3.12-slim`).
- **Tools & Skills**:
    - **Obsidian**: RAG over local Markdown notes (`list_notes`, `read_note`).
    - **Google Drive**: Hybrid semantic/metadata search and file reading.
    - **Communication**: Slack message sending and Gmail drafting (with human-in-the-loop confirmation).
    - **Calendar**: Listing today's events.
- **Observability**:
    - Full OpenTelemetry tracing stored in a local SQLite database (`~/.local/share/co-cli/co-cli.db`).
    - `co logs` command to launch a local Datasette dashboard for trace inspection.
- **System Health**: `co status` command to verify tool connections and configuration.

### Security
- **Privacy**: Local-first design; logs and vector search (if added later) stay on-device.
- **Safety**: High-risk actions (sending emails, posting to Slack, shell commands) require explicit user confirmation.
