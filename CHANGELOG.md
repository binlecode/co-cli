# Changelog

## [Unreleased]

## [0.8.230]

### Online reviewer + dream daemon MVP

- **Dream daemon** — per-`CO_HOME` out-of-process daemon (`co dream start/status/stop`); POSIX double-fork detach; Unix socket IPC; SIGTERM grace; PID + provenance file
- **KICK-based reviewer dispatch** — two domain counters (`turns_since_memory_review`, `iters_since_skill_review`) in `CoSessionState`; mid-session threshold trips and session-end always-fire both send durable KICK files to `$CO_HOME/daemons/dream/queue/`
- **Domain reviewer agents** — `MEMORY_REVIEW_SPEC` (memory_search + memory_manage) and `SKILL_REVIEW_SPEC` (skill_view + skill_manage + memory_search); domain-specific prompts; both run via `run_standalone` with `requires_approval=False`
- **Retry/backoff** — per-call `asyncio.timeout`; failed retries increment attempt counter on queue file; after `max_retry_attempts` file moves to `queue/failed/`; counter survives daemon restart
- **Recall metrics** — `MemoryItem.recall_days` (deduped ISO-date list); skill usage sidecar extended with `recall_days` + `bump_recall`; updated on `memory_search`, `skill_view`, and `/skill-name` slash
- **Inline counter resets** — `memory_manage(create|append|replace)` resets memory counter; `skill_manage(create|edit|patch)` resets skill counter; no crossover
- **Auto-spawn + inspectability** — bootstrap auto-spawns daemon when `dream.enabled=true`; first-spawn notice; `Dream:` banner line (3 states); `/dream` slash read-only inspection
- **Dead code removed** — `session_review.py`, `session_review_prompts.py`, in-process `background_review_task`, `_maybe_run_session_review`, `auto_approve_skill_ops`/`auto_approve_knowledge_ops` flags
- **Stale tests migrated** — 5 stale flow test files updated; deleted symbols fully purged
- **`dream.md` spec** — fully rewritten to document both the daemon reviewer layer and the batch cycle

## [0.8.228]

### Agent loop caps — iteration cap + tool-call hard-stop

- **Iteration cap** — `LlmSettings.max_iterations_per_turn` (default 90, `0` disables); `CO_LLM_MAX_ITERATIONS_PER_TURN` env override; hard ceiling on total LLM calls per user turn
- **Tool-call hard-stop** — `TOOL_CAP_HARD_STOP_CONSECUTIVE = 3`; after 3 consecutive tool-cap-violating llm_iterations, the turn is killed (not looped indefinitely)
- **Consecutive tracking** — `CoRuntimeState.consecutive_tool_cap_violations`; incremented by `CoToolLifecycle.after_node_run` on each violating `CallToolsNode`, reset to 0 on any clean step; cleared by `reset_for_turn()`
- **Exit paths** — `_check_turn_caps()` in `run_turn` checks both flags after `_run_approval_loop`, before `_length_retry_settings`; both emit `frontend.on_status()` with human-readable reason
- **Tests** — `test_flow_tool_call_limit.py` extended (3 new tests); `test_flow_iteration_cap.py` new (5 tests: 3 unit + 2 integration via real `FunctionModel` stub agents)

## [0.8.226]

### Concurrent-safe default + dispatch backstop

- **Default flipped** — `@agent_tool` now defaults `is_concurrent_safe=True`; 33 redundant explicit annotations are now accurate-but-optional (cleanup deferred)
- **Explicit opt-out** — `code_execute`, `file_write`, `file_patch` each carry `is_concurrent_safe=False` with an above-line comment explaining why
- **`is_read_only` shortcut** — `is_read_only=True` silently coerces `is_concurrent_safe=True`; no longer an error to omit the flag alongside it
- **Dispatch backstop** — `tool_dispatch_sem: asyncio.Semaphore(10)` on `CoDeps`; `_dispatch_capped` wrapper acquires it before every tool invocation; forked agents (reviewer, curator) share by reference so session-wide cap is bounded
- **Production bug fixed** — `_dispatch_capped` now uses `inspect.iscoroutinefunction(fn)` to branch; unconditional `await fn(...)` would have raised `TypeError` for all sync tools at pydantic-ai dispatch time
- **Tests** — 8 new behavioral tests in `test_flow_agent_tool_concurrent_default.py`; 20 tests in `test_flow_todo.py` converted to async (`@pytest.mark.asyncio` + `await`) after tool functions became async-wrapped

## [0.8.224]

### UAT evals phase-1 refactor — mission-tenet alignment

- **Mission-tenet citations** added to all 6 phase-1 eval module docstrings (`eval_daily_chat.py`, `eval_session_continuity.py`, `eval_memory.py`, `eval_skills.py`, `eval_background.py`, `eval_trust_visibility.py`)
- **W1.D `dream_propagates_to_recall`** replaces `dream_callable_smoke`; real `run_dream_cycle(dry_run=False)` + structural XOR gate (exactly one original archived) + judged agent recall turn (SOFT_FAIL on borderline miss)
- **W1.E `tool_spill_summary`** new case: oversized `memory_view` triggers spill; asserts `PERSISTED_OUTPUT_TAG` in `ToolReturnPart` + spill file created + judge rubric on coherent fact-citing answer
- **W2.D `rehydrate_uses_context`** upgraded: judged follow-up verifies agent uses rehydrated session context (DEPLOY_77 marker)
- **W2.E `compact_quality_holds`** upgraded: Lighthouse marker seeded pre-inflation; judged post-compact turn confirms marker survived compaction summarization
- **W3.G `forget_propagates_to_recall`** new case: 3-turn recall→delete→recall; judged assertion that agent does not cite deleted artifact
- **W6.C `deny_blocks_execution`** new case: `_DenyFrontend` exercises real approval-resume deny path; structural seed-survived check + judged denial-acknowledgement rubric
- **`kind: memory` discriminator removed** from memory frontmatter (`frontmatter.py`, `item.py`) and all fixtures/seeds — memory and session are peer tiers with no top-level discriminator
- **Phase-1 case count**: 26 → 29 (+3 net); judge-using cases: 2/26 → 9/29

## [0.8.222]

### TUI status surface — `PromptSession` footer toolbar

- **`StatusSnapshot`** frozen dataclass in `co_cli/display/core.py` — typed contract for footer content (`session_label`, `mode`, `context_pct`, `background_task_count`, `approval_count`)
- **`Frontend.update_status(snapshot)`** added to the protocol; implemented in both `TerminalFrontend` and `HeadlessFrontend`
- **`TerminalFrontend.render_footer_toolbar()`** produces plain-text compact footer (`a1b2c3d4 · idle · ctx 47% · 2 bg · 1 approval`); optional fields degrade when zero or `None`
- **`_build_status_snapshot(deps, mode)`** helper in `co_cli/main.py` assembles snapshot from `CoDeps` at four lifecycle push points (startup, pre-prompt, turn-start active, post-turn idle)
- **`PromptSession(bottom_toolbar=frontend.render_footer_toolbar)`** wired in `_chat_loop`; session label shows `"—"` before first persist
- **15 new tests** in `tests/test_display.py` covering all render paths, degenerate inputs, and snapshot assembly from real `CoDeps`

## [0.8.220]

### Deferred-interaction regression coverage — `clarify` e2e + `prompt_question` contract

- **`test_clarify_deferred_approval_routing`** (unit, deterministic): constructs `DeferredToolRequests` directly and calls `_collect_deferred_tool_approvals`; asserts routing to `prompt_question`, correct `QuestionPrompt` construction, and `ToolApproved(override_args={"user_answers": [...]})` injection.
- **`test_prompt_question_frontend_contract`** (unit): verifies `HeadlessFrontend` returns `question_answer`, records `last_question`, and increments `question_call_count`.
- **`test_clarify_deferred_resume_end_to_end`** (LLM smoke): asserts `clarify` never routes through the standard approval path (`approval_calls == 0`), catching seam failures regardless of model behavior.

## [0.8.218]

### `MemoryArtifact` → `MemoryItem` rename — artifact semantic layer removed from `co_cli/memory/`

- **`co_cli/memory/artifact.py` → `item.py`** via `git mv`; class `MemoryArtifact` → `MemoryItem`, enum `ArtifactKindEnum` → `MemoryKindEnum`, functions `load_artifacts` / `load_memory_items`, `filter_artifacts` / `filter_memory_items`, `format_artifact_row` / `format_memory_item_row`.
- **Frontmatter field** `artifact_kind:` → `memory_kind:` in all `.md` memory files; `render_artifact_file` → `render_memory_item_file`, `artifact_to_frontmatter` → `memory_item_to_frontmatter`.
- **Config** `max_artifact_count` / `CO_MEMORY_MAX_ARTIFACT_COUNT` → `max_item_count` / `CO_MEMORY_MAX_ITEM_COUNT`.
- **`MemoryStore`** methods `list_artifacts` → `list_memory_items`, `search_artifacts` → `search_memory_items`; **`IndexStore`** `list_artifacts` → `list_items`.
- **`similarity.py`** `find_similar_artifacts` → `find_similar_memory_items`; **`decay.py`**, **`archive.py`**, **`dream.py`** all updated (imports, local vars, docstrings).
- **Tool surface** (`recall.py`, `manage.py`, `view.py`): `_list_artifacts` → `_list_memory_items`, `_search_artifacts` → `_search_memory_items`; display strings updated.
- **`commands/memory.py`**, **`commands/core.py`**, **`context/rules/04_tool_protocol.md`**, **`skills/session_review_prompts.py`**: "memory artifact(s)" → "memory item(s)".
- **Tests** `test_flow_artifact_manage.py` → `test_flow_memory_item_manage.py`, `test_flow_memory_artifacts_waterfall_cap.py` → `test_flow_memory_items_waterfall_cap.py`; all test imports updated.
- **Evals** fixtures directory `knowledge/` → `memory/`; `artifact_kind:` frontmatter updated in 6 fixture `.md` files; `_fixtures.py` path updated.
- **Spec docs** (`memory.md`, `dream.md`, `observability.md`, `config.md`, `01-system.md`, `bootstrap.md`, `tui.md`, `prompt-assembly.md`, `core-loop.md`, `tools.md`): all `knowledge.*` config prefixes → `memory.*`, `knowledge/` paths → `memory/`, stale file paths updated, missing config rows added, duplicate phantom rows removed.
- **`noreason` temperature=0** added to qwen3.5 Ollama settings — eliminates LLM output non-determinism in compaction summarization and judge calls.

## [0.8.216]

### Rename `co_cli/persistence/` → `co_cli/fileio/`

- **Package renamed** `co_cli/persistence/` → `co_cli/fileio/` — the old name overstated scope; `fileio` is accurate and unambiguous.
- **7 source import sites** migrated from `co_cli.persistence.atomic` to `co_cli.fileio.atomic` (`tool_io.py`, `tools/system/skills.py`, `memory/service.py`, `memory/dream.py`, `skills/session_review.py`, `skills/usage.py`, `skills/curator.py`).
- **Doc references updated** in `agent_docs/code-conventions.md` and `co_cli/tools/files/write.py` docstring.
- **Test file renamed** `test_atomic_write_persistence.py` → `test_atomic_write.py`; import updated.

## [0.8.214]

### Skill-env propagation + single subprocess env chokepoint + `shell` → `shell_exec` rename

- **Skill-env now actually reaches `shell_exec` and `task_start` subprocesses.** `SkillInfo.skill_env` frontmatter was spec'd in `docs/specs/skills.md` but silently dropped by the host-allowlist (`SAFE_ENV_VARS`) — fixed end-to-end.
- **`co_cli/tools/shell_env.py`**: `_SAFE_ENV_VARS` → public `SAFE_ENV_VARS`; new `build_subprocess_env(extra_env=...)` is the canonical env builder for every co-cli subprocess (refuses overlay keys that shadow host allowlist, logs `subprocess.env_shadow_refused`).
- **`co_cli/deps.py`**: new `CoRuntimeState.active_skill_env` field — turn-scoped, mirrors `active_skill_name` lifecycle. Set at skill dispatch (`main.py`), cleared by `cleanup_skill_run_state` (`skills/lifecycle.py`).
- **Subprocess chokepoint normalization**: `shell_backend.py`, `tools/background.py`, `tools/files/read.py` (rg + grep), `tools/files/write.py` (ruff lint) all route through `build_subprocess_env()`. Only deliberate bypass: `tools/google/_auth.py` (gcloud OAuth needs full host env — commented).
- **`shell` tool renamed to `shell_exec`** for naming convention consistency (`<noun>_<verb>` matches every other tool: `file_read`, `web_fetch`, `knowledge_search`, …). Rename touched approvals, categories, display, tool-result markers, prompt_text, toolset, deps docstring, tools.md spec, and 12 test files.
- **Eval fixes (`evals/eval_skills.py`)**: skill body references real tool name `shell_exec`; W4.A judged by `TOOL_TURN_BUDGET_S` (tool-call turn, ~60s) rather than `TURN_BUDGET_S` (no-tool turn, 35s) — matches `eval_memory.py` pattern.
- **Cleanup**: `evals/_outputs/` added to `.gitignore`; stale tracked artifacts (`smoke-*` jsonl, `tmp/tmp_test.py`) untracked.

## [0.8.212]

### Memory module refactor — `knowledge` → `memory`, session tier promotion, IndexStore facade

- **`co_cli/index/`** — new infrastructure facade: `IndexStore` (SQLite + FTS5 + sqlite-vec), `RetrievalService`, `EmbeddingService`, `Chunk`, public `search_util.py` / `stopwords.py`
- **`co_cli/memory/`** — domain store: `MemoryStore` composes `IndexStore`; two-pass search policy (`search_artifacts`); `IndexSourceEnum.MEMORY = 'memory'`; `MemoryArtifact` replaces `KnowledgeArtifact`
- **`co_cli/session/`** — new domain: `SessionStore` composes `IndexStore`; `chunk_session()` returns `list[Chunk]` directly (drops `SessionChunk`); browser, transcript, persistence, filename modules
- **Tool surface**: `memory_search`, `memory_view`, `memory_manage` (renamed from `knowledge_*`); `session_search`, `session_view` promoted to own tier under `co_cli/tools/session/`
- **Config**: `Settings.memory_path`, `MemorySettings`, `MEMORY_DIR`, `CO_MEMORY_*` env vars; `co_cli/config/knowledge.py` deleted
- **Bootstrap / Deps**: `CoDeps` gains `index_store`, `session_store`; `memory_dir` replaces `knowledge_dir`; canon path `souls/{role}/canon/` replaces `memories/`
- **System prompt assets**: `04_tool_protocol.md` and `skills/triage.md` updated to `memory_*` tool names

## [0.8.210]

### Startup banner — Knowledge → Memory with counts

- **`Memory:` row** replaces `Knowledge:` in the welcome banner; shows backend label, optional degradation suffix, and live knowledge/session counts
- **`MemoryStore.count_docs(source)`** — new lightweight `SELECT COUNT(*)` method; used for both knowledge and session counts at startup
- **`display_welcome_banner()`** — gains `knowledge_count` and `session_count` keyword parameters; counts omitted automatically when backend is `grep` (no index)
- **4 banner rendering tests** in `tests/test_flow_bootstrap_banner.py` locking all scenarios (indexed, degraded, grep, zero counts)

## [0.8.208]

### Agent lifecycle / spec split

- **`co_cli/agent/`** — `agents/` renamed to `agent/`; `_native_toolset.py` → `toolset.py`; `tool_call_limit.py` moved to `tools/`
- **`OrchestratorSpec` + `TaskAgentSpec`** — independent frozen dataclasses in `agent/spec.py`; no shared base; all collection fields are `tuple[...]`
- **`build_orchestrator` / `build_task_agent`** — typed builders in `agent/build.py`; task builder resolves `spec.tool_names` against `TOOL_REGISTRY_BY_NAME` (fail-loud on unknown names), filters by config credentials, registers tools with `requires_approval=False`
- **`run_in_turn` / `run_standalone` / `_run_attempt`** — typed runners in `agent/run.py`; depth check + usage merge owned by `run_in_turn`; `run_standalone` skips both; `_run_attempt` is the inner primitive for `web_research`'s single-span two-attempt retry
- **`ORCHESTRATOR_SPEC`** — declarative record in `agent/orchestrator.py` (5 static builders, 2 per-turn, 5 history processors)
- **3 in-turn task specs** (`WEB_RESEARCH_SPEC`, `KNOWLEDGE_ANALYZE_SPEC`, `REASON_SPEC`) in `tools/agents/delegation.py`; `knowledge_analyze` and `reason` reduced to one-liners
- **`SESSION_REVIEW_SPEC`** in `skills/session_review.py`; `CURATOR_SPEC` in `skills/curator.py`; domain ownership matches lifecycle caller
- **Decorator flip** — `delegation=` kwarg removed from `@agent_tool`; `ToolInfo.delegation` field removed; `TOOL_REGISTRY_BY_NAME` populated at import time alongside `TOOL_REGISTRY`
- **Legacy deleted** — `build_agent`, `discover_delegation_tools`, `_run_agent_in_turn`, `_run_agent_standalone`, `_delegate_agent`; `test_flow_delegation_discovery.py` removed

## [0.8.206]

### Retire OTel — structured-log tracing + decorator-based spans

- **OTel removed** — `opentelemetry-sdk` dropped as direct dependency; `telemetry.py` and `viewer.py` deleted; `Agent.instrument_all()` removed
- **`co_cli/observability/tracing.py`** — new: `@trace` decorator (sync + async), `ContextVar`-based span stack, `RotatingFileHandler` JSON spans log at `~/.co-cli/logs/co-cli-spans.jsonl`, recursive redaction of nested JSON attributes
- **`co_cli/observability/capability.py`** — new: `ObservabilityCapability` wired alongside `CoToolLifecycle`; all 9 pydantic-ai lifecycle hooks with correct return types; capability ordering invariant documented
- **29 OTel touchpoints migrated** — 24 manual span sites converted to `@trace` decorators or events; 5 `get_current_span()` sites swapped to `current_span()`
- **`co tail`** — refactored to read JSON spans log; rotation-safe inode tracking; `--detail` reads new `co.agent.*`/`co.model.*`/`co.tool.*` attribute vocabulary; no `--tree`
- **`co trace <trace_id>`** — new snapshot tree command; reads live log + rotated backups; renders indented tree sorted by `start_ts`
- **`co traces`** — deleted; `co trace` replaces it
- **Test suite** — harness rewired; OTel-coupled tests updated; 4 new test files covering tracing, capability, tail, and trace command

## [0.8.204]

### Agent spec + inclusive bundle

- **`docs/specs/agents.md`** — new agent lifecycle spec: build, run, orchestration, agent-as-tool contract
- **All specs updated** — 01-system, bootstrap, compaction, config, core-loop, dream, memory, observability, personality, prompt-assembly, skills, tools refreshed to current state
- **`co_cli/agent/` package** — new modules: `build.py`, `run.py`, `orchestrator.py`, `spec.py`, `__init__.py`; `_runner.py` removed
- **Evals refresh** — new: `eval_background.py`, `eval_daily_chat.py`, `eval_memory.py`, `eval_session_continuity.py`, `eval_skills.py`, `eval_trust_visibility.py`, `_report.py`, `_trace.py`; stale evals removed
- **Tests** — deleted `test_flow_delegation_discovery.py`, `test_flow_skill_protocol.py`; new `test_agent_build_task_agent.py`; all remaining tests updated
- **`agent_docs/system-workflows-to-test.md`** removed; `review.md` updated
- **`docs/REPORT-*.md`** stale eval reports removed; active exec-plans added

## [0.8.203]

### Security fixes — SSRF protection and background task shell policy

- **DNS-rebinding SSRF fix** — `SSRFSafeNetworkBackend` (httpcore layer) resolves and validates the IP before every TCP connect, closing the TOCTOU gap between `is_url_safe()` pre-check and the actual connection. `ssrf_redirect_guard` rejects redirect targets that resolve to private/internal addresses.
- **`make_ssrf_safe_transport()`** — factory injects `SSRFSafeNetworkBackend` into the `httpx.AsyncHTTPTransport` pool; `web_fetch` uses this transport for every request.
- **Background task shell policy** — `task_start` now calls `evaluate_shell_command` before spawning; commands that match a `DENY` policy return a `tool_error` instead of executing, matching the behaviour of `run_shell_command`.

## [0.8.201]

### Fix four bugs in agent toolset construction

- **Misleading error message** — `build_agent` delegation-path error no longer advises "Pass toolset and tool_index" when `instructions`/`tool_fns` is set without `output_type`; message now says `"output_type is required when instructions or tool_fns is passed."`
- **Silent MCP tool loss on resume** — `_approval_resume_filter` now passes tools with no `tool_index` entry through on resume turns (`entry is None or …ALWAYS`) instead of silently dropping them
- **Duplicate `requires_config` predicate** — extracted `_config_requirement_met(info, config)` in `_native_toolset.py`; used by both `_build_native_toolset` and `discover_delegation_tools`
- **Stale docstring** — `build_mcp_entries` docstring corrected from `tool_index.is_concurrent_safe` to `tool_index[name].is_concurrent_safe`

## [0.8.200]

### Turn-boundary session review + public surface cleanup
- **Turn-boundary review** — session review now fires every ~5 tool-call iterations as a background task (`asyncio.create_task`) instead of once inline at REPL exit. Counter accumulates via `TurnResult.tool_iterations` (per-segment accumulator on `_TurnState`; multi-segment turns, approval cycles, and compaction-recovery are all immune). Single in-flight: skip if prior review task is still running, counter is NOT reset on skip. On REPL exit, pending review task is cancelled + bounded-drained (≤2s); no inline review fires at exit (hermes parity). Sessions shorter than `review_nudge_interval` (default 5) produce no review.
- **`run_session_review` refresh order** — fork child deps → `refresh_skills(child_deps)` → render manifest from child registry → build instructions → `build_agent`. Ensures successive turn-boundary passes within one session see prior passes' skill creations.
- **`CoSessionState`** — adds `iterations_since_review: int = 0` and `background_review_task: asyncio.Task | None`.
- **`SkillsSettings`** — adds `review_nudge_interval: int = Field(default=5, ge=1)` + `CO_SKILLS_REVIEW_NUDGE_INTERVAL` env override.
- **Protocol update** — `## Background review` section rewritten for turn-boundary cadence; dead curator/pin paragraph deleted.
- **`_lint.py` renamed to `lint.py`** — drops leading underscore (public surface cleanup); all import sites updated.
- **`run_dream_cycle` signature** — `miner_tool` moved from keyword-only to first positional argument; call sites updated.

## [0.8.198]

### Collapse skill discovery to manifest-only; remove SkillIndex, skill_search, URL-install, and curator
- **`skill_search` + `SkillIndex` retired** — FTS5-backed skill discovery removed; all skills (bundled + user-dir) now appear in the static `<available_skills>` manifest injected at prompt assembly. Zero DB construction cost per startup; no two-surface spec.
- **Manifest all-discoverable** — `render_skill_manifest()` walks both bundled and `~/.co-cli/skills/`; user-dir skill shadows bundled by same name; size guardrail warns (not blocks) when total count ≥ 30 after create.
- **Subagent skill discovery** — `run_session_review()` and `maybe_run_curator()` prepend the rendered manifest to their instructions; `skill_search` delegation removed from both.
- **URL-based install + upgrade removed** — `skill_manage install` (URL source), `skill-installer.md`, `/skills install`, `/skills upgrade`, and `SkillFetcher` deleted; skills are created by the agent or written by the user directly.
- **Usage sidecar + curator removed** — `.usage.json`, state machine (active/stale/archived), background `skill_curator` agent, and all associated plumbing deleted; skills are managed manually.
- **Test cleanup** — `test_flow_skill_search.py`, `test_flow_skill_index.py`, `test_flow_skill_installer_dispatch.py`, `test_flow_skill_curator.py`, `test_flow_skill_usage.py`, `test_flow_skills_pin.py`, `test_flow_skills_usage.py` deleted.

## [0.8.197]

### Test coverage: per-item/aggregate short-circuit ordering
- Add `test_per_item_error_short_circuits_aggregate_check` — verifies that per-item validation errors suppress the aggregate `in_progress` check, per the spec-stated ordering. Guards against pipeline restructuring regressions.

## [0.8.195]

### Enforce one-in-progress invariant in todo_write
- **`_check_one_in_progress` helper** — added to `co_cli/tools/todo/rw.py`; counts `in_progress` items in the final list and returns an error if count > 1.
- **Wired into both paths** — `_run_fresh` and `_run_merge` call the helper after per-item validation passes; aggregate failure is all-or-nothing (`session.session_todos` preserved unchanged).
- **Error message** — names all offending ids and instructs the model to resolve by setting all but one to `pending`, `completed`, or `cancelled`.
- **Docstring updated** — promoted from advisory to enforced: "only ONE item may be `in_progress` at a time — writes that produce more than one are rejected."
- **Tests** — 8 new cases in `tests/test_flow_todo.py` covering fresh (0/1/2 in_progress), merge (unrelated update, add second, atomic swap, legacy cleanup), and all-or-nothing preservation.

## [0.8.194]

### Persistence primitives + MemoryTransaction object redesign
- **New package `co_cli/persistence/`** — `atomic_write_text(path, content, *, encoding="utf-8", errors="strict")` and `atomic_write_bytes(path, content)` live in `co_cli/persistence/atomic.py`. Both build `mkdir(parents=True, exist_ok=True)` into the primitive; callers no longer pre-create parent dirs. `co_cli/memory/mutator.py` deleted; 8 importers migrated to `co_cli.persistence.atomic`.
- **Wrapper fold** — `_atomic_write_skill` deleted (5 internal callers in `tools/system/skills.py` now call `atomic_write_text` directly); `write_curator_state`, `write_records`, `write_skill_file` keep their signatures but drop the now-redundant `path.parent.mkdir(...)` line.
- **`tool_io.py` folded** — the local `tempfile.write_text + os.replace` block in tool-spill output is replaced by `atomic_write_text(file_path, content, errors="replace")`. Content-addressed dedup guard preserved.
- **`MemoryTransaction` object** — `MemoryStore.transaction()` now returns a `MemoryTransaction` context manager. `tx.index / tx.index_chunks / tx.remove` defer commits; `__exit__` commits on success or rolls back on exception. The hidden `_in_transaction` flag that silently switched `index() / index_chunks()` commit semantics is gone — those public methods always commit. The new private flag `_transaction_open` only refuses nested transactions.
- **`SkillIndex.upsert`** rewritten to `with self._store.transaction() as tx: tx.index(...); tx.index_chunks(...)`.
- **Convention docs** — `agent_docs/code-conventions.md` cites the new `co_cli.persistence.atomic.atomic_write_text` path and adds the rule "Multi-step writes to `MemoryStore` use `with store.transaction() as tx: ...`; hidden transaction state on the store is forbidden." `file_write` docstring carries an atomicity contract note pointing at the internal primitive.
- **Test coverage** — `tests/test_atomic_write_persistence.py` extended with mkdir-parent, `errors="replace"`, and `atomic_write_bytes` cases. `tests/test_flow_skill_index.py` extended with `test_nested_transaction_raises`, `test_transaction_method_outside_with_raises`, `test_transaction_remove_rolls_back_on_exception` — real sqlite, no mocks.

## [0.8.192]

### Proactive compaction focus inference
- **`_resolve_proactive_focus`** — private pure function in `co_cli/context/compaction.py`; derives a focus string from session state with no LLM call: in-progress todo content (head-capped at 200 chars) → most-recent user message tail (tail-capped at 200 chars) → `None`.
- **Wired into `proactive_window_processor`** — replaces the hardcoded `focus=None` at the `compact_messages` call site; the summarizer's existing `FOCUS TOPIC` block now preserves ~60-70% of the summary for on-task signal during auto-compaction.
- **Three unit tests** added to `tests/test_flow_compaction_proactive.py` covering all three resolution branches (in-progress todo, last user message, neither).

## [0.8.190]

### Atomic Write Hygiene — System-wide (Plan 3.5c-pre)
- **Canonical helper** — `co_cli/memory/_mutator.py` promoted to `co_cli/memory/mutator.py`; `atomic_write` renamed to `atomic_write_text(path, content)`; exception-cleanup bug fixed (temp file now unlinked on any failure, not just `os.replace` failure).
- **FTS5 upsert transaction** — `MemoryStore.transaction()` public context manager added; `SkillIndex.upsert` wraps both `index` + `index_chunks` writes in a single SQLite transaction — a mid-step failure no longer leaves a ghost row.
- **All non-atomic call sites migrated** — `skills/installer.py`, `tools/system/skills.py`, `skills/curator.py`, `skills/usage.py`, `memory/dream.py`, `agents/session_review.py`, `agents/skill_curator.py` all route through `atomic_write_text`; pid+uuid temp suffix dropped (tempfile already collision-safe).
- **Code convention rule added** — `agent_docs/code-conventions.md` documents that full-overwrite mutation must use `atomic_write_text`; local `tempfile.NamedTemporaryFile` blocks in mutation paths are forbidden.

## [0.8.188]

### Todo — Continuity (Plan todo-continuity)
- **`id` field on `TodoItem`** — every item now carries a model-assigned `id: str`; required, unique within session, no `.` or whitespace in the value.
- **`merge` mode on `todo_write`** — `merge=True` updates only the fields present on each payload item (matched by `id`); unknown ids are appended as new items; existing items not in the payload are preserved in order. Default `merge=False` replaces the full list.
- **All-or-nothing validation** — any validation error in either mode leaves `session.session_todos` unchanged.
- **`todos` in tool_output metadata** — `todo_write` success response carries `todos=list(session.session_todos)` for transcript-based rehydration.
- **Compaction snapshot format** — active todos now render as `- [{status}] {id}. {content}` so the model can reference items by id after compression.
- **`/resume` rehydrates `session_todos`** — scans loaded messages backwards; primary path reads `metadata['todos']` from the most recent `todo_write` `ToolReturnPart`; fallback path parses the most recent `TODO_SNAPSHOT_PREFIX` `UserPromptPart` (compacted sessions); defensive filter drops items without a non-empty `id`.

## [0.8.186]

### Skills — Self-evolution v1 (Plan 3.5b)
- **Session-end combined review** — when `skills.review_enabled=True`, `_drain_and_cleanup` forks a `session_reviewer` agent at REPL exit (`co_cli/agents/session_review.py`). The fork has both skill and knowledge toolsets, scans the just-finished transcript, and autonomously patches/creates skills + knowledge artifacts. Bounded by `REVIEW_MAX_ITERATIONS=8` + `REVIEW_TIMEOUT_SECONDS=120` outer cap. Reports `💾 <summary>` via `background_status_callback`. JSON + markdown per-run reports under `~/.co-cli/session-reviews/<timestamp>/`.
- **Skill curator** — when `skills.curator_enabled=True`, `_chat_loop` spawns `maybe_run_curator` as an `asyncio.create_task` at REPL startup. Pure state machine (`co_cli/skills/curator.py`): `active → stale` at `>CURATOR_STALE_AFTER_DAYS=30`, `stale → archived` at `>CURATOR_ARCHIVE_AFTER_DAYS=90`, `stale → active` on recent use; pinned skills opt out. After transitions, a `skill_curator` agent (skill-tools-only, `CURATOR_MAX_ITERATIONS=100` + `CURATOR_TIMEOUT_SECONDS=600`) consolidates prefix-clustered narrow skills into class-level umbrellas. Idle-gated (`CURATOR_MIN_IDLE_HOURS=2`) + interval-gated (default 7d). Archive moves files to `~/.co-cli/skills/.archive/` — never deletes. Optimistic-concurrency abort on cross-REPL collision.
- **Approval-bypass contract** — `auto_approve_skill_ops` / `auto_approve_knowledge_ops` flags on `CoRuntimeState` + `fork_deps_for_reviewer` / `fork_deps_for_curator` factories make the bypass scope explicit and testable. Actual bypass: `requires_approval=False` at delegation-agent tool registration (`agents/core.py:202`). Foreground tool calls unaffected.
- **Config** — `SkillsSettings` gains `review_enabled: bool = False`, `curator_enabled: bool = False`, `curator_interval_hours: int = 168`. Module-level constants for all iteration/timeout/day thresholds. Both features opt-in by default.
- **CLI** — `/skills curator status | run | pause | resume | restore <name>` and `/skills review run`. Status table surfaces `enabled / paused / last_run_at / run_count / next_eligible_at / idle_current / idle_required / pending_transitions / last_summary`. `run` enforces idle gate with explanatory error when blocked.
- **Tool surface tagging** — `skill_search` / `skill_view` / `skill_manage` carry `delegation=frozenset({"session_reviewer", "skill_curator"})`; `knowledge_search` / `knowledge_view` / `knowledge_manage` carry `delegation=frozenset({"session_reviewer"})`. `discover_delegation_tools` consumes these.
- **Background plumbing** — `CoRuntimeState.background_status_callback` (wired in `bootstrap/core.py` to `frontend.on_status`, never cleared by `reset_for_turn`); `CoSessionState.last_user_input_at` (updated per user input in `_chat_loop`) and `background_curator_task`. New shared standalone-agent runner `co_cli/agents/_runner.py:_run_agent_standalone` for background forks (no usage merge, no `ModelRetry`). `_run_agent_attempt` → `_run_agent_in_turn` rename (3 call sites). `_serialize_messages` → `serialize_messages` with new `include_tool_results: bool = True` keyword-only param.
- **Protocol acknowledgment** — `## Background review` section appended to `co_cli/context/rules/06_skill_protocol.md` so the foreground agent knows the review + curator exist and that `/skills pin` is the opt-out.

### Docs
- **Spec rename** — `docs/specs/memory-knowledge.md` → `docs/specs/knowledge.md`; `docs/specs/memory-sessions.md` → `docs/specs/sessions.md`. Cross-references updated in `bootstrap.md`, `compaction.md`, `core-loop.md`, `memory.md`, `system.md`, and `co_cli/memory/artifact.py` (which also had a stale `memory-session.md` singular typo — now `sessions.md`).

### Cleanup
- `docs/REPORT-test-hygiene-*.md` (10 files) removed — superseded by current `docs/REPORT-clean-tests-*.md` reports.
- Withdrawn `2026-05-03-113954-arxiv-research-ingestion.md` exec-plan deleted (per "withdrawn plans are deleted, not archived" convention).

## [0.8.184]

### Skills
- **`/clean-tests` skill trimmed**: 403 → 211 lines. Tracking log template dropped (was 90-line inline code block); per-violation fix catalog collapsed to a single fix principle + escalation block; Phase 2.5 folded into Phase 2; Phase 4 adversarial check merged into Phase 3; Phase 4.5/4.7 merged into new Phase 4 (coverage + registry); Phase 4.6 became Phase 5; Rules section dropped. Phase cross-references renumbered throughout.

## [0.8.182]

### Skills
- **Usage tracking sidecar** (`~/.co-cli/skills/.usage.json`) — per-skill counters (`use_count` / `view_count` / `patch_count`) and timestamps (`created_at`, `last_used_at`/`last_viewed_at`/`last_patched_at`), plus `state` and `pinned` flags. Hooks fire on `skill_view`, `skill_manage(action='create'/'edit'/'patch'/'delete'/'install')` success paths in `co_cli/tools/system/skills.py`. Best-effort writes — exceptions are `logger.debug`-logged and swallowed. Atomic via sibling-temp + `os.replace`.
- **Agent-created filter** — sidecar writes apply only to skills under `user_skills_dir` AND without `source-url`. Bundled skills (under `co_cli/skills/`) and URL-installed skills are upstream-managed and excluded.
- **CLI** — `/skills usage [<name>]` prints the per-skill table or a single record; `/skills pin <name>` / `/skills unpin <name>` toggle the `pinned` flag (rejects bundled and URL-installed with explanatory error).
- **Config** — `SkillsSettings` (new `co_cli/config/skills.py`) wired into `Settings.skills`. One knob: `usage_tracking_enabled` (env `CO_SKILLS_USAGE_TRACKING_ENABLED`, default `True`). Disabling short-circuits every hook.
- **Spec** — `docs/specs/skill.md` §2 gains the Usage Tracking Sidecar section; §3 management table gains the three new `/skills` subcommands; §4 Config and §5 Files updated.

### Forward-compat
- `bump_use` and `last_used_at` are reserved API surface for the 3.5b curator state machine (no production caller in 3.5a; "view IS use" in today's flat-file model).

## [0.8.180]

### Refactor
- **`deps.py` cleanup**: `GoogleSessionState` sub-struct groups `google_creds`, `google_creds_resolved`, `drive_page_tokens` off `CoSessionState`; `fork_deps` inheritance made explicit. `TodoItem` TypedDict replaces `list[dict]` for `session_todos`. `MappingProxyType` enforces "read-only after bootstrap" contract on `degradations`. `resource_locks` factory replaces `__post_init__` + `# type: ignore`. `reset_for_turn` docstring corrected (6 per-turn fields, was 5); CI test added as contract enforcement.
- **Test fixture fix**: phantom `"sqlite-fts → grep"` degradation string removed — that path raises `RuntimeError` in current bootstrap; replaced with the real `"hybrid → fts5"` degradation.

## [0.8.178]

### Skills
- **orchestrate-dev Step 4**: trim self-review to lint-fix only (`scripts/quality-gate.sh lint --fix`) — convention checklist moves to review-impl.
- **review-impl Phase 2C**: add naming, visibility (`_prefix`), API shape, modular structure, and anti-pattern checks to the convention checklist. Same 5 items added to Phase 6 final re-scan. Phase 6 (doc sync) removed — doc sync is owned by orchestrate-dev. Phases renumbered (7→6, 8→7, 9→8).
- **deliver**: skill deleted — atomic/single-file changes use Claude Code's built-in plan flow directly.
- **test-hygiene → clean-tests**: skill renamed to an action verb. All internal references, CLAUDE.md, and `agent_docs/system-workflows-to-test.md` updated.

## [0.8.164]

### Feature
- **Compaction summarizer — structural fix for `## Active Task` capture.** `summarize_messages` no longer passes the dropped history as `message_history`; instead serialises it inline under a `TURNS TO SUMMARIZE:` block in the user prompt (hermes/opencode-aligned). Eliminates the "most recent user request" ambiguity that caused the model to capture the summariser prompt itself in `## Active Task` instead of the user's last conversation message. New helper `_serialize_messages` renders `UserPromptPart` / `TextPart` / `ToolCallPart` / `ToolReturnPart` into role-labelled lines, joined by blank lines per message.
- **Per-message redaction at serialisation time.** `redact_text` (new public function in `co_cli/config/observability.py`) is applied to each message's content and tool args before they reach the summariser LLM. Removes the previous post-summary redaction (redundant with same patterns); single source of truth at serialisation.
- **Summariser prompt hardening.** Strengthened the global SKIP RULE so empty sections are omitted entirely rather than filled with `None.` / `[None]` filler. Tightened `## Completed Actions` format spec to make `[tool: name]` mandatory and forbid invented tool names or hallucinated edits. Replaced `## Additional Context` heading with `=== ADDITIONAL CONTEXT ===` to avoid collision with the LLM's output section markers.
- **Spill telemetry gap fills.** Three new span attributes for calibration: `co.tool.args_chars` (set by `lifecycle.before_tool_execute`), `co.user_prompt.chars` (set on `co.turn` span in `run_turn`), `co.tool.spill_refetch_attempt` (set by `file_read` when the path is under `tool_results_dir`).
- **Calibration script — `scripts/calibrate_spill_size.py`.** Produces a markdown report with per-tool size distribution (p50/p90/p95/p99), L2 aggregate trigger statistics, gap-fill signal distributions, and on-disk artifact analysis. Defaults to production-only (`service.name = "co-cli"`); `--include-pytest` opt-in for diagnostic runs.

### Docs
- **`docs/specs/compaction.md` §2.2** — "Why 4,000?" budget-arithmetic derivation: working-budget table, spill-trigger formula, sensitivity at 1K/16K, scaling table for 200K and 1M context windows, rationale for `file_read` exemption.

### Test
- Removed 8 structural tests in compaction suite: 3 OTel span-attribute tests in `enforce_request_size` (replaced 2 with pure behavioural assertions), 5 string-literal marker/prompt tests in `summarization`.
- Rewrote `test_summarize_messages_from_scratch_returns_structured_text` against a realistic multi-turn fixture with `file_read` / `file_edit` / `shell` tool calls. New assertions: required section presence, verbatim active-task fidelity, tool-name fidelity (no hallucination), no `None.` / `[None]` filler in skippable sections, core topic captured.
- Added `test_redact_text_removes_credential` and `test_redact_text_clean_text_unchanged` in observability redaction.

## [0.8.158]

### Refactor
- **Compaction API surface — collapse multi-path to single primitive.** Removed `compact_under_budget` and `compact_to_bounds` from the public surface; added `compact_messages(ctx, messages, bounds, *, focus)` (shared assembly primitive — slices, runs gated summarizer, builds marker, returns `(result, summary_text)` without writing runtime) and `commit_compaction(ctx, result)` (sole writer of the three "applied" runtime fields). Proactive-only policy (savings, status callback, OTEL execution attributes, thrash counter, commit) bundled into private helper `_record_proactive_outcome`. `_gated_summarize_or_none` drops its `announce` parameter — opening status callback always fires when the gate is open. Three callers (`proactive_window_processor`, `recover_overflow_history` PATH 1+2, `/compact`) all use `compact_messages` + `commit_compaction` with their own policy layered on top. Eliminates leaky `tokens_before` parameter, triplicated runtime-commit code, and asymmetric public API.

### Docs
- **`docs/specs/compaction.md`** — §1.1 trace, §1.2 layered budget, §1.3 mermaid diagram, §1.5 runtime flag map + sole-callback paragraph, §2.5 STEPs framing + Task-3 invariant + STEP 6, §2.6 callers table + callstack diagram + commit table, §2.7 PATH 1/PATH 2/thrash-reset, §4 files table — all synced to new API.
- **`docs/specs/memory.md`** §2.1, **`docs/specs/core-loop.md`** §3 — cross-spec references updated.

## [0.8.154]

### Feature
- **MCP schema sanitizer.** New `co_cli/tools/mcp_schema.py` — pure `sanitize_mcp_schema()` normalizes malformed MCP tool `inputSchema` dicts before they reach Ollama/Gemini backends. Handles six repair classes: bare-string type, type arrays, anyOf/oneOf nullable collapse, missing properties, missing type inference, and invalid required pruning. Recursive, idempotent, deep-copy (never mutates input).
- **`_SanitizingMCPServer` proxy.** Every MCPServer built in `_build_mcp_toolsets()` is now wrapped in `_SanitizingMCPServer`, which sanitizes `inputSchema` on `list_tools()`. Cached-mutation pattern ensures the model-call-time schema path is also covered. Proxy correctly delegates `__aenter__`/`__aexit__` for context manager lifecycle.

### Config
- **Default model corrected** to `qwen3.5:35b-a3b-q4_k_m-agentic` (active Ollama modelfile). `DEFAULT_MAX_CTX` and per-call `num_ctx` raised from 32 768 → 65 536 to match modelfile `num_ctx 65536`.

## [0.8.152]

### Refactor
- **Enricher simplification — `gather_compaction_context`.** Dropped `_gather_file_paths` and `_gather_prior_summaries` (recoverable LLM-side); removed four cap constants (`_FILE_PATHS_MAX_CHARS`, `_PRIOR_SUMMARIES_MAX_CHARS`, `_CONTEXT_MAX_CHARS`, `_cap()` helper); simplified function signature (dropped unused `dropped` parameter); extracted `_format_active_todos` shared formatter to eliminate bullet-format drift between `_gather_session_todos` and `build_todo_snapshot`. Single remaining source (session todos) has clear session-orthogonal value. ~50 lines removed.

### Docs
- **`docs/specs/compaction.md` §2.6.3** — Enrichment table reduced to one source row (session todos); cap table reduced to one entry; rationale updated.

## [0.8.150]

### Test
- **Test surface hygiene — file consolidation.** Five merges, one split/rename, two test deletions, and 3x near-identical delegation tests parametrized into one. Files reduced 40 → 34 (−15%); tests 204 → 202 (−2). Suite green at 202 passed in 155.85s. Specifics:
  - `test_flow_llm_settings.py` → folded into `test_flow_llm_call.py` (single reasoning-settings test alongside 3 noreason tests, same `llm_call` surface).
  - `test_flow_memory_lifecycle.py` → folded into `test_flow_memory_write.py` (`mutate_artifact` replace test joins the existing `mutate_artifact` group).
  - `test_flow_memory_search.py` → folded into `test_flow_memory_store_nochunk.py`; the combined file renamed to `test_flow_memory_store.py` (covers chunked FTS5 + `no_chunk=True` + `get_chunk_content` end-to-end, all `MemoryStore` direct).
  - `test_flow_mcp_spill.py` + `test_flow_spill_threshold.py` → unified `test_flow_spill.py` covering both the `spill_if_oversized` helper and the `CoToolLifecycle.after_tool_execute` MCP path in one place.
  - `test_flow_compact_command.py` → folded into `test_flow_slash_commands.py` (slash-command tests grouped: `/clear` + `/compact`).
  - `test_flow_bootstrap_session.py` was a 4-concern grab-bag; split: `test_restore_session_picks_most_recent` → `test_flow_session_persistence.py` (its actual home), remainder renamed to `test_flow_config_loading.py` (load_config dotenv/env, security checks, skill loading).
  - `test_flow_agent_delegation.py`: deleted redundant `test_reason_raises_model_retry_beyond_max_depth` (subsumed by `_at_max_depth`); folded `test_fork_deps_depth_propagates_through_chain` into a combined `test_fork_deps_increments_agent_depth` (single-level test alone passes the bug class where production sets `depth=1` constant rather than incrementing); 3x near-identical depth tests for `reason`/`knowledge_analyze`/`web_research` parametrized into one (3 collected instances, same coverage).
  - Bundled coworker test-hygiene edit: `test_flow_compaction_proactive.py` deletion of `test_post_compaction_failure_leaves_runtime_clean` (used `monkeypatch`, forbidden by `agent_docs/testing.md`).

### Docs
- **`docs/specs/compaction.md`** — replaced 4 references to deleted `test_flow_spill_threshold.py` with `test_flow_spill.py`; removed stale row pointing to long-deleted `test_flow_spill_otel.py`; added new MCP-lifecycle test row.
- **`docs/specs/memory.md`** — updated test-gate refs after the memory_search → memory_store, memory_lifecycle → memory_write, and bootstrap_session → session_persistence moves.

## [0.8.149]

### Fixed
- `/compact` now clears `previous_compaction_summary` when the summarizer falls back to a static marker, preventing the next proactive compaction from prepending a stale iterative summary that references history that no longer exists.

## [0.8.148]

### Refactor
- **Circuit breaker `_summarization_gate_open` rewritten block-first.** Three branches now explicit: `count < TRIP` → open, `skips_since_trip % PROBE_EVERY == 0` → probe, else → block. Same cadence; no implicit else.
- **`CoRuntimeState.current_request_tokens_after_spill` renamed to `current_request_tokens_estimate`.** Written on all `enforce_request_size` exit paths, not just spill paths. OTEL span attribute updated to `compaction.request_tokens_estimate`.
- **`spill_with_span` helper unifies native and MCP spill paths.** Extracted from `tool_output` into `co_cli/tools/tool_io.py`; `CoToolLifecycle.after_tool_execute` now calls it instead of bare `spill_if_oversized`. Both paths emit `tool_budget.spill_tool_result` spans.

## [0.8.147]

### Refactor
- **`_summarization_gate_open` is now read-only.** Return type changed from `bool` to `tuple[bool, bool]` (`gate_open`, `is_probe`). The `compaction_skip_count += 1` increment on the circuit-breaker block path moved to `_gated_summarize_or_none`, which already owns all other write paths (failure increment, success reset). Probe log emission also moved to the caller. All five cadence tests updated to unpack the tuple.

## [0.8.146]

### Feature
- **Background tasks: file-based output.** `BackgroundTaskState.output_lines` deque (`maxlen=500`) replaced with a per-task log file at `LOGS_DIR / f"bg-{task_id}.log"`. `_monitor` writes through a line-buffered handle inside a `with` block so the file closes on EOF, cancellation, or exception. Reads (`task_status`, `/tasks`) tail the file via the new `tail_log(path, n)` helper (64 KB seek-from-end window). `spawn_task` accepts an injectable `logs_dir` for test isolation; default remains `LOGS_DIR`. `_drain_and_cleanup` unlinks log files at session shutdown. Per-task history retained for the full session — no longer locked to the most-recent 500 lines. Closes the §3.6 gap in `docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md`.
- **`spawn_error` field on `BackgroundTaskState`** carries the spawn-stage failure message (cwd missing, mkdir denied, etc.) separate from the log file. Callers prefer `spawn_error` when set; otherwise tail the log.

### Fix
- **`spawn_task` mkdir-before-spawn.** Move `logs_dir.mkdir(...)` inside the try block so it runs BEFORE `create_subprocess_shell`. An mkdir failure (permission denied, disk full, race) now surfaces as `spawn_error` without leaving a running subprocess that has no `log_path` set and no `_monitor` task to drain its stdout. Closes a process-leak window introduced by the file-based-output refactor.

### Test
- **New `tests/test_flow_background_tasks.py`** (5 tests): full-output capture to log file; oversized-run (5000 lines) tail slicing; empty/missing/zero-n branches of `tail_log`; kill-while-running file-handle release; spawn-failure sets `spawn_error` with no log file. Replaced a fixed `await asyncio.sleep(0.3)` race with a poll-with-timeout (`async with asyncio.timeout(5): while not exists+nonzero: sleep(0.05)`).

### Docs
- **`docs/specs/compaction.md`** — restructure §1 around the end-to-end trace (§1.1), layered budget stack (§1.2), pipeline + message-shape diagrams (§1.3, §1.4), and a new runtime flag and callback map (§1.5). §2.5 trimmed to proactive trigger only; new §2.6 "Summarizer pipeline" merges the prior enrichment-helper and summarizer sections plus the marker / breadcrumb / circuit-breaker subsections pulled out of §2.5. ASCII feedback-loop diagram replaces the Mermaid one in §2.6.4. §2.6.3 enrichment table corrected to include the 20-path / 10-todo caps and the active-only todos filter.
- **`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md`** — §3.6 marked Done with code-verified reference to the new file-based output path. §3.5 (MCP dynamic refresh) updated with deferred rationale.

## [0.8.144]

### Refactor
- **L2 consolidation: per-batch hook → per-request history processor.** Replace the post-tool-exec `_enforce_request_budget` (capability hook on `CoToolLifecycle.after_node_run`) with a new `enforce_request_size` history processor that runs at every `ModelRequestNode` entry on the full message list. The old hook fired on `CallToolsNode` exit with a fixed `tail_fraction × budget` cap on the just-produced batch — over-fired when history was small (a 10K batch tripped the gate even when total context was well under budget) and under-fired across multiple batches in the same turn (3 × 5K each accumulated silently). The new processor sees the assembled request and force-spills the largest unspilled `ToolReturnPart`s largest-first until total tokens fit. Slots into the chain at `dedup → evict → enforce_request_size → proactive → sanitize` so cheap reductions happen first; `proactive_window_processor` fast-paths when spill brought aggregate under `compaction_ratio × budget`, sparing the LLM call.
- **New config knob: `compaction.spill_ratio`** (env `CO_COMPACTION_SPILL_RATIO`, default `0.50`). Validated `<= compaction_ratio` so post-spill aggregate falls below proactive's trigger and proactive fast-paths.
- **`CoDeps.spill_threshold_tokens`** replaces `request_aggregate_threshold_tokens`; `CoRuntimeState.current_request_tokens_estimate` replaces `current_request_aggregate_tokens_after_spill`. Computed once at bootstrap as `int(spill_ratio × model_max_ctx)`.
- **OTEL span rename:** `tool_budget.enforce_request_aggregate` → `tool_budget.enforce_request_size`; attributes `request_aggregate.*` → `request.*`. Bootstrap span attributes `budget.tail_fraction` / `budget.request_aggregate_threshold_tokens` → `budget.spill_ratio` / `budget.spill_threshold_tokens`.
- **Drop dead helper module** `co_cli/tools/_request_budget.py`. The L0 `enforce_tool_call_limit` span still fires from `after_node_run`; the L2 hook block is gone.
- **Direct imports for history processors.** `agent/core.py`, `tests/test_flow_history_processors.py`, and `context/assembly.py` now import `dedup_tool_results` / `evict_old_tool_results` / `COMPACTABLE_KEEP_RECENT` from `co_cli.context.history_processors` (origin) instead of through `co_cli.context.compaction` (re-export). Dead re-exports removed from `compaction.py`'s `__all__`.

### Test
- **New flow file** `tests/test_flow_enforce_request_size.py` (renamed/rewritten from `test_flow_request_budget.py`): 8 tests covering fast-path, force-spill ordering, cross-batch accumulation, cached-threshold use, all-spilled bail-out, text-only history, already-spilled exclusion, and OTEL span emission.
- **New integration file** `tests/test_flow_compaction_processor_chain.py`: verifies the chain ordering contract — when spill resolves pressure, `proactive_window_processor` fast-paths (no compaction marker); when spill has no candidates, proactive fires (static-marker fallback with `model=None`).
- **New file** `tests/test_flow_bootstrap_ollama_num_ctx.py`: extracted the two `_check_ollama_num_ctx_floor` tests out of `test_flow_bootstrap_budget_span.py` (wrong file).
- **Test surface cleanup.** Delete redundant `test_flow_spill_otel.py` (3 tests; folded the disk-write assertion into `test_spill_large_content`). Fold L0 OTEL tests into `test_flow_tool_call_limit.py` and delete `test_flow_tool_call_limit_otel.py`. Drop two `test_constants_pinned` structural tests. Merge three stub-format tests in `test_flow_spill_threshold.py` into one `test_stub_shape`. Trim circuit-breaker parametrize sweeps in `test_flow_compaction_proactive.py` (`range(3,13)` → `[3, 12]`, `range(14,23)` → `[14, 22]`) — boundary values carry the contract; intermediates were redundant. Net: −20 tests, suite drops 201 → 184, all pass.

### Docs
- **`docs/specs/compaction.md`** — §1 mechanism table row L2 rewritten + diagram updated to show `enforce_request_size` in the MRN chain (5 processors); §2.1 four-layer table L2 row rewritten; §2.4 entire section rewritten for the new history processor (skip cases, algorithm, span name, side effect, worked example for cross-batch accumulation); §2.6 enrichment helper reference renamed; §3 config table adds `compaction.spill_ratio` row; §4 Files table — drop `_request_budget.py`, add `enforce_request_size` to history-processors entry, update `lifecycle.py` description; §5 Test Gates — both rows renamed.
- **`docs/specs/core-loop.md`, `prompt-assembly.md`, `observability.md`** — history-processors tables add `enforce_request_size` row; "L2 aggregate request-budget" paragraphs removed (no longer separate from history processors); observability span attribute lists updated.

## [0.8.139]

### Fix
- **Length-continuation retry gate** — narrow `_length_retry_settings` to require a `TextPart` in the response (was `TextPart` OR `ToolCallPart`). A truncated `ToolCallPart` carries malformed JSON args; passing that history back produces an assistant message with an unanswered `tool_calls` entry that the OpenAI/Ollama protocol rejects. Tool-call truncations now fall through to `_check_output_limits` for the ceiling-hit status (`/compact` hint) instead of retrying with poisoned history.
- **Length-retry termination invariant** — module-load `assert _LENGTH_RETRY_BOOST > 1` documents the strictly-increasing-max_tokens contract that guarantees the retry loop terminates at the ceiling. Self-documenting, zero state, fails fast at import if the constant ever drifts to 1.

### Test
- **Gate-call coverage** — `tests/test_flow_length_retry.py` rewritten to test `_length_retry_settings` directly with synthetic `ModelResponse` inputs covering every gate branch: thinking-only, empty parts, tool-call-only, tool-call-after-thinking, text-after-thinking pass + boost, ceiling cap, ceiling block, non-`length` finish reason. Replaces 4 tautological predicate tests that re-implemented the gate inline.

## [0.8.138]

### Refactor
- **Memory tool surface contraction** — drop `memory_list` from the registered tool surface; recall is search-driven via `memory_search` (empty/kind-filtered query browses the index) and full-body reads route through generic `file_read`. Three active tools: `memory_search`, `memory_create`, `memory_modify`. CLAUDE.md updated; the unregistered-but-source-present `memory_read_session_turn` reader is documented in the rationale.
- **Knowledge chunk param naming** — `chunk_size` / `chunk_overlap` → `chunk_tokens` / `chunk_overlap_tokens` across `co_cli/memory/` and consumers (`google/drive.py`, `tools/memory/write.py`, dream consolidation, `MemoryStore`). Internal dream-window splitter constants disambiguated as `_DREAM_WINDOW_CHUNK_CHARS` / `_DREAM_WINDOW_CHUNK_OVERLAP_CHARS`. Tests updated for the new param names.
- **File-tool helper visibility** — drop leading underscores from cross-package helpers in `co_cli/tools/files/fs_guards.py` (`enforce_workspace_boundary`, `safe_mtime`, `detect_encoding`, `is_recursive_pattern`) per the project's `_prefix.py` convention; update call sites in `read.py`, `write.py`, and `tools/shell/execute.py`. `co_cli/tools/files/read.py` constant rename `_READ_DEFAULT_LIMIT` → `_READ_DEFAULT_LIMIT_LINES` for clarity.
- **`bootstrap/core.py` straggler imports** — `_tool_call_limit` → `tool_call_limit` import sites that the previous rename pass missed.

### Fix
- **`docs/specs/config.md`** — drop the stale `qwen3.6` entry; reflect the active model `qwen3.5:35b-a3b`. Rewrites the `max_ctx` section as a contract pivot: probed Modelfile `num_ctx` is the floor (must be `>= max_ctx`); static `_LLM_SETTINGS["...num_ctx"]` is the ceiling (must be `<= max_ctx`); the two checks share `max_ctx` as the reference and never compare against each other. `_check_ollama_num_ctx_floor` docstring expanded with the same framing.
- **`co_cli/config/llm.py`** — remove the `qwen3.6` entry from `_LLM_SETTINGS` (model no longer in use).
- **`tests/test_flow_tool_calling_functional.py`** — comment refresh: qwen3.6 → qwen3.5 with the same DashScope/OpenCode reasoning-mode rationale.
- **Research docs** — `RESEARCH-tools-gaps-co-vs-hermes.md` major rewrite (269-line update); `RESEARCH-tools-peers-tiers.md` minor sync.

## [0.8.136]

### Refactor
- **REPL completer migration** — replace the flat `WordCompleter` with a structured `SlashCommandCompleter` (`co_cli/commands/completer.py`) that pairs each `/cmd` with its description as `display_meta` in the popup. Adds a custom `_COMPLETION_STYLE` for the dropdown. `build_completer_words` → `build_completer_entries` returns `(name, description)` tuples; `_refresh_completer` → `refresh_completer` (now public, called by skill mutations).
- **Table styling standardization** — new `make_table(*columns)` helper in `co_cli/display/core.py` (borderless, no header, no padding) replaces inline `rich.Table` constructors across all command modules: `help.py`, `sessions.py`, `skills.py`, `tasks.py`, `history.py`, `knowledge.py`, `approvals.py`, `background.py`. Removes the trailing tip line from `/help`.

## [0.8.135]

### Fix
- **`docs/specs/compaction.md`** — add a `Scope` column to the functional architecture table (per-tool-result / per-turn / multi-turn / housekeeping) plus a one-paragraph scope-levels intro above the table. Docs-only formatting cleanup; clarifies how each compaction mechanism fits in the budget hierarchy.

## [0.8.134]

### Feature
- **Tool-call dedup hook** (`CoToolLifecycle.before_node_run`): drops later `ToolCallPart`s whose `(tool_name, args)` matches an earlier one in the same `ModelResponse`, before approval prompts and before parallel tool dispatch. Prevents duplicate execution, double approval prompts, and wasted tokens when smaller Qwen / GLM variants emit the same tool call twice. Closes gap 2.2 from RESEARCH-hermes-ollama-stability-gaps. Emits `tool_budget.dedup_tool_calls` span (`dedup.parts_before`, `parts_after`, `dropped`) only when duplicates are found.
- **Helpers** in `co_cli/tools/lifecycle.py`: `_args_dedup_key` (stable key for `str | dict | None` args; raw and parsed forms both supported) and `_dedup_tool_call_parts` (preserves order, returns `None` when no duplicates so callers can skip the rebuild).

### Fix
- **`evict_old_tool_results` index scope** (`co_cli/context/history_processors.py`): `_build_call_id_to_args` now scans `messages[:boundary]` instead of the full message list. `_rewrite_tool_returns` only ever rewrites parts in `messages[:boundary]`, and a `ToolReturnPart`'s paired `ToolCallPart` always precedes it, so the narrower scope still finds every needed call_id. Eliminates a per-turn full-history scan that grew with conversation length.

### Tests
- `tests/test_flow_tool_call_dedup.py` — 6 behavioral tests: identical dict args dedup, same-tool different-args preserved, different-tool same-args preserved, mixed text/tool ordering, non-`CallToolsNode` passthrough, byte-identical raw-string args dedup.

## [0.8.132]

### Feature
- **Surrogate sanitizer** (`sanitize_surrogate_codepoints` history processor): replaces lone Unicode surrogate code points (U+D800–U+DFFF) with U+FFFD before the message list reaches the SDK; closes gap 1.3 from RESEARCH-hermes-ollama-stability-gaps. Prevents `UnicodeEncodeError` crashes from byte-token reasoning models (Qwen3 quantizations, GLM-5, Kimi K2.5). Registered last in the history processor chain in `agent/core.py`.

### Fix
- Test import fixes for module renames: `_tool_call_limit` → `tool_call_limit`, `_history_processors` → `history_processors`; `KnowledgeSettings.chunk_size` → `chunk_tokens` across tests; `KNOWLEDGE_ENV_MAP` key `chunk_overlap_tokens` corrected to `chunk_overlap`

## [0.8.126]

### Feature
- **L0 tool-call cap**: `MAX_TOOL_CALLS_PER_MODEL_TURN = 6` brake in `CoToolLifecycle.wrap_tool_execute`; per-model-turn counter with `ctx.run_step` transition reset; returns `MaxToolCallsExceededPayload` JSON on breach
- **L2 aggregate turn-budget spill** (`enforce_turn_budget` history processor): after `evict_old_tool_results`, force-spills the largest current-batch `ToolReturnPart`s (largest-first) until the aggregate fits within `deps.turn_aggregate_threshold_tokens`; threshold bootstrapped as `int(tail_fraction * model_max_ctx)` and cached on `CoDeps`
- **L1 per-call spill refit**: `SPILL_THRESHOLD_CHARS = 4_000` and `TOOL_RESULT_PREVIEW_CHARS = 1_500` module constants replace config-driven threshold; `spill_if_oversized` replaces `persist_if_oversized` (adds `force=` param for L2 path); `ToolInfo.spill_threshold_chars` replaces `max_result_size`; `ToolsSettings` module deleted
- **OTEL coverage** (`co-cli.tool_budget` tracer): `tool_budget.resolved` at bootstrap, `tool_budget.spill_tool_result` per M1 check, `tool_budget.enforce_turn_aggregate` per M2L run, `tool_budget.turn_tool_calls` per model turn
- **Shared token constant**: `CHARS_PER_TOKEN = 4` in `co_cli/context/tokens.py`; replaces inline `// 4` in `estimate_message_tokens` and L2 aggregate estimate

### Refactor
- `resolve_compaction_budget` signature: `(config, ctx_window)` → `(deps: CoDeps)` — returns `deps.model_max_ctx` directly (always set at bootstrap)
- Bootstrap: `_probe_model_ctx` extracted from `create_deps` to fix C901 complexity; `turn_aggregate_threshold_tokens` computed and cached on `CoDeps` at startup
- `co_cli/context/compaction.py`: extended `compaction.proactive_check` span with `compaction.tool_call_limit` and `compaction.turn_aggregate_tokens_after_spill` attributes
- `co_cli/context/_history_processors.py`: `evict_batch_tool_outputs` replaced by `enforce_turn_budget` (L2 aggregate processor)

### Tests
- `tests/test_flow_spill_threshold.py` — 10 tests: constant values, threshold boundary, stub content, force= behavior
- `tests/test_flow_turn_budget.py` — 4 tests: below-threshold no-spill, largest-first ordering, all-spilled bail-out, cached threshold
- `tests/test_flow_tool_call_limit.py` — 6 tests: constant pin, allow up to cap, reject above cap with JSON payload, run_step reset, concurrent dispatch, guidance interpolation
- `tests/test_flow_spill_otel.py` — 3 tests: below-threshold pass-through, above-threshold spill, tracer name

## [0.8.124]

### Refactor
- Removed unused `InferenceSettings` class and `LlmSettings.reasoning` / `.noreason` fields — no shipped config used the user-override layer; `_inference()` collapses to a one-line lookup
- Renamed `_INFERENCE_MODEL_SETTINGS` → `_LLM_SETTINGS` to fit the file's `LLM_*` prefix family

### Fix
- `settings.reference.json` rewritten to validate against the current `Settings` schema (was failing with 11 validation errors): dropped dead `llm.ctx_warn_threshold` / `ctx_overflow_threshold` / `reasoning` / `noreason`, `knowledge.llm_reranker`, `memory.injection_max_chars` / `extract_every_n_turns`, `tools.batch_spill_chars`, top-level `subagent` block and `library_path`; added missing `compaction` block plus knowledge lifecycle fields; replaced `provider: "ollama-openai"` with `"ollama"`; populated `mcp_servers` with the shipped `context7` default

### Docs
- Synced `docs/specs/config.md` and `docs/specs/bootstrap.md` to the renamed symbol and removed override fields

## [0.8.122]

### Refactor
- Renamed `_INFERENCE_DEFAULTS` → `_INFERENCE_MODEL_SETTINGS` — the table is canonical per-model knobs, not "defaults" of anything; bootstrap defaults are kept separately at the top of `llm.py`
- Added `DEFAULT_LLM_MODELS: dict[str, str]` for per-provider default model id (full id with variant tag); replaces the single hardcoded `DEFAULT_LLM_MODEL` constant
- Pydantic `model_validator` on `LlmSettings` auto-resolves empty `llm.model` to `DEFAULT_LLM_MODELS[provider]`; "no model configured" is no longer a reachable bootstrap failure mode
- Deduplicated scalar + extra_body extraction across `reasoning_model_settings()` / `noreason_model_settings()` via `_ollama_settings()` and `_gemini_settings()` translators
- `reasoning_model_settings()` is now provider-aware (closes a latent gap where Gemini-specific keys were silently ignored)
- Stale path comment in `bootstrap/check.py` (`config/_llm.py` → `config/llm.py`)

### Tests
- Added `test_flow_llm_settings.py` exercising `reasoning_model_settings()` end-to-end against real Ollama; closes the reasoning-path coverage gap (existing `test_flow_llm_call.py` only covers noreason)
- Added `LLM_REASONING_TIMEOUT_SECS = 30` constant for reasoning-mode tests

### Docs
- Synced `docs/specs/config.md` to the renamed table and new `DEFAULT_LLM_MODELS`
- Updated `docs/specs/bootstrap.md` failure-mode table — removed the now-unreachable "No model configured" entry; added unknown-model and noreason-only-model failure modes

## [0.8.119]

### Refactor
- Removed `context_window` from `_INFERENCE_DEFAULTS` — static fallbacks replaced by runtime probe
- Added `max_ctx` to `LlmSettings` as a safety ceiling on the Ollama probe result
- `effective_num_ctx()` now returns 0 when probe has not run (unknown) instead of a stale static default; caps probe result at `max_ctx`
- Removed `LlmModel.context_window` and `reasoning_context_window()` — compaction budget now sourced exclusively from `effective_num_ctx()`
- Simplified `resolve_compaction_budget` signature: no `context_window` param; uses `effective_num_ctx()` directly

## [0.8.117]

### Refactor
- Trimmed `_INFERENCE_DEFAULTS` for ollama qwen3.x: reasoning down to `max_tokens` + `context_window`; noreason down to `think=false` + `reasoning_effort=none` — all other params deferred to the served model
- `reasoning_model_settings()` and `noreason_model_settings()` now build `ModelSettings` conditionally, omitting absent keys rather than hard-coding them

## [0.8.115]

### Fixes
- Corrected ship skill version bump rule: bump to nearest even (feature) or odd (bugfix) patch number, not a fixed +1/+2 increment

## [0.8.114]

### Refactor
- Unified canon into the artifacts channel: `_search_canon_channel()` deleted; canon flows through `_search_artifacts()` as `kind='canon'` (source='canon' in MemoryStore)
- `ArtifactKindEnum.CANON` added; `sync_dir()` auto-sets `kind='canon'` when `source='canon'`
- Three-pass FTS5 structure: canon priority → user priority → waterfall (rule/article/note, dual-capped by count and chars)
- Four module constants: `_ARTIFACTS_CANON_CAP=3`, `_ARTIFACTS_USER_CAP=3`, `_ARTIFACTS_WATERFALL_CHUNK_CAP=5`, `_ARTIFACTS_WATERFALL_SIZE_CAP=2000`
- `character_recall_limit` config field deprecated (kept for one version; not consumed by recall)

## [0.8.113]

### Fixes
- Lowered `compaction_ratio` default from 0.65 → 0.50: trigger now fires at ~16k tokens (32k ctx) instead of ~21k, giving the LLM ~5k more headroom before context pressure degrades output quality
- Headroom per pass: ~24% (was ~36%); tail budget unchanged at 20% × budget; shape invariant `tail_fraction < compaction_ratio` still satisfied (0.20 < 0.50)
- Removed redundant `compaction_ratio = 0.5` eval override in `eval_compaction_multi_cycle.py` (now matches production default)

## [0.8.111]

### Fixes
- Removed dead `evict_batch_tool_outputs` history processor (200k threshold never fired; redundant with at-write spill in `tool_output()`)
- Removed `batch_spill_chars` config field and `last_overbudget_batch_signature` runtime state
- Removed `asyncio.timeout(90)` from `_PerCallTimeoutCapability` — per-call timeout fired mid-stream causing `httpx.ReadError` crash; outer 360s segment hang timeout is the correct guard
- Added `httpx.ReadError` to `run_turn` error handlers (pydantic-ai streaming path does not wrap this as `ModelAPIError`)
- Added per-LLM-call timing to `_PerCallTimeoutCapability` — DEBUG log every call, WARNING when ≥81s
- Fixed `AgentRunResult.data` → `.output` in eval judge (pydantic-ai API rename)
- `eval_compaction_multi_cycle`: replaced broken LLM judge gate with deterministic keyword chain check; added `outcome="error"` turn detection; set `compaction_ratio=0.5` to trigger phase-2 earlier on local models; added summary content previews

### Refactor
- Centralize eval model construction — no local `build_model()` calls in eval files

## [0.8.107]

### Features
- Canon recall merged into unified FTS pipeline (`source='canon'`): `MemoryStore.sync_dir(no_chunk=True)`, `get_chunk_content()`, `_sync_canon_store()` at bootstrap, `_search_canon_channel()` rewritten to BM25 + full-body fetch
- `canon_recall.py` deleted — bespoke token-overlap recall path removed
- `eval_canon_recall.py` updated with FTS-appropriate sub-cases (`canon-fts-match`, `canon-top-hit-relevant`)
