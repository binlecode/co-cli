# Changelog

## [Unreleased]

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
- **`CoDeps.spill_threshold_tokens`** replaces `request_aggregate_threshold_tokens`; `CoRuntimeState.current_request_tokens_after_spill` replaces `current_request_aggregate_tokens_after_spill`. Computed once at bootstrap as `int(spill_ratio × model_max_ctx)`.
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
