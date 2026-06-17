# Changelog

## [0.8.394]

Memory items are no longer phased out by age or recall frequency — recall-time ranking signals are no longer repurposed as deletion triggers. Storage is unconstrained and recall precision is a query-time concern (top-k + score-floor gated), so the only automated memory curation is now similarity-based merge.

- Removed memory decay end to end: `co_cli/memory/decay.py`, the daemon `decay_memory` housekeeping phase (and its `co.housekeeping.decay` span), the `memory.decay_after_days` / `memory.recall_protection_days` config keys + env vars, and the `HousekeepingStats.memory_decayed` counter. Zero-backward-compat — an explicit `settings.json` carrying the removed keys now fails config load (`extra="forbid"`); shell env vars degrade silently.
- Validity/supersession (retiring a now-false fact) is the agent's explicit `memory_manage` action; skills decay is unchanged (manifest-budget cost).
- Fixed an archive re-index leak: `MemoryStore.sync_dir`/`rebuild` now index top-level `*.md` only (mirroring `load_memory_items`), so `_archive/` is never traversed and archived items stay out of the index.
- Added a warn-only safety-net tripwire (`MEMORY_ITEM_COUNT_WARN = 10_000`): an over-cap active store emits a log warning + `co.housekeeping.memory_count_warn` span event and flags the `/memory` and banner count yellow — never evicts. Crossing it signals a write loop / runaway / pollution to investigate.

## [0.8.393]

Dream daemon failures are no longer silent. The daemon runs detached with stdout/stderr at DEVNULL, so its JSONL log is the only durable error channel — and two paths bypassed it.

- KICK review failures now log at WARNING with a full traceback (`exc_info`) on every failed attempt (not just the terminal one), so transient and retried failures are visible in `co-dream.jsonl` — previously the only trace was a stackless `last_error` in the failed-queue sidecar, written only after retries exhausted.
- Uncaught exceptions from bootstrap or the main loop now log at ERROR with a traceback before the process exits (the PID file is still cleaned by the finally-block); previously a crash left no record anywhere.
- Spec (`dream.md` §1.4, §2.6) updated to document both logging paths.

## [0.8.392]

The `/dream` slash command gains full daemon lifecycle control from inside the REPL — previously you could only *see* the daemon was down but had to drop to a shell to act.

- `/dream` now dispatches `start | stop | tidy` (plus the existing status default); each routes to the existing detached `process.py` control surface, so the daemon's lifetime stays independent of the REPL.
- `start` works regardless of `dream.enabled` (which gates only auto-spawn) and is idempotent — a second `start` reports already-running without aborting the turn (guarded `SystemExit`).
- `stop` is force-gated on both surfaces: the daemon is a per-`CO_HOME` singleton, so a bare `/dream stop` / `co dream stop` warns and no-ops; `/dream stop force`, `co dream stop --yes` (graceful), or `co dream stop --force` (SIGKILL) confirm.
- Renamed the housekeeping verb `run` → `tidy` consistently across both surfaces (`co dream tidy`, `/dream tidy`) and the sentinel `DREAM_RUN_TAG`/`run.tag` → `DREAM_TIDY_TAG`/`tidy.tag`. Zero-backward-compat, no alias.
- Down-state status hint now names the in-REPL `/dream start` rather than directing the user to a shell.

## [0.8.390]

Tool execution now shows live timing, matching the existing `Thinking… Ns` / `Thought for Ns` reasoning vocabulary — on a slow local model a running tool is no longer indistinguishable from a hung one.

- In-flight tool panel paints a live `(Ns)` elapsed label, advanced by a single App-level wall-clock ticker gated on "any tool active" (one task for all tools, modeled on the reasoning ticker); degrades to a no-op for headless/sync callers with no running loop.
- Every finished tool commits a standalone `label (Ns)` duration line independent of the result payload, so structured/empty-result tools also report timing.
- Ticker is cancelled and start-stamps cleared on every teardown path (`_close_tool` when the last tool closes, plus `cleanup()` on error/interrupt) so no leaked ticker repaints a torn-down region.
- Default `reasoning_display` flipped from `collapsed` to `full` — reasoning bodies stream visibly by default; `off`/`collapsed` remain selectable via config, env, `--reasoning-display`, and `/reasoning`.

## [0.8.388]

Measured compaction-summarizer fidelity on the configured local model and revised the handoff template to match what the model honors.

- Added a throwaway eval harness (`evals/eval_summarizer_fidelity.py`) that drives real compactions over three synthesized transcripts (user-correction, tool-heavy, carry-forward), scores section/verbatim/tool-name/path/carry-forward fidelity deterministically over N=5 samples per arm, and emits a paired A/B JSONL verdict.
- Measured verdict on `qwen3.6:35b-a3b-agentic`: REVISE — the model ignored the omit-empty SKIP RULE (emitting `None.` placeholders) and dropped the verbatim drift anchor once a task completed.
- Revised `_SUMMARIZE_PROMPT` to keep-every-section with `(none)` placeholders, promoted `## User Corrections` to a permanent positioned section, and made `## Active Task` preserve the user's request verbatim with `(completed)`. Removed the contradictory leftover SKIP RULE. Paired A/B re-measure flips the verdict to COMPLIANT (all properties 1.00, no regression).

## [0.8.387]

Fixed broken spilled-tool-result re-fetch — `file_read` can now read back its own spilled outputs.

- `spill_if_oversized` writes oversized tool results under `~/.co-cli/tool-results/` and the placeholder instructs the model to `file_read` that path, but `file_read`'s boundary check only permitted `file_search_roots`, so every re-fetch failed with "Path escapes all read roots".
- `file_read` now includes `tool_results_dir` among its allowed read roots (file_search is unaffected — the directory remains unlistable).
- Added a functional round-trip test (spill → file_read) in `test_flow_files_read.py`.

## [0.8.386]

Rules-conformance cleanup — drained the current batch of R1 one-sided (write-only / dead) members surfaced by the periodic whole-codebase audit. All behavior-preserving subtractions.

- Removed three clean write-only/dead fields: `WebRetryResult.status_code`, `TurnResult.streamed_text`, `SearchResult.confidence`.
- Removed `CoRuntimeState.background_status_callback` (live status reaches consumers via `frontend.on_status`) plus its bootstrap assignment and survival test.
- Dropped the write-only `HousekeepingStats.done_pruned`/`session_pruned` counters, their increments, the now-orphaned `state` param on `prune_done_and_snapshots`/`prune_sessions`, and their test assertions.
- Synced `core-loop.md` and `dream.md` to match.

## [0.8.384]

FTS / hybrid recall hardening — a no-match query now returns few/zero results instead of calibration-free junk, lexical-only mode is a single switch, and past-conversation questions reach session recall.

### Relevance floors (eval-calibrated, ship on by default)

- Pre-fusion **vector-similarity floor** (`vector_similarity_floor`, default 0.02): vector-only candidates below the cosine floor are dropped before RRF fusion; BM25/lexical hits are always kept.
- Post-fusion **reranker-score floor** (`rerank_score_floor`, default 0.2): when the TEI reranker succeeds, hits below the floor are dropped; skipped when the reranker is absent or its breaker is open (an all-below-floor result stays breaker-closed).

### Lexical-only mode + observability

- The reranker is now gated to hybrid mode — `search_backend=fts5`/`grep` issues zero reranker calls, making a fully lexical, no-external-model run a single switch.
- Runtime hybrid→FTS degradation emits an `index.hybrid_degraded_to_fts` span event (visible in `co tail` / `co trace`).

### Tool surface

- `memory_view` clamps oversized artifact bodies (`VIEW_MAX_BODY_CHARS`) with a truncation marker, so one artifact can no longer flood context.
- `session_search` visibility flipped DEFERRED → ALWAYS so past-conversation questions reach session recall instead of misrouting to `memory_search`.

## [0.8.382]

Cross-session recall concept expansion — bridge vocabulary mismatch when a past session recorded an entity in different words than the question asks.

### Feature: regex/pattern search path for session recall

- `session_search` gains a `pattern=` parameter (regex, case-insensitive), mutually exclusive with literal `query=`; an invalid pattern is `re.compile`-validated up front and returns an explicit `tool_error`, never a silent empty result or a fallthrough to the Python line-scan.
- Engine: `search_sessions(..., *, is_regex=False)` / `SessionStore.search(..., *, is_regex=False)` return a `SessionSearchResult` (`hits` + optional `error`); literal default path is byte-for-byte unchanged.
- Guidance: the `session_search` docstring gains a concept→pattern/synonym/multi-angle "expanding intent" block; `07_memory_protocol.md` gains a three-rung cross-session recall cascade (literal → pattern/synonym angles → honest miss) carrying behavioral triggers only (no deferred-tool call signatures on the floor).
- Instruction-floor ceiling re-pinned 24,200 → 25,000 to absorb the intentional cascade addition (cascade trimmed to a terse skeleton first; full detail rides the deferred docstring).

Rules-conformance cleanup (R4 layer back-edges) — eliminate three lower-layer-imports-higher-layer edges by pure module relocation (behavior-preserving).

### Refactor: relocate misfiled symbols to their owning layer

- `llm → session`: split `session/usage.py` — the realtime accumulator (`UsageAccumulator`, `record_usage`) moves down to `observability/usage.py`; the durable ledger stays in `session/usage.py`.
- `llm → context`: move `sanitize_surrogate_codepoints_messages` (+ helpers) from `context/history_processors.py` into the new `llm/_message_sanitize.py` (its sole caller is surrogate recovery).
- `context → daemons`: relocate the KICK producer `write_review_kick` from `daemons/dream/kick.py` to `session/review_kick.py` so both producers import down.

### Test hardening: vision turns immune to local-GPU throttling

- `test_flow_multimodal_prompt` and `test_flow_user_image_intake` now build the live vision turn against a minimal orchestrator spec + empty toolset, cutting the prefill from ~16k tokens to ~40. The behavior under test (multimodal prompt threading, image-path intake) is unchanged; the turn no longer times out under sustained suite load.

## [0.8.378]

Session retention — opt-in age-based pruning of session transcripts via the dream daemon's housekeeping pass. New `session_retention_days` knob (`CO_DREAM_SESSION_RETENTION_DAYS`, default `0` = disabled, recommended 30) deletes canonical session `.jsonl` files older than N days; adds a `session_pruned` counter and a per-pass summary log line.

## [0.8.376]

Rules-conformance cleanup — remove the rejected fitness-function scaffolding and fix the real coding-rule violations it was avoiding (behavior-preserving).

### Refactor: enforce coding rules at the source, not via structural tests

- Removed the rejected arch/test-hygiene fitness functions, snapshot/debt artifacts, the `import-linter` dev dep, and both superseded plans. The test suite is functional-only again.
- Fixed the `display._app` underscore-visibility leak: `display/_app.py` → `display/app.py`, `_ReplRuntime` → `ReplRuntime` (public surface consumed by `main.py`).
- Broke the `session → tools` and `display → commands` import cycles: relocated `build_subprocess_env` (+ `SAFE_ENV_VARS`/`restricted_env`) to the new low-level `co_cli/proc/env.py`; the REPL app now depends on prompt_toolkit's `Completer` ABC with the concrete completer injected from the composition root.
- Resolved `bootstrap` back-edges: `bootstrap/check.py` → `co_cli/check.py`, `bootstrap/project_info.py` → `co_cli/project_info.py`, status helpers extracted to `co_cli/commands/status_report.py`.
- Convention cleanup: dream `_queue.py` full-file write now routes through the shared `atomic_write_text`; unit-suffix added to five constants (`*_BUDGET_CHARS`, `_FETCH_TIMEOUT_SECONDS`, `_SEARCH_TIMEOUT_SECONDS`).

## [0.8.374]

Batch of ad-hoc cross-team work.

### Fix: atomic session-transcript writes

- `append_messages` / `_write_messages` now create the JSONL file via `os.open(..., 0o600)` with `O_CREAT|O_APPEND` / `O_CREAT|O_TRUNC`, so the file is created with owner-only permissions atomically — closing the window where the transcript was briefly group/other-readable between `open()` and the old `chmod(0o600)`.

### Feature: status glyphs in list commands

- `/filescope`, `/skills`, `/tools` now render aligned tables with status glyphs: ✓/✗ root existence, ✓/✗ user-invocable vs model-only, ●/○ always-loaded vs deferred tool visibility.

### Docs

- Added the code regulation model (Tier-0/1/2 enforcement; prose rules re-litigated ≥3× must graduate to fitness functions or retire) and the clarity-by-subtraction ruleset to `.agent_docs/review.md`; wired `clean-tests` and `review-impl` skills to it.
- Synced `tools.md` (drop removed `requires_config` gate; `check_fn` sole per-turn gate; `approval` → `is_approval_required`) and `code-conventions.md` (IndexStore transaction) to current code.

## [0.8.372]

### Feature: semantic session label in the status footer

- **The status footer now shows a human-readable session label instead of the opaque 8-char id.** Before the first message the footer reads `(new session)`; once the first turn persists it flips to the first-user-message title (truncated to 30 chars with `…`), matching what the `/resume` picker already shows.
- **`_extract_title` promoted to public `extract_title`** (`co_cli/session/browser.py`) so the live-session label reuses the same derivation as the picker.
- **New `CoSessionState.session_title`** caches the label — set once after the first successful persist (frozen thereafter, so it survives in-place compaction that may drop the original first message), and populated immediately on `/resume` from the selected session's title.
- **`(untitled)` is unchanged** — it remains the parse-failure fallback for a persisted session whose first user-prompt can't be read, distinct from the pre-first-message `(new session)` placeholder.

## [0.8.370]

### Refactor: rename the context-window family `max_ctx` → `max_context_tokens`

- **Config field + JSON key renamed `max_ctx` → `max_context_tokens`.** **Breaking config change (zero-backward-compat, no alias):** if your `~/.co-cli/settings.json` sets `"max_ctx"`, rename the key to `"max_context_tokens"` — the old key is no longer read. The value (default `65536`) and all ceiling/floor/compaction-budget behavior are byte-for-byte unchanged.
- **Companion renames:** constant `DEFAULT_MAX_CTX` → `MAX_CONTEXT_TOKENS` (drops the `DEFAULT_` prefix — it names a control limit, not a fallback default); runtime `model_max_ctx` → `model_max_context_tokens`; eval settings `EVAL_MAX_CTX` → `EVAL_MAX_CONTEXT_TOKENS` and `eval_max_ctx()` → `eval_max_context_tokens()`.
- **Telemetry key:** the span attribute `"ctx.max_ctx"` (`ctx_overflow_check` event) renames to `"ctx.max_context_tokens"`; external trace parsers keyed on the old string must update.
- **Rationale:** the codebase names token quantities with a `_tokens` suffix (`spill_threshold_tokens`, `static_floor_tokens`, `peak_input_tokens`); `max_ctx` was the lone unit-less, abbreviation-laden outlier. `num_ctx` (Ollama's own API parameter) is a distinct concept and is untouched.
- **Zero behavior change.** Pure identifier/key rename.

## [0.8.368]

### Refactor: tighten the turn-level model-request cap (90 → 40)

- **`max_model_requests_per_turn` default lowered 90 → 40.** This turn-cumulative cap is the only guard against an in-cap doom-loop (a model re-issuing 1–3 tool calls per request indefinitely — the consecutive-over-cap hard-stop never trips because the streak resets on any ≤3-call request). At 90 such a loop burned ~90 multi-second local-model requests (a wedged-looking session) before firing; 40 stops it an order of magnitude sooner. Sized as a circuit breaker: ≈5–6× over typical real usage (~7/turn) with >2× margin over the multi-resume worst case (~20–25), above opencode's single-loop 25 because co's approval-split turns span more cumulative requests.
- **Constant renamed `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` → `MAX_MODEL_REQUESTS_PER_TURN`** (it names a control limit, not a fallback default; zero-backward-compat, no alias).
- **Specs:** folded the circuit-breaker sizing rationale into `core-loop.md` §1 and corrected the stale 90 → 40 there and in `config.md`.

## [0.8.367]

### Fix: show `ctx %` immediately after a transcript swap

- **`/resume` and `/compact` now seed the context-usage estimate.** The footer's `ctx %` read `current_request_tokens_estimate`, which was only written by the history processor during a turn — so a restored or compacted session showed no `ctx %` until the first message. The `ReplaceTranscript` outcome now seeds the estimate via `static_floor_tokens + estimate_message_tokens(history)`, mirroring the spill-trigger formula so the value cannot drift.

## [0.8.366]

### Refactor: drop the "segment" term — align co's vocabulary to pydantic-ai's "run"

- **Vocabulary now `turn ⊇ run ⊇ model request`.** co's coined "segment" was a 1:1 synonym for pydantic-ai's native **run** (one `agent.run_stream_events()` → one `AgentRunResult`). Removed the redundant third noun so the orchestration model matches the SDK's own.
- **Renames (zero-backward-compat, no aliases):** `_execute_stream_segment` → `_execute_run`; `LLM_SEGMENT_TIMEOUT_SECS` → `LLM_RUN_TIMEOUT_SECS`; `_LLM_SEGMENT_WARN_SECS` → `_LLM_RUN_WARN_SECS`; test `test_multi_segment_turn_records_final_usage_once` → `test_multi_run_turn_records_final_usage_once`. Run-sense comments, docstrings, and the 4 observability string literals (log/span/exception) swept to "run".
- **Specs aligned:** added the canonical `turn ⊇ run ⊇ model request` hierarchy to `core-loop.md`, swept ~40 run-sense refs across 9 specs, and fixed a pre-existing drift (`core-loop.md` cited the non-existent `_LLM_SEGMENT_HANG_TIMEOUT_SECS`; now `LLM_RUN_TIMEOUT_SECS`).
- **Zero behavior change.** Pure rename; constant values unchanged (120s / 90s). The three unrelated "segment" meanings (SIGSEGV, tool-name `_`-split, compaction text-split) left intact.

## [0.8.364]

### Feature: `/reasoning` with no argument opens an interactive picker

- Bare `/reasoning` now renders a list picker (off / collapsed / full) with the current mode pre-highlighted, navigable with up/down and selected with enter (escape/q cancels). Previously it only printed the current mode.
- Falls back to printing the current mode when no interactive frontend is present. Explicit-token (`/reasoning full`) and `next`/`cycle` paths are unchanged.

## [0.8.363]

### Fix: bump pydantic-ai 1.81.0 → 1.92.0 to fix MCP cancel-scope crash

- **Cancel-scope crash fixed.** Turns cancelled mid-stream (Esc / timeout) while a stdio MCP server was connected intermittently raised `RuntimeError: Attempted to exit a cancel scope that isn't the current task's current cancel scope`. pydantic-ai v1.92.0 ships the upstream fixes (PR #4514 — MCP session in a dedicated task; PR #5313 — streaming-response cleanup on cancellation). Verified across 6 consecutive `eval_multistep_plan` runs with zero occurrences.
- **API drift adapted (zero-backward-compat, no shims).** `Agent(retries=…)` → `Agent(tool_retries=…)` (PR #5075 deprecation) at `agent/build.py`; `run_stream_events` direct iteration → `async with … as stream:` at `context/orchestrate.py` (the context-manager form is the cancellation-cleanup mechanism).
- **Transitive deps held at floor** (mcp 1.26.0, anyio 4.12.1) — no downgrade.

## [0.8.362]

### Refactor: reasoning display — drop `summary`, adopt `Thinking… Ns` / `Thought for Ns`

- **`summary` mode removed.** The outlier last-sentence reducer (`_reduce_thinking`) and its discard-on-supersede path are deleted. `reasoning_display` is now `off` / `collapsed` / `full`, defaulting to `collapsed`.
- **`collapsed` (new default).** A live transient `Thinking… Ns` header that commits a single durable `Thought for Ns` line; the raw reasoning body is never shown. To read the raw reasoning, switch to `full`.
- **`full`.** Streams the `Thinking… Ns` header plus the raw thinking body, committing the body + `Thought for Ns` footer.
- **Event-driven elapsed.** The counter advances as thinking deltas arrive; the committed `Thought for Ns` is measured at thinking-end (wall-clock accurate). No periodic ticker was added.
- **Frontend cleanup.** `on_reasoning_progress` and the transient `"reasoning"` in-flight kind are removed; `StreamRenderer` composes per-mode strings into the existing `on_thinking_delta` / `on_thinking_commit` surfaces. Tool-start annotations now render in all modes (the old `summary`-only suppression is gone).

## [0.8.360]

### Feature: shell_exec auto-yields an unbounded command to a background task

- **Turn no longer blocks on a stuck command.** A foreground `shell_exec` command still running after `shell.yield_window_seconds` (default 20s; `0` disables) is now auto-promoted to a background task instead of holding the turn open to the hard timeout. An unbounded command (`mpv <url>`, `tail -f`, a dev server) no longer makes the REPL appear frozen.
- **Same live process, no re-spawn, no re-gate.** The non-pty `ShellBackend.run_command` drains stdout with a bounded incremental read; on yield it hands the *live* process back as a `YieldedProcess`. `shell_exec` adopts that same process via `adopt_running_process` (`background.py`) into a `BackgroundTaskState` — never killed-and-re-run (no double-execute), and not re-gated (it already cleared the approval gate before spawn).
- **Output continuity.** Bytes read before yield seed the task log; the adopt monitor reuses the same live stream (shared `_drain_to_log` tail) — no lost prefix, dup, or gap. The result is a task handle (`task_id` + partial output) — inspect with `task_status`, stop with `task_cancel`, exactly like a `task_start` task.
- **Config + exemption.** New `shell.yield_window_seconds` (`CO_SHELL_YIELD_WINDOW_SECONDS`), validated below `max_timeout`. `pty=True` is exempt (its master-fd drain has no `proc.stdout` to hand off) and keeps the plain hard-timeout behavior.

## [0.8.359]

### Fix: kill the shell process group when a turn is cancelled mid-command

- **Orphaned subprocess on cancel.** `shell_exec` spawns with `start_new_session=True` (own process group), but cancelling a turn mid-command (Esc) only raised `CancelledError` out of the backend — the child was left running, reparented to init. A long foreground command (e.g. `mpv` streaming audio) kept running after the prompt returned, and the REPL appeared "blocked" while it held the turn open.
- **Cancellation-safe group teardown.** On `CancelledError`, both the plain and pty paths now send an immediate synchronous `SIGTERM` to the process group (`terminate_process_group`), then schedule `kill_process_tree` as a retained background task to escalate to `SIGKILL` if the group ignores `SIGTERM`. The async escalation cannot run inline (the awaiting task is itself being cancelled), so it rides an independent task — mirroring the peer pattern (openclaw's unref'd timer, opencode's acquireRelease finalizer, hermes's kill-before-re-raise).

## [0.8.358]

### Feature: `/status` command + TUI render-fidelity harness

- **`/status` slash command** — consolidated current-state report (Session, Model & context, Dream, Work in flight, Capabilities, Degraded). Read-only: assembles from in-memory `deps` plus cheap local reads, degrading each section to a placeholder rather than aborting. Capability counts shared with the banner via `build_status_counts` (`bootstrap/banner.py`) so the two cannot diverge.
- **REPL render fixes** — `patch_stdout(raw=True)` so the themed console's SGR passes through `write_raw` instead of being sanitized to `?` (was rendering garbled escape sequences mid-app); plus an input echo in `_handle_one_input` so submitted commands/turns leave a record in scrollback (the inline `TextArea` never committed accepted input).
- **`build_key_bindings(frontend=...)`** — prompt-mode key bindings now gate on `frontend.prompt_active`; in-app y/n/a approval resolves on the running app's event loop (replacing the deadlock-prone `run_in_terminal` path).
- **Render-fidelity test harness** — `tests/integration/_tui_harness.py` drives the real `build_repl_app` through pipe-fed keystrokes under production-equivalent `patch_stdout(raw=True)`, captures the actual ANSI byte stream via `Vt100_Output`, and forces the module console to emit SGR. First regression test over `/status` asserts input echo, no ESC-sanitization garble, real styled headers, and all six sections — guarding the two render regressions that previously shipped undetected.

## [0.8.356]

### Refactor: dream daemon config naming + daily-grid interval validation

- **`poll_interval_seconds` → `tick_interval_seconds`** (env `CO_DREAM_POLL_INTERVAL_SECONDS` → `CO_DREAM_TICK_INTERVAL_SECONDS`). The field is the idle-loop tick that drives both the queue scan *and* the housekeeping-schedule check — "poll" undersold it as queue-only. Spec descriptions reworded accordingly.
- **`run_at` → `run_start_at`** (env `CO_DREAM_RUN_AT` → `CO_DREAM_RUN_START_AT`) — clearer that it is the preferred start time-of-day for the scheduled housekeeping pass.
- **`run_interval_hours` daily-grid validation.** Because `run_start_at` is a once-per-day clamp, the effective cadence is quantized to whole days. A `field_validator` now rejects misaligned values: below 24 must be a factor of 24 (1, 2, 3, 4, 6, 8, 12); above 24 must be a multiple of 24 (48, 72, …). Prevents a configured interval from being silently rounded by the clamp.

## [0.8.354]

### Feature: user image intake — lone path-reference → `image_view` routing (`user-image-intake`)

- **Drag-and-send images now work.** When a user's submitted turn is, in its entirety, a single supported image path (the canonical drag-an-image-into-the-terminal gesture), the turn layer reads the pixels through `image_view`'s shared read core and splices them into the user prompt as `BinaryContent` — deterministically, before the LLM call (hermes parity). A vision-capable agent answers about the image on the same turn instead of the gesture silently no-opping.
- **Lone-path trigger only.** Detection requires the *entire* trimmed input (after quote-strip, `file://` + `%20` decode, `\<space>` unescape, `~` expansion, relative-resolve against `workspace_dir`) to be one path with a supported image suffix that exists. Any trailing text, question, or mid-sentence mention is ignored — talking about a file is not handing it over. At most one image per turn.
- **User-gesture read allowance.** A lone user-supplied path is read even when it resolves outside `file_search_roots` (e.g. a screenshot dragged from `~/Desktop`). Strictly scoped: preprocessor-only, read-only, one named file, still gated on `agent_vision_capable`. `image_view`'s agent-initiated path keeps the full `enforce_read_boundary` check unchanged.
- **Honest vision gate.** A blind model is never handed pixels — the preprocessor consults `agent_vision_capable` and, when the model can't see, emits exactly one notice and runs text-only.
- **Shared read core (refactor).** Byte-read + validation (exists/dir/MIME/size/`read_bytes`) factored into `read_image` in `co_cli/tools/vision/intake.py`; `image_view` and the new `detect_lone_image_path` both consume it (single source for the MIME set and 20 MB cap). Boundary resolution stays caller-side.
- **`BinaryContent`-aware token estimate (prereq).** `estimate_message_tokens` now counts a `BinaryContent` as a bounded flat image-token constant instead of `json.dumps`-ing the raw bytes — fixes a latent `TypeError` crash on the spill processor, `/compact`, and summary-budget calc, and prevents a multi-MB image from spuriously triggering compaction.
- **Multimodal prompt threading.** `run_turn` accepts `str | list[str | BinaryContent]`; the `co.user_prompt.chars` span sums string-part lengths only.

## [0.8.353]

### Bugfix: dream queue/review path — three logical defects (`dream-queue-review-fixes`)

- **Defect A — consistent terminal-move directories.** `main_loop` now takes injected `done_dir`/`failed_dir`; all three terminal transitions (success → `done/`, corrupt-KICK → `failed/`, exhausted-retry → `failed/`) use the same canonical, pre-created, CO_HOME-safe dirs. Removes the `queue_dir.parent / "failed"` drift that misfiled corrupt KICKs to an orphan bin.
- **Defect B — full-fidelity pre-compaction review.** At the `compact_messages` chokepoint (the one place whole messages are dropped), the full pre-drop message list is snapshotted to `daemons/dream/snapshots/` and a memory review KICK fires against it (new `transcript_override` KICK field, read uncapped by `process_review`). The reviewer now extracts durable facts from the original turns instead of re-summarizing co's own lossy compaction marker. Snapshot is unlinked on terminal KICK transition; one capture covers proactive PATH 2, overflow PATH 2, and `/compact`. A one-shot `runtime.skip_compaction_snapshot` flag suppresses only the no-progress escalation re-entry, so genuine second-in-turn compactions still snapshot.
- **Defect C — bounded `done/` + snapshot retention.** New housekeeping prune phase deletes `done/` files and orphaned snapshots older than `done_retention_days` (default 7, `CO_DREAM_DONE_RETENTION_DAYS`); `failed/` left intact for diagnostics.
- **Shared KICK producer.** Extracted `write_review_kick` into `co_cli/daemons/dream/kick.py` (no context→dream import cycle); `main.py`'s counter/session-end producers and `compaction.py` both call it.

## [0.8.352]

### Feature: interactive terminal — `shell_exec(pty=True)` + `task_write`/`task_close` stdin drive; dream curation lens

- **Interactive command-line capability (`toolgap-interactive-terminal`).** Two deliberately-separated surfaces: output *fidelity* on the one-shot path, input *drive* on the background path.
  - `shell_exec(pty=True)`: runs the one-shot command under a stdlib `pty.openpty()` pseudo-terminal so the child sees a TTY (`isatty()` true, ANSI colors, line-buffering). Stays blocking/one-shot — same timeout, `work_dir`, policy gate, `kill_process_tree`-on-timeout. No stdin channel; raw ANSI preserved; never interactive drive. `pty=False` (default) is the unchanged `proc.communicate()` path. Stdlib only — no `ptyprocess`/`pywinpty`; master-fd read treats Linux `b""` and macOS `OSError`/EIO alike as clean EOF, and both pty fds close on every path (incl. spawn failure).
  - `task_write(task_id, input, newline=True)` / `task_close(task_id)`: `task_start` now spawns with `stdin=PIPE`; `task_write` answers a running task's prompt (newline = submit), `task_close` signals EOF. Both DEFERRED (ALWAYS floor unchanged). Writing to a finished task or closed pipe surfaces a clean `tool_error` (`TaskInputError`). Approval gate stays at `task_start` (the command), not per-write.
  - `/write <id> <input>` slash command — human symmetry with `/background`; first token is the task id, remainder passed to stdin verbatim (never `shlex`-split).
  - Docstrings steer the model to prefer a non-interactive flag (`--yes`, `--no-input`, `gh auth login --with-token`, `git ... --no-edit`) first and reach for the write loop only when none exists. `PER_ALWAYS_TOOL_CEILING` re-pinned 2300→2500 for the `pty` param (bucket held at 17,700).
- **Dream curation lens.** The active soul's `souls/{role}/curation.md` is now appended to the dream daemon's memory/skill review prompts via `load_soul_curation`, scoping retention judgment (durable-signal threshold, merge disposition) into curation without importing voice. Gated on `deps.config.personality`, degrades to the bare base prompt when absent. Ships curation lenses for `finch`, `jeff`, `tars`.

## [0.8.350]

### Refactor: agentic-loop eval realignment to first principles (W11/W12 → v2 rubrics)

- Realigns two behavioral evals that encoded rigid/adversarial proxies instead of true agentic-loop first principles. Governing rule held: fix the eval, never tune the prompt to pass a misaligned eval. M1 measurement (distinct Gemini judge, trace-validated) confirmed the model already behaves correctly on all three axes the prompt tasks targeted — so all three conditional prompt/doctrine changes (T-3/T-4/T-5) were dropped; no `co_cli/context/rules/*` doctrine changed this cycle.
- `evals/eval_multistep_plan.py` (W11): replaces the `t0_jumped_to_tools` (any tool call in turn 0 = FAIL) gate with `_mutated_before_plan` — a `_MUTATING_TOOLS` frozenset (`file_write` + the memory write ops) gates on state mutation before a plan signal (`todo_write` or ≥3 enumerated steps); recon reads/searches before the plan are expected, not a violation.
- `evals/eval_agentic_loop.py` (W12.B/C): drops the "keep trying until it works" / "please keep retrying" instructions from the prompts so the case tests *natural* loop-avoidance; verdict now gates on a self-initiated identical-call streak reaching `doom_loop_threshold` (pinned to its floor) AND whether the agent surfaced the blocker. Removed a pre-existing orphan `_used_shell_command` helper.
- `evals/_rubrics/multistep_plan.v2.md`, `evals/_rubrics/agentic_loop.v2.md`: new v2 rubrics (v1 retained for audit) encoding plan-before-mutation and natural loop-avoidance with PASS/FAIL calibration transcripts.
- Spec: `docs/specs/uat_evals.md` W11/W12 rows, Test-Gates entries, and rubric table synced to the realigned criteria and v2 rubrics.

## [0.8.348]

### Feature: UAT behavioral+performance eval suite — phase 2 close-out

- Closes out the phase-2 behavioral eval suite (W7–W12) with the 2026-06-11/12 validation pass: perf-band calibration, eval-harness fixes, and the W10 recall→reuse rescope. The suite now drives a real workspace, traces per-turn deltas correctly, and gates perf on calibrated bands.
- `evals/_perf.py` + `evals/_timeouts.py`: T-8b calibration — `PERF_BANDS_GATING=True`, `WARM_CALL_BUDGET_S=15.0` (≈1.5× the warm p95 of 9.7s; the 24–36s tail is decode-bound, not latency), `PEAK_INPUT_TOKENS_BUDGET=24000` (≈1.5× observed max, zero overflows). PROVISIONAL markers dropped. SOFT_FAIL stays a review signal, never overrides a behavioral FAIL.
- `evals/_trace.py`: per-case message slicing so each `TurnTrace` records only the turn's new messages (was including cumulative history from `all_messages()`); plus verbatim system-prompt capture so a reviewer can confirm what context actually reached the model.
- `evals/_settings.py` + `evals/_fixtures.py`: workspace isolation — `EVAL_WORKSPACE_DIR` is wiped per build (no cross-run file bleed), and `load_fixture` copies an optional `<name>/workspace/` subtree so file-based cases run against real files (new `multistep_research_baseline/workspace/` helios codebase).
- `evals/eval_user_model.py` + `evals/_rubrics/user_model.v2.md`: W10 rescoped from "auto-applied preferences" to the recall→reuse path co actually supports (memory is recall-on-demand, not auto-injected) — each case now pairs a structural recall-tool check with the v2 rubric verdict.
- `evals/eval_approval_discipline.py`: W8 scratch dir migrated from `tmp/` to the isolated `EVAL_WORKSPACE_DIR`.
- Spec: `docs/specs/uat_evals.md` synced to the behavior+performance-only suite (W12 + perf dimension + pytest-coverage map).

## [0.8.346]

### Feature: scanned/image-only PDF reading (tier-2 render → vision)

- A scanned PDF (photographed contract, receipt, handout — no text layer) used to dead-end at the tier-1 `[no-text-layer: likely scanned]` sentinel even though the configured model can see images. Adds the thin render+route glue so the `documents` skill reads those pages with the agent model's own vision — no new tool, no new skill, no OCR engine.
- `co_cli/skills/documents/scripts/extract_pdf.py`: adds a `--render` mode on the existing `co-extract-pdf` console command. Reuses the open pymupdf handle to rasterize the selected pages to PNGs (150 DPI, long-edge clamped to ~2000 px / ~4 MP — the measured model downsample ceiling) into a script-owned OS tempdir, and emits a pinned stdout contract: one `<page>⇥<abs-png-path>` line per page plus a final `total_pages=M` line so a caller detects truncation. Honors `--pages`, `--max-pages` (default 10), `--outdir`; corrupt/encrypted PDFs reuse the existing distinct non-zero exits.
- `co_cli/skills/documents/SKILL.md`: Step 5 scanned branch. When `image_view` is absent (text-only model) it degrades honestly — names the cause and suggests `web_fetch`/conversion, never fakes a read. When available, it renders, reads each page through `image_view` one at a time, **transcribing each page to text before advancing** (only the tail page's pixels stay in view per the vision history processor), then synthesizes with page-N grounding and states any page truncation.
- Tests: `tests/test_flow_scanned_pdf.py` (new) + a committed 3-page image-only fixture `tests/skills/fixtures/scanned_invoice.pdf` — real pymupdf render + truncation (no mocks), always-run honest-degradation assertion, and a vision E2E (reads "540.00 USD" off page 3) skipped on text-only hosts.
- Spec: `docs/specs/skills-document.md` (new) — the namespaced per-skill spec covering the documents skill's two-tier read end to end (already forward-referenced from `skills.md` and the `01-system.md` index).

## [0.8.344]

### Feature: office skill — local `.docx`/`.pptx`/`.xlsx` text extraction

- co was blind to Office documents (`file_read` rejects binary). Adds an `office` bundled skill mirroring the `documents` (PDF) skill: locate → extract → answer, driven over `shell_exec` + a thin subprocess script. No new tool, no model-multimodal plumbing.
- `co_cli/skills/office/scripts/extract_office.py` (new): the `co-extract-office` console entry point. Dispatches by extension to ML-free format-specific backends — `mammoth` (docx), `python-pptx` (pptx), `openpyxl` (xlsx) — emitting markdown with `## Slide N` (pptx), `## Sheet <name>` tables (xlsx), and native headings for docx (flat prose where no heading styles exist). xlsx is row-capped with an explicit `[truncated: …]` notice; five distinct one-line error messages (missing / unsupported / corrupt / password-protected via CFB magic-byte sniff / `.pdf`→documents), each non-zero exit with no traceback.
- `co_cli/skills/office/SKILL.md` (new): bundled, `user-invocable: false`; routes `.pdf` to the `documents` skill and URLs to `web_fetch`. Reciprocal with `documents` — each owns one backend and one citation contract.
- Dependencies are ML-free by construction (no torch/CUDA, no onnxruntime/magika, no pandas): `mammoth>=1.12.0`, `python-pptx>=1.0.2`, `openpyxl>=3.1.5`.
- Tests: real-command `subprocess.run(["co-extract-office", …])` over committed docx/pptx/xlsx fixtures + error paths (no mocks); `office` registered in the bundled-library gate. Eval: new W4.B real-LLM model-selection case asserting `documents`↔`office` mutual exclusivity (pdf→documents, pptx/xlsx→office, bare URL→neither).
- Spec: `docs/specs/skills-office.md` (new); one-line pointer in `skills.md`; registered in the `01-system.md` Component Docs index.

## [0.8.343]

### Fix: dream daemon unresponsive to SIGTERM during cold bootstrap

- The dream daemon ran its canon/memory index-sync (a synchronous, blocking embedding `httpx.post`) directly on the event-loop thread inside `create_deps`. On a cold embedding backend the first embed blocks ~10s while the model loads, during which the loop cannot deliver the SIGTERM callback — so a stop issued mid-bootstrap was ignored until the embed returned, then force-killed after the full grace window (bypassing the daemon's own clean teardown).
- `co_cli/bootstrap/core.py`: the canon + memory index-sync now runs in an `asyncio.to_thread` worker (`_sync_indexes_offthread`) that opens its **own** short-lived `IndexStore` connection (sqlite connections are thread-affine). The embed `timeout=30.0` and `co_cli/index/` internals are unchanged — only where the work runs changes.
- `co_cli/daemons/dream/process.py`: `_run_foreground` races `create_deps` against the shutdown event (`asyncio.wait(FIRST_COMPLETED)`); a stop arriving mid-bootstrap cancels bootstrap, unlinks the PID file, and calls `os._exit(0)` (skipping `asyncio.run`'s join of the uncancellable embed worker). `stop_daemon`'s SIGTERM→SIGKILL grace tightened 10s→`STOP_GRACE_SECONDS` (3s); SIGKILL fallback retained.
- Daemon integration tests now spawn via the detached launcher (production path) and assert cooperative shutdown (the "daemon stopped" branch, not SIGKILL). Wall-clock: `test_stop_daemon_terminates_process` 13s→2.8s, `test_queued_kick_processed_after_daemon_restart` 27s→6.5s.
- Test hygiene: narrow `filterwarnings` ignore for the benign, non-deterministic `BaseSubprocessTransport.__del__` "Event loop is closed" teardown warning (a pytest per-test-loop GC race with no production impact).
- Spec synced: `docs/specs/dream.md` (stop grace, startup sequence, new "Bootstrap responsiveness" paragraph, `create_deps` daemon-path contract).

## [0.8.342]

### Feature: vision input — `image_view` tool

- New DEFERRED, capability-gated `image_view(path, prompt)` tool (`co_cli/tools/vision/view.py`) — reads a local image (png/jpeg/webp/gif) and attaches the real pixels via `ToolReturn.content`, which pydantic-ai materializes as a separate `UserPromptPart` so the agent model sees them next turn. Read-boundary-confined, ~20 MB cap, media-type allowlist (PDFs routed to the `documents` skill).
- Vision is the **agent model's own capability or nothing** — no describe-fallback, no pinned `vision_model`. Resolved once at bootstrap onto `deps.agent_vision_capable` (True for Gemini and for an Ollama model whose `/api/show` reports `vision`). `probe_ollama_model` now returns `OllamaModelProbe` (num_ctx + vision) from the same `/api/show` call, degrading to `vision=False` on error.
- Honest gate: when the agent model can't see, `image_view` self-hides (`check_fn=_vision_available`) and `tool_view` returns a remediation message instead of revealing a tool that can never materialize — also closing a pre-existing phantom-load gap for the `check_fn`-gated Google tools.
- New history processor `elide_old_multimodal_prompts` (`co_cli/context/history_processors.py`) elides multimodal pixels from non-tail `UserPromptPart`s on replay (preserving the most-recent turn), so base64 does not accumulate across turns.
- Tests: `tests/test_flow_vision.py` (real-model native-pixel read + deterministic gate/error/elision cases, no mocks). Specs synced: `tools.md`, `config.md`, `compaction.md`, `bootstrap.md`.

## [0.8.340]

### Feature: `documents` skill — local PDF text extraction

- New bundled, model-invocable `documents` skill (`co_cli/skills/documents/SKILL.md`) — drives a Locate → Extract → Answer flow over `file_search`/`web_fetch`/`file_read`/`shell_exec`; PDF-only, defers Office formats to the future `office` skill and URLs to `web_fetch`.
- First **skill-bundled executable asset**: `co_cli/skills/documents/scripts/extract_pdf.py`, exposed as the `co-extract-pdf` console entry point (`[project.scripts]`) and reached only via `shell_exec` (subprocess isolation, never imported into the agent). Emits markdown with `## Page N` citation markers; distinct non-zero error paths for missing/non-PDF/corrupt/password-protected; emits the exit-0 sentinel `[no-text-layer: likely scanned]` for image-only PDFs (the tier-2 vision seam — no OCR/vision here).
- Dependency: `pymupdf4llm==1.27.2.3` (version-synced; pulls `pymupdf`/`pymupdf-layout`/`onnxruntime` in lockstep) + unconstrained `pymupdf`. No PyTorch/marker-pdf.
- Scanned detection reads pymupdf's raw text layer (placeholder-proof); pymupdf-layout's C-level fd-1 chatter is silenced so stdout stays clean.
- Spec: `docs/specs/skills.md` gains a "Bundled Executable Assets" section (console-entry-point convention, `co_cli/` ruff/T20 rules, approval semantics).
- Tests: `tests/test_flow_skill_documents.py` (10 behavioral subprocess tests against real committed PDF fixtures, real pymupdf4llm, no mocks) + `documents` registered in the bundled-library gate.

## [0.8.338]

### Refactor: drop eval `trace_files` cross-file pointer

- Removed `CaseResult.trace_files` (no programmatic reader since the markdown REPORT was deleted in v0.8.336) and the now-orphaned `EvalRun.outputs_dir` property; stripped all 33 `trace_files=` construction lines across the 11 eval scripts.
- Retires the `.parent.name`-style cross-file-pointer surface (the bug class fixed in v0.8.336) — a case's turns are located by the sibling filename `<scenario>-<ts>-case_<id>.jsonl`, no stored pointer.
- Eval logging logic is otherwise unchanged: three independent append-incremental writers (`run.jsonl` / `case_<id>.jsonl` / `spans.jsonl`); `record_turn` still writes per-turn in its `finally`. `case_trace_path` retained.

## [0.8.336]

### Refactor: drop eval markdown REPORTs; flat `_outputs/` layout; drift reads JSONL

- **Single source of truth for eval run records**: deleted `evals/_report.py` and all 13 `docs/REPORT-eval-*.md` markdown reports. Per-run JSONL under `evals/_outputs/` is now the only record; removed the `prior_run_dir`/`load_prior_cases` dead helpers from `_observability.py`.
- **Flat `_outputs/` layout**: per-run folder → flat prefixed files `<scenario>-<ts>-{run,case_<id>,spans}.jsonl`. `EvalRun` drops `dir`, gains `stem` + `run_jsonl_path`/`case_trace_path`/`spans_path`/`outputs_dir`; `setup_perf_spans` takes the spans path directly. Old folder-layout dirs are abandoned (zero-backward-compat; no migration code).
- **`_drift.py` reads structured JSONL**: rewritten to glob `<scenario>-<ts>-run.jsonl`, skip skipped cases, uppercase the lowercase `Verdict` StrEnum, and extract `judge.score` from `reason` — replacing fragile markdown regex parsing. Now covers all `_outputs/` scenarios, not only those that had a REPORT file.
- **Doc sync**: `docs/specs/uat_evals.md` (diagram, lifecycle, config + Files tables, coverage-gaps drift row) and source docstrings updated to the flat-JSONL model; `CLAUDE.md` permanence policy clarified.
- **Tests**: trimmed `tests/test_eval_perf.py` to behavioral-only (removed one low-value happy-path verdict test).
- Bundled: in-progress exec-plan edits for the pending vision-input / skill-documents / scanned-pdf-vision features.

## [0.8.335]

### Fix: eval agent file-writes escaped to the repo root

- **Eval workspace isolation**: `eval_deps`/`make_eval_deps` built `CoDeps` without a `workspace_path`, so `create_deps` fell back to `Path.cwd()` (the repo root) as the write boundary. A relative `file_write` by the agent under test (e.g. `multistep_plan` W11.C creating a decision doc) therefore landed in — and was permitted within — the repo working tree (`enforce_write_boundary` saw the repo root *as* the boundary). New `apply_eval_workspace(deps)` in `evals/_settings.py` re-anchors `workspace_dir` and `file_search_roots` to a dedicated `EVAL_WORKSPACE_DIR` (`USER_DIR / "eval-workspace"`); applied centrally in `_deps` for every eval (safety invariant, not opt-in). Real, stable dir — no temp, no cleanup.
- Bundled: in-progress exec-plan refinements for the pending `skill-documents`/`skill-office` features (pymupdf dependency rationale).

## [0.8.333]

### Reranker input truncation — bound cross-encoder latency

- **`rerank_text_char_budget` (default 512)**: each candidate's text sent to the TEI cross-encoder is now truncated to this many chars (title prepended, never clipped). The cross-encoder runs one forward pass per `(query, candidate)` pair, so latency scales with `candidate_count × tokens_per_candidate` — an untruncated batch of ~50 large chunks cost ~14s. Config-plumbed (`CO_MEMORY_RERANK_TEXT_CHAR_BUDGET`) like its sibling `tei_rerank_batch_size`; applied in `_fetch_reranker_texts`.
- **Calibration**: root-caused the `multistep_plan` W11.C stall to rerank payload size (not cold-start/idle/concurrency/co-residency — all falsified). 512 is Pareto-optimal: 2.63s vs 14.5s untruncated, with top-1 100% / top-5 97–98% fidelity to the full reranker; it is a no-op for the 54% of chunks already ≤512 chars. Full investigation in `docs/REPORT-rerank-latency-calibration.md`. End-to-end re-run: W11.C `index.search` fell from ~14s to ~1.3–2.4s (case scored 10/10).

### Eval suite expansion (build-time)

- New eval scenarios + harness: `agentic_loop`, `approval_discipline`, `bounded_autonomy`, `groundedness`, `multistep_plan`, `user_model`; shared `_perf`/`_drift` instrumentation, rubrics, fixtures, and the phase2 migration (`test_flow_phase2_migrated`, `test_eval_perf`). Per-eval markdown REPORTs under `docs/`.
- Retrieval constant rename: `RERANKER_CANDIDATE_MULTIPLIER` → split into `FTS_CANDIDATE_MULTIPLIER` (4) + `VECTOR_CANDIDATE_MULTIPLIER` (16). Full suite green (654 passed).

## [0.8.332]

### Skill folder model — every skill is a `<name>/SKILL.md` directory

- **Folder layout**: each skill is now a directory `<name>/SKILL.md` (Anthropic convention) instead of a flat `<name>.md`, for both bundled (`co_cli/skills/`) and user (`~/.co-cli/skills/`) skills. Discovery globs `*/SKILL.md`; the skill name derives from the parent directory. This lets a skill carry a `scripts/`/`references/` payload that ships with the package — unblocking scripted skills like the pending `documents` PDF extractor. Zero backward compat: no flat-file fallback; the 6 bundled skills migrated via `git mv` (history preserved).
- **All consumers migrated**: loader, lifecycle discovery, user CRUD (`skill_create`/`edit`/`patch`/`delete`), usage sidecars (now `<name>/SKILL.usage.json` inside the folder — folder deletion is self-cleaning), `iter_records`, `/skills` commands, and the dream-daemon skill housekeeping (load + archive the whole folder).
- **Shell-bypass steering**: the `shell_exec` description and `06_skill_protocol.md` now direct the model to mutate skills only via the `skill_*` tools — never via a direct `shell`/`file_write` of `SKILL.md`, which would bypass the security scan, atomic write, catalog reload, and usage tracking.
- **Test hardening**: skill + chat-loop test files aligned to the testing policy — removed dead disk fixtures from the manifest tests, strengthened weak/structural assertions to exact values, and added sidecar-travels-with-folder + multi-sidecar `iter_records` coverage. Full suite green (654 passed).

## [0.8.331]

### Deferred-tool reveal — single source of truth (`revealed_tools`)

- **Stub generator now honors the reveal set**: `build_deferred_tool_awareness_prompt` gained a `revealed_tools: set[str]` parameter and skips any DEFERRED tool already revealed via `tool_view`. Previously the per-turn awareness block kept emitting a "load it via `tool_view`" stub for a tool whose full schema was already surfaced — a redundant, self-contradicting instruction that wasted floor budget and compounded as more tools were revealed in a session. Both consumers (the visibility filter and the stub generator) now read the one runtime set, so they agree by construction.
- **Field renamed `unlocked_tools` → `revealed_tools`**: the `CoRuntimeState` field is renamed to name its true membership (DEFERRED tools the model has revealed), with a full "unlock"→"reveal" terminology sweep across code, tests, and comments. Model-facing vocabulary is deliberately preserved: the `tool_view` docstring and `"Loaded …"` return keep the "load"/"view" action verb — internal *state* is `revealed_tools`, the model-facing *action* stays "load via `tool_view`".
- **No data-model or behavior regression**: reveal state stays in `runtime` (fork-fresh, survives compaction with no history coupling); no catalog mutation, no `ToolInfo` change. Specs (`tools.md`, `pydantic-ai-integration.md`, `compaction.md`) and reference docs synced. Full suite green (652 passed).

## [0.8.330]

### `ToolInfo` cleanup — remove write-only `is_read_only`, make `is_concurrent_safe` a required field

- **Removed `is_read_only`**: the field was set on ~20 native tools but never read at runtime — its entire effect (coercing `is_concurrent_safe=True`, blocking `is_approval_required=True`) was consumed at decoration time before the value was stored, leaving an inert "semantic tag." Dropped the `ToolInfo` field, the `@agent_tool(...)` decorator kwarg, the coercion, the import-time mutual-exclusion check, and all 20 call sites. Every affected tool already set `is_concurrent_safe=True` explicitly, so removing the coercion is a no-op.
- **`is_concurrent_safe` now required (no dataclass default)**: the decorator defaulted it to `True` while the `ToolInfo` dataclass defaulted it to `False` — a silent disagreement that any non-decorator construction (the MCP path, test fixtures) inherited wrongly. Removed the dataclass default and moved the field into the required block; the decorator keeps `True` as the sole native-author convenience, and the MCP synthesis path now states `is_concurrent_safe=False` explicitly (sequential until proven concurrent-safe).
- **Specs + reference synced**: `tools.md` `ToolInfo` field table (dropped the `is_read_only` row, corrected the `is_concurrent_safe` default cell and the `is_approval_required` cross-reference), `RESEARCH-tools-gaps-co-vs-hermes.md`. No behavior change; full suite green (648 passed).

## [0.8.328]

### `ToolInfo` field naming consistency — `approval` → `is_approval_required`, drop vestigial `requires_config`

- **`approval` → `is_approval_required`**: the bare-noun boolean now matches the `is_*` predicate form of its peers (`is_read_only`, `is_concurrent_safe`). Renamed the `ToolInfo` field, the `@agent_tool(...)` decorator kwarg, the import-time mutual-exclusion check, every `.approval` read (`_build_native_toolset` bridge, `co.tool.requires_approval` span, `capabilities`, `bootstrap/check`), and all 13 `@agent_tool(approval=True)` call sites. `MCPToolsetEntry.approval` renamed in lockstep (it feeds the synthesized MCP `ToolInfo`). The bridge mapping stays legible: `add_function(requires_approval=info.is_approval_required)`.
- **Deleted vestigial `requires_config`**: no tool set it, so the `_config_requirement_met` gate always passed (Google tools self-gate per-turn via `check_fn`). Removed the field, the decorator kwarg, the gate function, and its two callers (`_build_native_toolset`, `build_task_agent`).
- **Dead-orphan cleanup**: deleting the gate made the `config` parameter of `build_native_toolset` / `_build_native_toolset` unused — dropped it (and the now-orphaned `Settings` / `AGENT_TOOL_ATTR` imports), updating the `bootstrap/core.py` caller and ~22 test call sites.
- **Specs synced**: `tools.md` (full `ToolInfo` field table + new clarification on how co uses the SDK's `FunctionToolset` vs `ToolDefinition`), `agents.md`, `pydantic-ai-integration.md`, `01-system.md`, `bootstrap.md`. No behavior change; full suite green.

## [0.8.327]

### Compaction production-logic fixes (convergence guard + floor-aware tail + metric fixes)

- **No-progress convergence guard (ISSUE-2)**: after an applied proactive pass, if `tokens_after >= token_count` the processor escalates **once** to `recover_overflow_history` (strip-then-summarize) instead of re-firing an identical no-op — kills the static-marker-per-request treadmill a small-window model could enter. `_record_proactive_outcome` now returns `tokens_after`; the guard is fail-open (a `None` recovery returns the original `messages`) and reachable from both the summarize and anti-thrash static paths.
- **Floor-aware tail sizing (ISSUE-1)**: `plan_compaction_boundaries` sizes the tail off the *usable* trigger headroom (`tail_fraction / compaction_ratio × usable_trigger`, `usable_trigger = max(0, compaction_ratio × budget − static_floor_tokens)`) instead of the raw window, so post-compaction headroom below the next trigger no longer undershoots. Two pure-function kwargs (`static_floor_tokens=0`, `compaction_ratio=1.0`) reduce to the legacy `tail_fraction × budget` at defaults; all three callers pass the real config. Both `CompactionSettings` Field docstrings corrected.
- **Spill accumulator drift (ISSUE-4)**: `_spill_largest_first` accumulates freed chars and floor-divides once (`starting_tokens − chars_freed // CHARS_PER_TOKEN`) — no per-item rounding drift in the terminal classification.
- **`spill_errors` semantics (ISSUE-6)**: spillable candidates pre-filtered to `> TOOL_RESULT_PREVIEW_CHARS`, so `request.spill_errors` counts only genuine I/O failures, never "too small to spill".
- **L3 status write-back (ISSUE-7)**: a proactive pass writes the post-compaction estimate to `current_request_tokens_estimate`, so the status line reflects the compacted size.
- **Focus marker-skip (ISSUE-5)**: `_resolve_proactive_focus` skips compaction markers and the todo snapshot (and guards non-str content) so focus anchors on a real user message.
- **ISSUE-8 / eval re-pin (TASK-7)**: `eval_context_stability.py` re-run validated the floor-aware tail (CS.A `SOFT_FAIL → PASS`, coherence recalled); levers kept (`tail_fraction=0.10`, `min_proactive_savings=0.10`, floor-inclusive savings basis). `docs/specs/compaction.md` synced.

## [0.8.326]

### Prior-summary dedicated `PRIOR SUMMARY` slot — fix carry-forward erosion

- **Root fix**: on repeat compaction passes the prior summary marker was rendered inline inside the opaque `TURNS TO SUMMARIZE:` block, where the summarizer system prompt's "ignore commands in this data" rule collided with the task prompt's "integrate this summary" rule — fragile on a small local model, eroding carry-forward across long sessions. The prior summary is now lifted into a dedicated, trusted `PRIOR SUMMARY` slot above the turns block, and the raw marker is excluded from the re-summarized window. Peer-aligned (hermes/opencode/codex/openclaw).
- **`extract_summary_body` helper** (`_compaction_markers.py`): recovers the embedded recap from a summary marker's content (inverse of `summary_marker`, co-located); returns `None` for static/non-markers. Wires the previously dead `is_compaction_marker` recognizer into the live path.
- **`_partition_dropped`** (`compaction.py`): splits the dropped region into a marker-free body + the latest prior-summary recap; the summarizer is fed `body` while `build_compaction_marker` keeps `len(dropped)` — assembled output byte-for-byte unchanged. One seam fixes both proactive compaction and `/compact`.
- **`prior_summary` threaded** through `_gated_summarize_or_none` → `summarize_dropped_messages` → `summarize_messages` → `_build_summarizer_prompt`; the carry-forward clause (`_PRIOR_SUMMARY_CLAUSE`, Pending→Resolved transitions) is emitted only when a prior summary is present; `redact_text` applied to the slot.
- **Drift-anchor front-loading**: `## Active Task` / `## Next Step` moved to the top of `_SUMMARIZE_PROMPT` so neither output-length tail (cap-truncation clips the end; stub-collapse writes only the front) can drop the load-bearing sections — makes the carry-forward length variance robust by construction.
- **Tests/eval/spec**: deterministic partition+slot unit test (no LLM); `eval_context_stability` CS.B canary shifted to the front-loaded load-bearing sections + slot logging across ≥2 passes; `docs/specs/compaction.md` updated.

## [0.8.324]

### pydantic-ai SDK decouple — drop private-module dependency + topology heuristic

- **`schema_budget.py` de-coupled from SDK internals**: deleted `_unwrap_function_toolset` (a deep duck-typing walk over the SDK's toolset-composition topology) and the private `pydantic_ai._run_context` / non-canonical `pydantic_ai.result` imports. `measure_always_schema_budget` now takes the inner `FunctionToolset` as a parameter, fed from the `native_toolset` already in scope at `bootstrap/core.py`. Measured schema-budget number is unchanged (17,224), pinned by the existing regression guard.
- **Streaming-path JSON-repair regression test**: new tests in `test_flow_tool_call_repair.py` drive a malformed-JSON `ToolCallPart.args` through `SurrogateRecoveryModel.request_stream(repair_tool_args=True/False)` — pins the `_RepairingStreamedResponse` assumption so a future SDK change that bypasses it turns the test red instead of silently breaking Ollama streaming sessions.
- **Cosmetic cleanups**: `build_orchestrator` annotated `-> SessionAgent` (existing alias, imported under `TYPE_CHECKING`); `orchestrate.py` `UsageLimits(request_limit=None)` kept with an explanatory comment (orchestrator intentionally unbounded; human drives turn count).
- **New spec**: `docs/specs/pydantic-ai-integration.md` documents the SDK consumption surface (model wrapping, toolset composition, call-seam, approval protocol, schema budget) as the living source of truth; linked from `01-system.md`.

## [0.8.323]

### Config surface cleanup — dead settings removed, dream consolidation threshold fix

- **Dead settings removed**: `memory.max_item_count` and `memory.recall_half_life_days` (plus their env-var mappings `CO_MEMORY_MAX_ITEM_COUNT`/`CO_MEMORY_RECALL_HALF_LIFE_DAYS` and the `DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS` const) had zero consumers — no corpus-size cap or time-decay recall scoring exists. Operators setting these env vars would have seen no effect; both are now hard-removed under the zero-backward-compat rule.
- **Dream consolidation threshold fix**: `_write_consolidated_item` now passes `deps.config.memory.consolidation_similarity_threshold` to `save_memory_item`. Previously the save-time Jaccard re-dedup fell back to the hardcoded `0.75` default regardless of the configured value; the cluster-decision path already honored config, making the two-step inconsistent.
- **`settings.reference.json` updated**: `recall_half_life_days` entry removed; reference config round-trips cleanly under `MemorySettings(extra="forbid")`.
- **Specs synced**: `docs/specs/config.md` and `docs/specs/memory.md` rows for both dead settings removed; orphan duplicate `Memory` subsection in config.md cleaned up.

## [0.8.321]

### Spec sync — stale paths, config defaults, phantom settings

Doc-only corrections across `docs/specs/` to match current source (no code change):

- **Stale source paths** (memory/index/session/skills restructure): `memory/session.py`→`session/filename.py` (bootstrap.md, core-loop.md), `memory/memory_store.py`→`memory/store.py` (bootstrap.md), `memory/indexer.py`→`session/transcript.py` (bootstrap.md), `memory/memory_store.py:sync_dir(no_chunk=True)`→`bootstrap/core.py:_sync_canon_dir` (personality.md), `skills/_lint.py`→`skills/lint.py` (skills.md).
- **Wrong config defaults**: `compaction.tail_fraction` `0.20`→`0.10` (config.md, compaction.md + worked example); `llm.host` `11434`→`11433` (config.md, bootstrap.md, 01-system.md); `llm.model` `qwen3.5:35b-a3b-q4_k_m-agentic`→`qwen3.6:35b-a3b-agentic` (01-system.md, uat_evals.md); `skills.review_enabled` default `(existing)`→`false` (dream.md); default model `Qwen3.5`→`Qwen3.6` (compaction.md).
- **Phantom config removed**: `ctx_warn_threshold`/`ctx_overflow_threshold` (never existed) dropped from core-loop.md; flow prose rewritten to the real mechanism (hardcoded `ratio≥1.0` overflow, `ratio≥compaction_ratio` warn gated by `proactive_thrash_window`).

## [0.8.320]

### Toolset symbol renames — `_CallSeamToolset` + `tool_index` → `tool_catalog`

- **`_RoutingToolset` → `_CallSeamToolset`** (`co_cli/agent/toolset.py`) — the per-call `WrapperToolset` does
  not route (native-vs-MCP dispatch is `CombinedToolset`'s job one layer in); it is the single seam hosting
  the `tool {name}` span, the per-model-request tool-call cap, and MCP-result spill. The new name matches the
  established "seam" vocabulary in the docstring and `docs/specs/tools.md`. Hard rename, no alias; the builder
  `assemble_routing_toolset` is kept (it genuinely assembles the routing surface). Updated all `co_cli/` and
  `tests/` references and synced specs (`tools.md`, `compaction.md`, `core-loop.md`, `observability.md`,
  `agents.md`).
- **`tool_index` → `tool_catalog`** — repo-wide rename of the native+MCP `dict[str, ToolInfo]` carried on
  `deps` and threaded through bootstrap, MCP discovery, and the call seam, aligning the symbol with spec
  vocabulary. Includes incidental in-flight coworker edits across `commands/`, `bootstrap/`, and `context/`.

## [0.8.319]

### Deferred-tool stub completeness + instruction-floor-audit report consolidation

- **Deferred-tool stubs** — fixed three Google tool docstrings (`google_calendar_list`, `google_drive_search`,
  `google_gmail_draft`) whose first physical line wrapped mid-clause. The per-turn deferred-tool stub
  (`build_deferred_tool_awareness_prompt`, which renders only the first docstring line via
  `agent_tool.py`'s `description` extraction) now leads with a complete ≤100-char purpose statement. All 17
  native DEFERRED tools verified complete against `TOOL_REGISTRY`.
- **Spec/report consolidation** — distilled the one durable rule from `docs/REPORT-instruction-floor-audit.md`
  (the signature-coherence invariant: the floor carries WHEN/WHY, a tool's call signature/HOW lives in its
  schema; a deferred signature must never sit in floor prose) into `docs/specs/prompt-assembly.md` §2.2, then
  removed the standalone report. Build-time findings/proposals/measurements remain preserved in the archived
  plan; the archived plan's provenance line was repointed to the spec.

## [0.8.318]

### instruction-floor-audit — dedup, deferred-signature decouple, coupling guard, full-floor accounting

Gives the instruction half of the fixed prefill floor (soul seed + mindsets + rules + toolset guidance +
critique) the first-principles audit the schema half got in A1/A2 (P1–P4 from
`docs/REPORT-instruction-floor-audit.md`).

- **Single-owner dedup (P1/F1–F4)** — collapsed duplicated rules to one floor owner each: the memory
  save-policy + safety constraints migrated `02_safety.md` → `07_memory_protocol.md`; `MEMORY_GUIDANCE`
  deleted (its recall content folded into `07`); the "is a loop" anti-loop principle stated once.
- **Deferred-tool signature decouple (P2/F5)** — stripped the literal call signatures of the deferred tools
  (`session_search`, `session_view`, `skill_patch`, `skill_edit`, **`skill_create`**) from the floor while
  keeping the behavioral triggers. The floor now carries WHEN/WHY; the loaded schema carries HOW. Resolves
  the contradiction with `04_tool_protocol.md`'s deferred-load mechanic.
- **Coupling guard (P3)** — new `tests/test_instruction_floor_coupling.py` derives the deferred set live and
  asserts no deferred tool's call signature rides the assembled floor. No hardcoded allowlist; any future
  defer is auto-covered. Caught a live `skill_create` leak on first run.
- **Full-floor accounting (P4/F6)** — `deps.static_floor_tokens` and `test_instruction_budget.py` now measure
  the full delivered floor (base + toolset guidance + critique), not the base alone. Closes the ~144-tok
  compaction-trigger under-count; `INSTRUCTION_BLOCK_CEILING` re-pinned 23,600 → 24,200 (surface-definition
  expansion). Stale `static_floor_tokens` composition descriptions in `compaction.md` corrected.

## [0.8.316]

### context-stability TASK B — coherence gate + compaction production-logic audit

Completes the context-stability-sizing-control plan. Closes the coherence half of the loop-stability
guarantee (the eval previously validated boundedness only) and captures the audit's durable knowledge.

- **`evals/eval_context_stability.py` rewritten** — adds a **coherence gate**: a distinctive fact is planted
  pre-first-compaction (so it lands in the compactable middle), the bounded-loop pressure turns run, then the
  agent is asked to restate it after ≥1 compaction pass. A bounded-but-incoherent run grades SOFT_FAIL;
  boundedness/overflow stay HARD. House style reconciled (`run.append` centralized in `main()`); proven
  span-readers preserved. Real-LLM run: **coherence passes at `tail_fraction 0.10`** (fact recalled after 7
  compaction passes) — `tail_fraction` pinned 0.10, no revert. CS.C remains a documented eval-scaffold SKIP.
- **Spec knowledge-baking** — `docs/specs/tools.md` gains a "Tool-schema prefill floor" section (the
  ALWAYS-vs-DEFERRED budget criterion + guard, from the A1/A2 audit); `docs/specs/compaction.md`
  cross-references it from the `static_floor_tokens` definition.
- **`docs/ISSUE-compaction-production-logic.md`** (new) — 7 compaction production-logic findings surfaced by
  the adversarial trace done during the rewrite, all verified cold with concrete fixes (led by no-convergence
  guard + prompt-only marker preservation). Reported for a separate fix plan; none fixed here.

## [0.8.314]

### defer-recall-and-skill-edit-tools — shrink the ALWAYS tool-schema floor (context-stability A2)

The fixed prefill floor (static instructions + ALWAYS tool schemas) rides on every request — uncompactable, and a direct prefill-latency tax on a small local model's fixed 64k window. TASK A2 of the context-stability plan defers four episodic, low-per-turn-frequency tools so their schemas leave the per-turn floor, loaded on demand via the `tool_view` loader.

- **Four tools flipped `ALWAYS → DEFERRED`** — `session_search`, `session_view` (cross-session recall), and `skill_edit`/`skill_patch` (unifying all four skill-write tools as DEFERRED alongside `skill_create`/`skill_delete`).
- **ALWAYS schema bucket: 20,581 → 17,224 chars** (19 ALWAYS tools; `tool_count` unchanged at 36 — deferral hides, does not drop). `deps.static_floor_tokens` auto-updates at bootstrap from `measure_always_schema_budget`, so the runtime compaction trigger floor shrinks with it.
- **Schema-budget guard re-pinned** — `ALWAYS_BUCKET_CEILING` 21,000 → 17,700.
- **Doc-sync** — `docs/specs/skills.md` (all four skill-write tools DEFERRED) and `docs/specs/tools.md` (19·17 counts, expanded DEFERRED enumeration, per-row visibility). Fixed two pre-existing `.agent_docs/tools.md` inaccuracies (stale registration path; DEFERRED described as SDK `search_tools` rather than co's `tool_view`).
- **Verified** — deferred round-trip fires live (eval_memory W3.C loads `session_search` via `tool_view`; eval_skills W4.E discovery 3/3); no dream-daemon regression (task-agent toolsets pass tools by name, bypassing the visibility filter).

## [0.8.312]

### drop-capability-api — remove pydantic-ai capability SDK coupling

co attached two pydantic-ai capabilities (`ObservabilityCapability`, `CoToolLifecycle`) to every agent — a general loop-spanning middleware justified by only one of six behaviors, carrying a comment-only LIFO ordering invariant with silent `_NoOpSpan` failure on reorder, and making co the lone peer using a capability-style abstraction. All capability-API coupling is removed; the behaviors are reimplemented with explicit mechanics on seams co already owns.

- **Routing `WrapperToolset`** (`agent/toolset.py`) — a single `call_tool` seam hosting the `tool` span + `co.tool.*` attributes, the per-turn tool-call cap, and MCP oversized-result spill, as straight-line ordered code (no LIFO puzzle, no global-span bridge).
- **Cap ported with per-request granularity** — immediate increment at the `(cap+1)`-th call of a run_step, delayed reset on run_step change, plus a one-line segment-boundary finalize in `orchestrate.py`; the `orchestrate.py` hard-stop consumer is preserved bit-for-bit.
- **Model/chat span** moved into `SurrogateRecoveryModel` on both the streaming (`request_stream`) and non-stream (`request`) paths; streamed-turn token attributes asserted non-zero.
- **JSON arg repair** relocated into `SurrogateRecoveryModel`, gated to the Ollama-backed model (Gemini unaffected); peer-aligned (hermes/openclaw repair, opencode does not).
- **Usage recording** moved to run-result boundaries, recorded once per run (orchestrator turn-end `finally`, `run_standalone`, unchanged `llm/call.py`) — no cumulative-`RunUsage` double-count, error/interrupted/cap-stopped turns still ledgered.
- **Path normalization** dropped — `enforce_write_boundary` already resolves relative→absolute; `PATH_NORMALIZATION_TOOLS` deleted. Tool-call dedup deleted (agentic loop tolerates duplicates).
- **Deleted** `observability/capability.py`, `tools/lifecycle.py`; `capabilities=[...]` removed from both agent builders; `serialize_messages`/`serialize_response` relocated to `observability/serialize.py`.
- **Fix (review):** `reset_for_turn()` now zeroes the sibling cap-state fields (`tool_call_limit_run_step`, `tool_calls_in_model_request`), closing a rare cross-turn run_step collision.
- Span-tree (`co tail`/`co trace`), token-ledger, and hard-stop parity verified live.

## [0.8.310]

### tool-view-load-by-name — name-addressed deferred-tool loader, fully SDK-decoupled

co stubs every DEFERRED tool by exact name every turn, so keyword search (`search_tools`) was redundant indirection — the model had to invent keywords for a tool it could already name. This replaces it with `tool_view(name)`, a single name-addressed loader consistent with the `memory_view`/`session_view`/`skill_view` family. The original plan coupled to pydantic-ai's capability layer; on review that was rejected in favor of a fully decoupled design — co owns deferral end to end and the SDK loader never engages.

- **`tool_view(name)`** — a normal `@agent_tool` (ALWAYS) in `tools/system/tool_view.py`: normalized-exact name match unlocks the tool; a near-miss returns `difflib` "did you mean" candidates (unlocking nothing — a hallucinated name never resolves to a wrong tool); no match is terminal ("do not retry").
- **Deferral is co-owned.** Tools no longer set SDK `defer_loading`; the per-turn `_tool_visibility_filter` (`agent/toolset.py`) hides a DEFERRED tool until its name is in `deps.runtime.unlocked_tools`, which `tool_view` populates. The auto-injected `ToolSearch`/`ToolSearchToolset` stays inert and is never imported. MCP dropped `DeferredLoadingToolset` — one loader for native and MCP.
- **Unlocks survive compaction for free.** Unlock state lives in runtime memory, not message history, so the `_preserve_deferred_tool_discoveries` compaction coupling was deleted.
- **Validated in the loop.** Live-model W4.E probe: 3/3 trials the model loaded DEFERRED `skill_create` via `tool_view` then called it (no `file_write` fallback).
- **Source.** `co_cli/tools/system/tool_view.py` (new), `agent/toolset.py`, `agent/core.py`, `agent/mcp.py`, `deps.py`, `context/compaction.py`, and the deferred-prompt / rules / guidance text.
- **Tests.** `tests/test_tool_view.py` (resolution ladder + visibility gate + compaction-independence); `tests/test_orchestrator_schema_budget.py` ceiling re-pinned for the new lean ALWAYS tool; `evals/eval_skills.py` W4.E updated to the `tool_view` flow.
- **Specs.** `prompt-assembly.md`, `core-loop.md`, `skills.md`, `tools.md`, `compaction.md`, `self-planning.md`.

## [0.8.308]

### read-view-emission-spill-cap — dedup read/view line caps into one pagination cap; verbatim session_view turns

The four read/view tools (`spill_threshold_chars=∞`) had each grown their own ad-hoc line-count caps — three different values (`500`/`2000`/`200`) doing the same job across two tools — plus `session_view`'s byte cap and a 200-char per-turn preview that defeated the tool's stated "exact turn content" purpose. This dedupes the line-count caps into one shared pagination constant and makes `session_view` return verbatim turns. The emission cap originally proposed for ISSUE-5 was traced to be the wrong fix (a low cap re-breaks first-sight visibility; a high one is inert) and dropped — read tools keep `∞` so they land inline, and the sibling L2 tail-protection (v0.8.307) keeps them visible.

- **One pagination cap.** New `READ_MAX_LINES = 500` in `tools/tool_io.py` replaces `_READ_DEFAULT_LIMIT_LINES` (500), `_READ_MAX_LINES` (2000), and `_SESSION_TURN_MAX_LINES` (200). `file_read` uses it for both the no-range default and the ranged ceiling; the continuation hint pages forward.
- **`session_view` returns verbatim.** Removed `_SESSION_TURN_MAX_BYTES` + the byte-accumulation loop and the 200-char preview; the structured-return field `content_preview` is renamed to `content` (breaking, zero-backward-compat) and holds the full turn. Bounded only by `READ_MAX_LINES` turn count + L2/HTTP-400 recovery, with no per-turn char clip.
- **Kept (peer-aligned).** `file_read`'s per-line clip `_READ_MAX_LINE_CHARS = 2000` (bounds a pathological single 400 KB line that pagination cannot), the `_READ_MAX_FILE_BYTES` full-file I/O guard, and `spill_threshold_chars=∞` on all four read/view decorators are unchanged.
- **Source.** `tools/tool_io.py`, `tools/files/read.py`, `tools/session/view.py`.
- **Tests.** `tests/test_flow_files_read.py` (pagination at `READ_MAX_LINES` for both read modes, normal read stays inline, >2000-char line clipped), `tests/test_flow_session_view.py` (turn past 200 chars returned verbatim; `content_preview`→`content`).
- **Specs.** `tools.md` (corrected stale file-path attribution: `session_search`/`session_view` live under `tools/session/`, not `tools/memory/`).

## [0.8.307]

### l2-spill-tail-protection — L2 force-spill now preserves the recent tail (model sees what it just read)

The L2 force-spill processor `spill_largest_tool_results` collected every string `ToolReturnPart` with no recency protection and spilled largest-first — so a freshly-read large document, the biggest and newest return, was the prime spill target and got stubbed to a `<persisted-output>` placeholder on the very request that was supposed to carry it back to the model. The model never saw the content it just asked for. L3 (`proactive_window_processor`) and overflow recovery already protect a recent tail via `plan_compaction_boundaries`; L2 ran first and pre-empted it. This makes L2 respect the same tail.

- **Tail exclusion.** `spill_largest_tool_results` computes `tail_start` from the **same** `plan_compaction_boundaries(messages, resolve_compaction_budget(deps), cfg.tail_fraction)` boundary L3 and overflow recovery use (budget = `model_max_ctx`, **not** `spill_threshold_tokens` — no drift), and excludes `ToolReturnPart`s at message index `>= tail_start` from the spillable set. When the planner returns `None` (fewer than 2 turn groups) every candidate stays spillable — pre-tail-protection behavior.
- **Invariant.** A tool result is visible to the model at least once — on the request immediately following its production — before it becomes spill-eligible. The freshest read lives in the last turn group, which `plan_compaction_boundaries` always retains (`_MIN_RETAINED_TURN_GROUPS=1`).
- **Unchanged.** The spill trigger (`spill_threshold_tokens`), `_spill_largest_first`, L3, and the HTTP-400 overflow path are untouched. `_collect_tool_return_candidates` now returns `(index, part)`; the OTEL event gains `request.tail_start` / `request.tail_protected_count`.
- **Source.** `context/history_processors.py`.
- **Tests.** `tests/test_flow_compaction_spill_largest_tool_results.py` — fresh read in last turn group survives while an aged read spills; protected tail alone over threshold defers without stubbing; single turn group → no protection (None fallback).
- **Specs.** `compaction.md` (§2.4 Scope corrected — the "no protected tail at this stage" claim was inverted; §1.2 L2 row, algorithm step 3, span attribute list, test-coverage map).

## [0.8.306]

### rename-enforce-request-size — L2 history processor renamed to `spill_largest_tool_results`

The L2 history processor was named `enforce_request_size` — an abstract metric (`request_size`) as object and a generic verb (`enforce`) that hid the mechanism, inherited from a retired per-batch hook (`_enforce_request_budget`). Its two siblings follow `<verb>_tool_results` (`dedup_tool_results`, `evict_old_tool_results`). Renamed to `spill_largest_tool_results` so the chain self-documents: object (tool_results), action (spill), selection (largest). Hard rename, no alias (zero-backward-compat).

- **Function + OTEL event.** `enforce_request_size` → `spill_largest_tool_results`; span event `tool_budget.enforce_request_size` → `tool_budget.spill_largest_tool_results`. The runtime field `current_request_tokens_estimate` is unaffected (not named after the function).
- **Source.** `context/history_processors.py` (def + docstring + `add_event`), `agent/orchestrator.py` (import + registration), `deps.py`, `tools/lifecycle.py`, `config/compaction.py`.
- **Tests.** `tests/test_flow_compaction_enforce_request_size.py` → `tests/test_flow_compaction_spill_largest_tool_results.py` (git mv); call-sites in `processor_chain` and `proactive` tests updated.
- **Evals + script.** `eval_context_stability.py` (event-name constant + helper `_read_spill_events`), `eval_trust_visibility.py`, `scripts/calibrate_spill_size.py` (the span-name SQL filter moves with the event).
- **Specs.** `compaction.md` (diagram box re-padded), `core-loop.md`, `prompt-assembly.md`, `observability.md`.

## [0.8.304]

### token-usage-tracking-refactor — durable per-turn token ledger + `/usage` command

Provider-reported token usage was captured at the model-call boundary but immediately discarded (forwarded to ephemeral spans, dropped at turn end). There was no durable record of what a session — or the user across days — had spent. This adds a durable ledger fed by ground-truth `RunUsage` and a `/usage` reporting command.

- **Usage module (`co_cli/session/usage.py`).** New `UsageAccumulator` (turn-scoped token tally), `record_usage(deps, usage)` (best-effort bump), append-only ledger primitives `append_turn` / `aggregate` → origin-split `UsageWindow` (Session / Daemon / Total + distinct-session count). All I/O best-effort; never blocks a turn.
- **Fork-shared capture.** `CoDeps` gains `usage_accumulator` (shared by reference across `fork_deps`, like `file_tracker`) and `usage_log_path` (`USAGE_LOG = ~/.co-cli/usage.jsonl`). Captured at both model-call chokepoints — `ObservabilityCapability.after_model_request` (agent loop) and `llm_call` (direct calls) — so chat, subagent, and compaction-summarizer tokens all roll into the active turn.
- **Per-turn flush.** `_finalize_turn` appends one `origin="session"` ledger line then resets; the `/compact` transcript-replacement branch flushes its summarizer tokens (no mis-attribution to the next turn); turn-start reset in `run_turn`. Write-only observational accounting — never feeds compaction triggers or the status-line context-%.
- **Daemon spend.** The dream daemon (separate process) captures its own model spend via the same hooks and flushes `origin="daemon"`, `session_id=null` lines at each cycle boundary — counted toward windowed/total figures but never folded into any session.
- **`/usage` command.** No arg → current-session totals (daemon excluded); `week|month|total` → rolling-window Session / Daemon / Total split with a distinct-session count. Append-only, no TTL; cross-process appends atomic (`O_APPEND`, line < `PIPE_BUF`).

## [0.8.303]

### fix-dream-logging — dream daemon emits structured JSONL via a shared observability coordinator

The dream daemon attached a single plain-text `FileHandler` to the root logger writing to an unbounded per-run `logs/dream/<ts>.log`. Because `setup_spans_log` was never called in the daemon process, the spans logger kept `propagate=True` and span records leaked to root, where the plain-text formatter mangled them into non-JSON lines invisible to `co tail`/`co trace`. The daemon now routes through the same observability stack as the main app.

- **Shared coordinator (`co_cli/observability/setup.py`).** New `setup_observability(log_dir, *, app_log_name, spans_log_name, settings, errors_log_name=None)` wires the app log + separated span stream (`propagate=False`) + noisy-logger suppression in one place, so the daemon and main app can never re-diverge. Both `main.py:_setup_observability()` and `dream/process.py:_run_foreground()` call it.
- **Parameterized `setup_file_logging`.** Gains keyword-only `app_log_name="co-cli.jsonl"` and `errors_log_name="errors.jsonl" | None` (errors handler skipped when `None`); defaults preserve the main app exactly.
- **Dream daemon wiring.** Produces rotating JSONL `co-dream.jsonl` (INFO+, captures WARNING+ too — no separate errors file) and `co-dream-spans.jsonl` directly under `logs/`, with `co-*` filenames distinct from the main app's (`RotatingFileHandler` is not multi-process safe). `DREAM_LOG_DIR` and `_install_daemon_log_handler` removed. Note: `co tail`/`co trace` still read only `co-cli-spans.jsonl`; dream spans are `jq`-inspectable over `co-dream-spans.jsonl`.
- **Dedup bugfix.** Both handler dedup sites (`file_logging.py`, `tracing.py`) compared against `str(log_path)` while `RotatingFileHandler.baseFilename` is always the abspath — a relative `log_dir` defeated dedup and double-attached the handler. Now compared against `os.path.abspath(log_path)`.

## [0.8.302]

### drop-reported-realtime-trigger — compaction triggers key off a single realtime-local count, no provider-reported floor

Both compaction triggers compared against `max(local_estimate, last_reported_input_tokens)`, where `reported` is a provider-reported usage snapshot carried from the *previous* model response. A successful L2 spill lowers the realtime payload but cannot lower `reported`, so L3 would re-read the stale-high `reported` and fire an LLM summarization the spilled payload no longer required — papered over by a post-compaction overwrite of `reported`. The floor is now removed; co aligns with its chain-shaped peers (hermes/openclaw), which drive the same spill→summarize chain off a bare `chars/4` realtime count. The overflow backstop (HTTP 400/413 recovery) is unchanged and remains the safety net.

- **L2 spill trigger (TASK-1).** `enforce_request_size` drops `max(.., reported)` at both read sites (the entry trigger and the post-spill `effective_after`); the trigger is now `static_floor_tokens + estimate_message_tokens`. The `request.reported_tokens` span attr is removed.
- **L3 summarize trigger + commit (TASK-2).** `proactive_window_processor` drops the `reported` fetch and the `compaction_applied_this_turn → reported = 0` branch; `token_count = effective_request_tokens(...)`. A successful spill deterministically suppresses an unnecessary summarize. `commit_compaction` is reduced to a single `compaction_applied_this_turn = True` write — the post-compaction `reported` overwrite (and its sole-purpose estimate) are deleted.
- **Full field removal (TASK-3).** The lone non-trigger consumer (overflow telemetry) re-sources the provider's real input count straight from the `AgentRunResult` it already holds (`latest_result.response.usage.input_tokens` — the last `ModelResponse`, not accumulated `RunUsage`), so `last_reported_input_tokens`, the `TokenTrackingCapability` writer (both chat + task registrations), `token_tracking.py`, and the `/clear`/`/new` resets all go — no `chars/4` degradation in the warning.
- **Status-line context-% re-based (TASK-5).** The status-line "context used %" moves off the accumulator `turn_usage.input_tokens` (a response-snapshot-as-status-var that over-counted on multi-request turns) onto the realtime `current_request_tokens_estimate`; `turn_usage` + `_merge_segment_usage` are removed. `latest_usage` (spans/`TurnResult`) is untouched.
- **Tests + docs.** Trigger tests carry an authentic stale-high provider signal as a prior `ModelResponse(usage.input_tokens=20_000)` and prove both triggers ignore it; a chain test runs L2 spill → L3 fast-path with **zero summarizer LLM calls**; overflow warning verified off the provider count. `compaction.md` / `core-loop.md` reconciled; parent context-stability plan's ISSUE-3 reduced to a pointer (root cause was `reported` in the trigger `max()`, not the spill/summarize band).

## [0.8.300]

### summary-output-length-control — bound the compaction summary proportionally to what it compresses

The summarizer's output was capped only by the flat noreason ceiling (8192 on Ollama), independent of input size, with no length target in the prompt — so a small dropped region could be replaced by a near-ceiling summary (near-zero net savings) or a rambling one could be hard-truncated mid-structure, silently dropping the trailing `## Next Step` / `## Critical Context` sections. The summary is now bounded proportionally to the region it compresses.

- **Lockstep cap helper (TASK-1).** New `cap_output_tokens(settings, max_tokens)` in `config/llm.py` centralizes the Ollama lockstep rule (scalar `max_tokens` + `extra_body["max_tokens"]` move together; Ollama honors only the root value via `extra_body`, not OpenAI's `max_completion_tokens`). Gemini gets the scalar only; pure, non-mutating.
- **Proportional budget + prompt target (TASK-2).** `resolve_summary_budget(messages)` = `clamp(0.25 × estimate_tokens, 2000, 6000)` drives two levers: a `Target ~N tokens` line in the prompt (replaces the bare "Be concise") and a hard `max_tokens = min(ceil(budget × 1.3), noreason_ceiling)` override on the summarizer `llm_call` (worst case 7800 < 8192, so the cap never raises the ceiling). FLOOR=2000 keeps the worst-case cap (2600) clear of the template scaffold + mandatory verbatim quotes, so a small region never truncates mid-structure. Budget/cap/focus surfaced on the parent compaction span for trace-driven tuning. memory-merge and judge calls keep the unmodified noreason settings — only the summarizer is capped.
- **Lockstep dedup (review follow-up).** `orchestrate.py`'s length-retry path (`_length_retry_settings`) now reuses `cap_output_tokens` instead of a third inline copy of the lockstep rule.
- **Tests + eval.** Deterministic functional tests for the clamp branches, the lockstep helper (real config-derived settings), and the prompt target line; `eval_context_stability.py` gains case CS.B — re-reads the existing run's spans (no extra LLM cost) to assert every summarizer `output_tokens ≤ cap` end-to-end, trailing `## Next Step` survives, and the FLOOR + focus worst cases are exercised, logging the `budget/cap/output_tokens` tuning triple.
- **Docs synced** — `compaction.md §2.6` gains a "Summary output budget" subsection with the as-shipped constants.

## [0.8.298]

### session-search-ripgrep — session recall moves from the hybrid index to file-based ripgrep

Session-transcript search leaves the shared hybrid `IndexStore` (FTS5 + sqlite-vec) for file-based ripgrep over `~/.co-cli/sessions/*.jsonl`. Curated memory + canon stay on the hybrid index, untouched. This applies co's curated-vs-uncurated search dichotomy (uncurated transcripts → lexical, no index) and removes the highest-volume, lowest-value-density corpus from the embedding pipeline.

- **File-based search module (TASK-1).** New `co_cli/session/_search.py`: ripgrep (`--fixed-strings --ignore-case --no-config --no-ignore --hidden`, Python line-scan fallback when `rg` is absent) over the raw JSONL, mapped back through `extract_messages` to a readable, line-cited snippet. Returns `SessionHit` with `path`=uuid8 and 1-indexed `start_line==end_line`, ranked `(match_count desc, recency desc)`. Structural-JSON-key matches are dropped (no readable content to cite). `SessionStore` rewritten file-based: `search()` delegates, `count()` globs `*.jsonl`; no `IndexStore`, `index_session`, `sync`, or chunker.
- **Indexing wiring removed (TASK-2).** Deleted `chunker.py`, `init_session_index` + its boot call, the orphaned `session_chunk_*` config fields + env map, and the dead eval `index_session` seed. `SessionStore` is now constructed unconditionally (no index dependency).
- **Tool-call args are searchable (review follow-up).** `extract_messages` now renders a tool-call's decoded arguments into its content, so a term that occurred only as an agent-synthesized tool input (a saved memory, file path, command) is recalled with a citation and shown in `session_view` — not just the tool name. Closes a content-surface gap inherited from the prior indexed design.
- **Tool contract unchanged.** `session_search` / `session_view` keep their names, args, and result shape; the agent-facing surface and `recall.py` logic are untouched.
- **Docs synced** across `sessions.md`, `memory.md`, `01-system.md`, `bootstrap.md`, `config.md` off the file-based backend.

## [0.8.296]

### antithrash-static-marker-fallback — anti-thrash gate degrades to a static marker, never a no-op (ISSUE-2)

The proactive anti-thrash gate was a compaction kill-switch: after `proactive_thrash_window` consecutive low-yield summary passes it returned the conversation unchanged, so text/reasoning context (uncapped by the tool-return evict path) grew toward the 64k hard limit until the model errored. This demotes the gate from a *trim-or-not* switch to a *summary-vs-static-marker* choice — it never stops trimming.

- **Static-marker fallback (TASK-1/2).** New keyword-only `compact_messages(..., summarize=True)`; when `False` the gated summarizer is skipped entirely and the dropped region is replaced by the existing `static_marker` (no LLM call), reusing the marker/`todo_snapshot`/deferred-tool/tail assembly unchanged. `proactive_window_processor`'s anti-thrash branch now sets `summarize=False` and falls through the shared `plan_compaction_boundaries → compact_messages → _record_proactive_outcome` tail instead of `return messages`. "Whether to compact at all" is now owned solely by the threshold check and the boundary-`None` guard.
- **Truthful status (CD-M-1).** A `summary_skipped` flag threads into `_record_proactive_outcome` so the deliberate-skip path reports **"Compacted (static marker)."** instead of the misleading "Summarizer failed — used static marker." Circuit-breaker state (`compaction_skip_count`) is untouched on this path.
- **Loop-stability eval (TASK-4).** New `evals/eval_context_stability.py` — real-LLM UAT driving sustained text/reasoning pressure at the real 64k window; asserts the proactive loop stays bounded (no overflow, every fired pass reduces tokens, post-pass below trigger). Anti-thrash trip is gate-conditional (non-engagement logged); the deterministic tripped-state guarantee lives in the unit test.
- **Centralized eval settings.** New `evals/_settings.py` — `EVAL_MAX_CTX` sourced from `load_config().llm.max_ctx` with an `eval_max_ctx(override)` lever, so evals share one system-sourced settings surface and never coin a window inline.
- **Specs synced.** `docs/specs/compaction.md` updated from the old banner/no-op model to static-marker demotion (`summarize=False`).

## [0.8.294]

### context-stability — floor-aware compaction trigger + proportional tail (ISSUE-1/1.5)

Partial delivery of the context-stability-sizing-control plan. The L2/L3 compaction triggers undercounted live input within-turn, and the preserved tail was sized against the full window rather than the operational budget — together diluting the compressible middle and letting long sessions drift toward overflow.

- **Floor-aware trigger (ISSUE-1.5).** The trigger local estimate is now floor-inclusive via new `effective_request_tokens(deps, messages) = static_floor_tokens + estimate_message_tokens(messages)`. `deps.static_floor_tokens` is measured live at bootstrap (static instructions + ALWAYS-visibility tool schemas, ~10,788 tok) via new `co_cli/bootstrap/schema_budget.py`. Closes the within-turn undercount where a stale/zeroed/missing provider report left the floor-blind local as the sole signal — the trigger no longer fires up to one floor (~11k tok) late.
- **Savings basis fixed.** Post-compaction `savings` and the `commit_compaction` overwrite now use a floor-inclusive `tokens_after`, removing the overstatement that biased the anti-thrash low-yield detector.
- **Proportional tail (ISSUE-1).** `compaction.tail_fraction` default lowered `0.20 → 0.10` so the preserved tail is ~20% of the operational budget (was 40%). Combined with the floor-aware work, the compressible middle widens from ~23.5% to ~47% of the trigger.
- **Shared schema-budget helper.** `measure_always_schema_budget` factored out of `tests/test_orchestrator_schema_budget.py` so bootstrap and the regression guard read one source of truth; `estimate_text_tokens` added to `co_cli/context/tokens.py`.
- **Specs synced.** `docs/specs/compaction.md` and `docs/specs/core-loop.md` updated to the floor-aware trigger (the "always current by construction" claim is now scoped to cross-turn; within-turn is backstopped by the floor-inclusive local).

## [0.8.292]

### structural-logging-gap-fill — span coverage for non-agent processing paths

Three processing paths that bypass the agent loop emitted zero structural spans, so direct LLM calls, sanitize-retry recovery, and index retrieval were untrackable in `co tail` / `co trace`. The compaction summarizer — a hot-path direct LLM call — was the costliest blind spot (the 71.95s scare ran untraced).

- **`co_cli/llm/call.py`** — `llm_call()` wraps `model_request` in an `llm_call {model}` span (`kind="model"`) at attribute parity with the agent-path `chat` span (`co.model.name/input/output`, `co.model.tokens.input/output`, `co.model.finish_reason`). Distinct name keeps direct calls separable from agent turns; nests under any active parent. Covers the compaction summarizer, dream merges, and eval judge calls at once.
- **`co_cli/observability/capability.py`** — lifted `serialize_messages`/`serialize_response` to public (importable) surface, reused by the direct-call span. Distinct from `context/summarization.py`'s same-named human-readable serializer.
- **`co_cli/index/store.py`** — `IndexStore.search()` emits an `index.search` span per invocation (query_len, sources, kinds, limit, hits) so recall work is attributable under the `memory_search`/`session_search` tool span.
- **`co_cli/llm/surrogate_recovery_model.py`** — emits a `surrogate_recovery` event on the active model span when sanitize-retry fires (both `request` + `request_stream`).
- **`co_cli/context/compaction.py`** — `CompactionFallbackReason` enum + `compaction_fallback` event with a distinct reason per degradation branch (`model_absent`, `circuit_breaker_open`, `summarizer_error`, `empty_summary`), so a silent degradation to a static marker is visible in the trace.

## [0.8.290]

### filescope-command — read-only `/filescope` slash command

Adds a `/filescope` built-in that prints the active filesystem scope — the resolved `file_search_roots` (read scope) and the `workspace_dir` (write anchor) — so a misconfigured or empty `file_search_paths` is no longer invisible until searches quietly miss.

- **`co_cli/commands/filescope.py`** (new) — `_cmd_filescope` reads `deps.file_search_roots` / `deps.workspace_dir` and prints a numbered read-scope list plus the write anchor. A root failing `Path.exists()` is flagged `(missing)`; the unconfigured single-root case is labeled as default (workspace-only) scope. Read-only, returns `None` (`LocalOnly`); paths print with `soft_wrap=True` so long paths don't wrap.
- **`co_cli/commands/core.py`** — registers `BUILTIN_COMMANDS["filescope"]`, auto-wiring `/help` listing and tab-completion.
- **`docs/specs/tui.md`** — `/filescope` row added to the built-in slash-command table.

## [0.8.288]

### rules-block-trim-finish — complete the conservative rules-block trim + single-source pytest timeouts

Banked the full conservative rules-block trim (child 4 of prefill-trim) with zero adherence regression. The numbered behavioral-rules block rides every cold prefill and every post-compaction state, so duplicated guidance is a recurring context-budget tax.

- **`06_skill_protocol.md`** — manifest-scan cue collapsed from 3× to one canonical home (`## Discovery`); the two upstream echoes thinned to a lead-in; `## Background review` mechanism prose compressed to its behavioral cue. All load-bearing cues intact (`skill_view`/`skill_patch`/`skill_edit`/`skill_create`, the 3+-step create bar, create-on-behalf confirm, the distinct `## Create` "search first" dedup). ~3,194 → 2,710 chars.
- **`05_workflow.md` / `07_memory_protocol.md`** (carried from prefill-trim-4) — blocker-loop cue deduped to one home; `Triggers:` recall line and the 4-way `SaveResult.action` enum collapsed to the behavioral cue.
- **Instruction-budget guard re-pinned** (`tests/test_instruction_budget.py`): `build_static_instructions` re-measured post-trim = 23,352 chars; `INSTRUCTION_BLOCK_CEILING` 24,200 → 23,750 (tightened, never raised).
- **Adherence gate** held across all three trimmed files (skills + memory evals on the v0.8.286-fixed harness; no domain regressed).
- **Single-source pytest timeouts** (user-directed): the per-test pytest-timeout ceiling moved out of `pyproject.toml` into `tests/_timeouts.py` (`PYTEST_PER_TEST_TIMEOUT_SECS = 180`), applied via a new `tests/conftest.py` `pytest_configure` hook. Removed a redundant `@pytest.mark.timeout(180)` and expressed the one legitimate override relative to the constant — the only literal pytest-timeout number now lives in `_timeouts.py`.

## [0.8.286]

### eval-infra-output-sync — prune to workflow evals + read canonical `turn_result.output`

Eval suite pruned 9 → 6 (kept the labeled Workflow evals: daily_chat, session_continuity, memory, skills, background, trust_visibility; removed the 3 non-workflow evals `mindset_selection`/`domain_review`/`research_direct` + their dead REPORTs). Fixed the response-reading drift: evals reconstructed the agent's reply from message `TextPart`s, which read empty on qwen3.6's length-retry/thinking-budget turns and caused spurious FAILs (eval_skills W4.A was 2/4 flaky). New shared `response_text(turn_result)` accessor in `evals/_trace.py` reads the canonical `AgentRunResult.output`; routed skills/session_continuity/daily_chat through it and added a trace `assistant_text` fallback. W4.A now PASS 2/2.

## [0.8.284]

### defer-skill-write-tools — `skill_create`/`skill_delete` moved off the ALWAYS surface

Progressive-disclosure trim for the small-model tool surface. `skill_create` and `skill_delete` — deliberate, rarely-first-turn actions — move from `ALWAYS` to `DEFERRED`; the model discovers them on demand via `search_tools` and stays aware of them through the per-turn deferred-tool stub. `skill_view`/`skill_edit`/`skill_patch` stay `ALWAYS` so the immediate drift-fix path needs no discovery round-trip. ALWAYS surface 24 → 22 tools (`skill_*` hot-surface names 5 → 3); ALWAYS schema bucket 20,988 → 19,800 chars.

- **Two decorators flipped** (`co_cli/tools/system/skills.py`): `skill_create`, `skill_delete` → `VisibilityPolicyEnum.DEFERRED`; approval + subject-fn unchanged. Dream daemon unaffected (`build_task_agent` registers by explicit name, ignores visibility).
- **Schema-budget guard re-pinned** (`tests/test_orchestrator_schema_budget.py`): `ALWAYS_BUCKET_CEILING` 21,400 → 20,200 to lock the win.
- **Spec sync** (`docs/specs/skills.md`): Path 3 records the always-loaded vs deferred split.
- **End-to-end validated** with a live model: agent issues `search_tools` then calls the deferred `skill_create` successfully (both hinted and unprompted).
- **Test hygiene** (`tests/test_flow_deferred_tool_stubs.py`): trimmed structural string-shape tests to the one functional discovery-completeness guard.

## [0.8.282]

### prefill-trim-2 — tool-guidance de-duplication + cumulative schema-budget guard

Last child of the `prefill-trim` family. Removes tool-routing guidance duplicated between rule `04` and tool docstrings (one canonical home per cue), trims the routing/web/file docstrings, and locks the ALWAYS tool-schema bucket against regression. ALWAYS bucket 22,589 → 20,988 chars; rules block −371 tok. No routing regression (validated by `eval_mindset_selection`).

- **Rule↔docstring de-dup (`03`/`04`).** Dropped `04`'s "## File tools"/"## Shell" sections (verbatim duplicates of `shell_exec`'s docstring, the canonical home); kept the cross-tool absolute-paths rule as `## Paths`; relocated the stale-data web_search/web_fetch verification cue into `03`'s `## Verification`; dropped the "Track convergence" paragraph (canonical home is rule `05`, per child-4 coordination).
- **Docstring trim (`web_fetch`/`web_search`/`file_read`).** Dropped `Returns:` enumerations and model-derivable caveats; tightened `Args:` to noun-phrase + constraint. Load-bearing injunctions preserved verbatim: `web_fetch`'s fabricate-URLs rule (incl. "from tool output") and Shell-fallback cue; `file_read`'s file_search-first cue. No code/signature changes.
- **Cumulative schema-budget guard (NEW `tests/test_orchestrator_schema_budget.py`).** Builds real deps via `create_deps` (headless, `stack=None`), prepares every tool def, and pins the ALWAYS bucket ≤ 21,400 chars, per-tool ≤ 2,300, tool floor ≥ 27, non-empty descriptions — a regression lock for the whole prefill-trim family.

## [0.8.280]

### Google auth: least-privilege scopes + `co google auth` as sole acquisition path

Makes co's Google credential path best-practice. `co google auth` is now the only way co acquires a Workspace credential — gcloud/ADC legs are gone (gcloud's built-in OAuth client cannot grant Workspace user scopes).

- **Least-privilege scopes.** `ALL_GOOGLE_SCOPES` dropped from the restricted `gmail.modify` to `gmail.readonly` + `gmail.compose` + `drive.readonly` + `calendar.readonly` — the minimal floor for what the tools call. No mail modify/delete/send authority.
- **`co google auth`** — runs `InstalledAppFlow` with the user's own OAuth Desktop-app client (`google_client_secret_path`, default `~/env-secrets/google_client_secret.json`) and writes an authorized-user token to `GOOGLE_TOKEN_PATH` (chmod 0600). Default uses a local browser; `--no-browser` prints the consent URL and reads the pasted code for headless/SSH machines.
- **`co google check`** — verifies an existing token against the required scope set with a scope-validating refresh; prints a granted-vs-required diff and the actionable re-auth guidance on a shortfall. No command prints secrets.
- **Terminal scope/auth failure.** `handle_google_api_error` classifies a `google.auth` `RefreshError` as a terminal `tool_error` pointing at `co google auth` (was silently retried as the catch-all `ModelRetry`); transient 403/404/429/5xx still retry.
- **Per-turn visibility.** The seven Google tools dropped `requires_config` and self-gate per turn via `check_fn=_google_available` — visible only when a credential exists on disk (explicit `google_credentials_path` file or the default `GOOGLE_TOKEN_PATH`). The misleading ADC branch was removed from `co doctor` and the orphaned `ADC_PATH` constant deleted.

### Bundled: web_research removal + `web_fetch` content extraction

This release also lands the in-flight `drop-web-research-add-fetch-extraction` work (interleaved in shared files, shipped together): the in-turn `web_research` delegation tool and its `run_attempt`/`MAX_AGENT_DEPTH` machinery are removed; `web_fetch` gains `trafilatura`-based HTML content extraction.

## [0.8.279]

### Tool-surface small-model audit — Task 4 (non-Google) + Task 5 web steers

Closes out the cross-tool small-model surface audit for the non-Google tools. Docstring/wording only — no signature or behavior changes.

- **`web_research.max_requests`** — Args reworded so the magic `0` reads correctly ("Leave at 0 to use the configured default budget (10)") instead of the literal "0 = no requests".
- **`web_search.max_results`** — Args now states the real 1-8 silent clamp ("Values above 8 are silently clamped to 8"); the contradictory "Max 8 … capped regardless of max_results" Caveats bullet removed.
- **`web_fetch.format`** — Args corrected to the real two-outcome behavior: `markdown` converts HTML→markdown; `html`/`text` both return the raw decoded body unchanged; ignored for JSON/XML/plain-text. (The prior wording implied three distinct HTML renderings.)
- **`memory_view.name`** — Args disambiguated from `memory_create.name_title` by appending "; not the artifact title".
- **Sibling steers (`web_search` ↔ `web_research`)** — reciprocal when-to-use / when-NOT-to-use lines added to both docstrings (quick snippet lookup → `web_search`; multi-page read+synthesis → `web_research`).
- **Scope move** — all Google Workspace surface items (calendar/drive/gmail docstrings + gmail/calendar steers) moved to the dedicated `deferred-tool-stub-grouping` plan; `task_list.status_filter` dropped (already adequate). Audit plan archived.

## [0.8.278]

### Tool-surface small-model audit — Task 4 (work_dir name + contract unification)

Unifies the foreground/background working-directory surface under one name and one path contract, per the small-model monomorphic doctrine.

- **`work_dir` rename (house `_dir` convention).** `shell_exec.workdir` and `task_start.working_directory` both renamed to `work_dir` — the codebase convention is the `_dir` suffix (20+ identifiers: `workspace_dir`, `memory_dir`, `sessions_dir`, …); `working_directory`/`workdir` were the lone outliers. Distinct from `workspace_dir` (the project root): `work_dir` is an optional per-call subdirectory under it.
- **`task_start` contract conformed to `shell_exec`.** `task_start.work_dir` is now boundary-guarded via `enforce_write_boundary` (rejects absolute / `..`-traversal paths → `tool_error`) and defaults `None → workspace_dir` (was `Path.cwd()`, unchecked). Closes a real escape gap — a detached background command could previously run anywhere on disk. The `/background` REPL slash command anchor likewise moved `Path.cwd()` → `workspace_dir`, so every shell-launch path shares one cwd anchor.
- **`docs/specs/tools.md`** — working-directory section extended to document the shared `task_start`/`/background` contract; `file_patch` row notes whole-file delete is `shell_exec` (`rm`).
- **Tests** — added `task_start` `work_dir` scope + escape-rejection tests; renamed `workdir` kwargs/tests across `test_flow_shell_exec.py`.

### Shell exit-code classification

Benign non-zero shell exits (grep with no matches, diff finding differences) now come back as normal tool output instead of errors, so the model does not mistake a successful "found nothing" for a failure and retry-loop. Real errors (grep exit 2, command-not-found exit 127) stay classified as errors with an explanatory exit-meaning header. New `co_cli/tools/shell/_exit_codes.py` (`benign_exit_note`, `shell_exit_meaning`).

## [0.8.276]

### Tool-surface small-model audit — Task 3c (file_patch V4A removal)

Removes the V4A multi-file patch capability from `file_patch`, collapsing it to a monomorphic single-file find-and-replace tool. V4A is the OpenAI-Codex-native patch format; peers gate it to OpenAI models only (opencode `registry.ts:322-325`, openclaw `pi-tools.ts:266-292`), making it the wrong surface for co's small local models. No native-tool-count change (no new tool added); whole-file delete moves to `shell_exec` (`rm`), in-file deletion stays via `new_string=""`. Supersedes the Task-1 V4A Move-directive parser fix ([0.8.272]) — the `_v4a.py` module it patched is now deleted.

- **`co_cli/tools/files/_v4a.py`** — deleted (the V4A parser module; only consumers were `write.py` + the V4A tests).
- **`co_cli/tools/files/write.py`** — removed the `_v4a` imports, the `PatchMode`/`Literal` alias, and all V4A apply helpers (`_PendingWrite`, `_insert_addition_hunk`, `_compute_v4a_update`/`_add`/`_delete`, `_write_v4a_pending`, `_apply_v4a_patch`). `file_patch` is now `file_patch(path, old_string, new_string, replace_all=False, show_diff=False)` — all params unconditional, `path`/`old_string`/`new_string` required by signature (the three None-guards removed), defaults stated inline, with the `old_string`-uniqueness guidance and `new_string=""` delete idiom in the docstring. The `mode`-dispatch and `_file_patch_replace` indirection were inlined into the tool body.
- **`tests/test_flow_files_write.py`** — removed the three V4A tests; dropped the now-absent `mode=` kwarg from the replace tests; added `test_file_patch_deletes_matched_text_with_empty_new_string` for the `new_string=""` delete idiom.
- **`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md`, `RESEARCH-tools-peers-tiers.md`** — factual correction: the three co-cli comparison cells that asserted V4A support now read "removed — V4A is OpenAI-Codex format, gated to OpenAI models by opencode/openclaw"; peer-inventory rows left intact.

Integration touch-points (`agent/toolset.py`, `tools/display.py`, `tools/categories.py`, `tools/approvals.py`) unchanged — `file_patch` keeps its name and single `path` arg.

## [0.8.274]

### Prefill-trim child 3 — data/reflexive tool schema trim

Trims the ALWAYS-tool schema budget by cutting docstring (desc + params) bloat on the data and reflexive tools, and removes `skill_manage`'s non-functional hermes-parity stubs from the signature. Reference-not-routing content, near-zero behavioral risk; all load-bearing injunctions preserved.

- **`memory_manage`** — params `Args:` prose tightened (1,765 → 1,200 chars); `replace` "section must appear exactly once" injunction kept.
- **`skill_manage`** — dropped the four non-functional stubs (`write_file`/`remove_file` actions, `file_path`/`file_content` params) and the now-orphaned dispatch branch, `_skill_patch` `file_path` branch, and `_LINKED_FILE_ERROR` constant; params trimmed; action-routing description left intact (stays ALWAYS).
- **`clarify`, `todo_write`, `todo_read`, `memory_search`, `memory_view`, `file_search`** — desc/params trimmed; clarify one-call-only and todo_write single-`in_progress` injunctions preserved.
- **`docs/specs/dream.md`** — fixed the inline tool-write-reset table row that still named the removed `write_file`/`remove_file` actions.
- **Tests** — removed two tests pinning the deleted `_LINKED_FILE_ERROR` stub surface.

ALWAYS bucket at delivery: 25,612 → 21,941 chars (~−918 tok).

## [0.8.272]

### Tool-surface small-model audit — Task 1 (Pattern 5: dead params & broken references)

Removes dead/parity surface from `skill_view` and fixes the V4A patch parser's silent handling of unsupported directives. Surface-correctness cleanup for small models; no registry or native-tool-count change.

- **`co_cli/tools/system/skills.py`** — `skill_view`: dropped the dead `file_path` parameter (every non-None value errored) and the always-empty `linked_files={}` return stub; removed the inert `plugin:skill` prefix-strip (co has no plugin namespace) and inlined the `lookup` alias to `name`. Plugin-prefixed names now return a clean unknown-skill error instead of silently stripping.
- **`co_cli/tools/files/_v4a.py`** — `parse_v4a_patch` now rejects any unrecognized `*** Xxx File:` directive (e.g. `Move File`) with an explicit parse error, instead of silently absorbing it as a hunk context line.
- **`co_cli/tools/files/write.py`** — converted the unreachable `else: continue` (silent MOVE skip) in `_apply_v4a_patch` into an explicit error return.
- **`docs/specs/skills.md`** — synced the `skill_view` signature/prose; fixed three verification rows that pointed at the nonexistent `tests/test_flow_skills_tools.py` → `tests/test_flow_skills_manage.py`.
- **Tests** — added `test_file_patch_v4a_mode_rejects_unsupported_move_directive`; removed the now-dead `file_path` and `plugin`-qualified `skill_view` tests.

## [0.8.270]

### Deferred-tool awareness — auto-generated per-tool stubs

Replaces the hardcoded, category-level deferred-tool hint with per-tool stubs derived from `tool_index` — complete by construction, so no future DEFERRED tool can be silently omitted from the prompt. Re-tested the `skill_manage` DEFERRED flip on top of the stubs; the gated discovery eval failed (0/3 < 2/3), proving awareness was not the binding constraint (the `search_tools`→load→call loader UX is), so `skill_manage` stays ALWAYS. The awareness + regression-guard value ships regardless.

- **`co_cli/tools/deferred_prompt.py`** — body rewrite: iterate `tool_index`, select `visibility == DEFERRED`, emit one `` - `name`: <one-liner> `` line per tool under a `search_tools` directive. One-liner rule: first non-empty line of `description`, stripped, truncated to ≤100 chars (ellipsis in-budget); empty-description → name-only. Empty-set returns `""`. Drops `_NATIVE_TOOL_CATEGORIES` / `_REPS` / `_INTEGRATION_TOOL_CATEGORIES`. Renamed `tool_category_awareness_prompt` → `deferred_tool_awareness_prompt` (the category concept is gone).
- **`co_cli/agent/_instructions.py`, `co_cli/agent/orchestrator.py`** — rename + docstring; builder stays per-turn (post-static), preserving the v0.8.266 cached-prefix invariant.
- **`tests/test_flow_deferred_tool_stubs.py`** (new) — completeness, exclusion, one-liner cap, empty-description fallback, truncation, first-line, `search_tools` directive, and empty-set contract, built from a real native bootstrap `tool_index`.
- **`evals/eval_skills.py`, `evals/_deps.py`** — adds `case_w4_e_discovery` (N≥3 independent trials, self-skipping `SOFT_PASS` guard that auto-reactivates if `skill_manage` is ever re-flipped to DEFERRED); `EvalFrontend` prompt methods made async to match the awaited frontend protocol.
- **`tests/test_flow_turn_result_model_requests.py`** — fix a latent test-invariant bug: on the interrupted path `_build_interrupted_turn_result` deliberately trims the trailing tool-call response, so the request accumulator may exceed the trimmed history's `ModelResponse` count; the assertion now branches on `turn.interrupted` (`>=` when interrupted, `==` otherwise).
- **specs** — `prompt-assembly.md`, `tools.md`, `personality.md`, `bootstrap.md`, `01-system.md`: identifier rename + category→per-tool prose.

## [0.8.268]

### REPL bounded input queue — config-gated cap + drop policy (Phase 3)

Phases 1/2 made mid-turn submissions enqueue and manageable, but the queue was unbounded — a runaway paste or a wall of type-ahead could grow it without limit. Phase 3 bounds it behind config, default-off so a user who sets nothing sees zero behavior change.

- **`co_cli/config/repl.py`** (new) — `ReplSettings` (`queue_cap: int = 0` [`0` = unbounded], `drop_policy: Literal["oldest","newest"] = "oldest"`) + `REPL_ENV_MAP` (`CO_REPL_QUEUE_CAP`, `CO_REPL_DROP_POLICY`). Mirrors `DreamSettings`.
- **`co_cli/config/core.py`** — registers the `repl` group: `Field(default_factory=ReplSettings)` + `nested_env_map["repl"]`.
- **`co_cli/main.py`** — centralizes the mid-turn append in a single `_enqueue(runtime, text, deps, on_status)` helper: blank-drop first (a blank never counts against the cap), then cap check + drop policy (`"oldest"` pops the head then appends; `"newest"` rejects the incoming item, one notice either way), then exactly one status repaint. `queue_cap == 0` preserves Phase 1/2 behavior. `_build_accept_handler` gains a `deps` param.
- **`docs/specs/config.md`, `docs/specs/tui.md`** — document the `repl.*` group and the `_enqueue` blank→cap→drop behavior.

## [0.8.266]

### Prompt static-prefix stability — move skill manifest + tool-category awareness to per-turn

Removes ~345 tokens of `skill_index` / `tool_index`-dependent content from the static prompt prefix, making the cached prefix byte-identical across turns regardless of mid-session skill or tool changes. On Ollama, any prefix mutation forces full KV-cache re-prefill; with ~9 loop calls per turn this was paying ~3,000 tokens/turn of unnecessary re-prefill on any session with skill/tool mutations.

- **`co_cli/agent/_instructions.py`** — adds `skill_manifest_prompt` and `tool_category_awareness_prompt` as per-turn callables reading live `ctx.deps.skill_index` / `ctx.deps.tool_index` each turn.
- **`co_cli/agent/orchestrator.py`** — removes `_skill_manifest_provider` and `_tool_category_awareness_provider` from `static_instruction_builders`; appends both to `per_turn_instructions`.
- **`co_cli/tools/deferred_prompt.py`** — renames `build_category_awareness_prompt` → `build_tool_category_awareness_prompt` for clarity.
- **`co_cli/context/rules/06_skill_protocol.md`** — drops stale "above" positional wording; manifest now lands after the rules block in the assembled prompt.
- **`co_cli/context/rules/07_memory_protocol.md`** — condenses kind-selection table → bullets (~40 tokens, information preserved 1:1).
- **`docs/specs/`** — 6 spec files updated to reflect static→per-turn relocation and the builder count change.
- **Measured result:** static prefix 7,112 → 6,761 tok (−351); per-turn +340; total −11 absolute.

## [0.8.264]

### REPL input-queue UX — `/queue` command + head-item toolbar preview (Phase 2)

Phase 1 (v0.8.260) made mid-turn submissions enqueue instead of drop. Phase 2 makes that queue **inspectable and manageable**. The user can now see what's pending, drop a mis-typed entry before it costs a turn, or wipe the queue entirely — without killing the session.

- **`co_cli/commands/_queue_control.py`** (new) — queue-control core: `list` / `clear` / `pop [n]` operating on a `deque[str]` by reference. 1-based indices in the user surface, usage errors (never silent no-op or exception) on bad/out-of-range args. Prints via the module-level `console` (no `Frontend` parameter — matches `help.py` / `tasks.py`).
- **`co_cli/commands/queue.py`** (new) — `_cmd_queue(ctx, args)` builtin handler delegating to the core on `ctx.input_queue`; returns `None` so dispatch maps to `LocalOnly` (never a history list, never an armed turn).
- **`co_cli/commands/types.py`** — `CommandContext` gains `input_queue: deque[str] | None = None` (stdlib only, mirrors the existing `completer`/`frontend` optionality — no `_ReplRuntime` import into `co_cli/commands/`).
- **`co_cli/commands/core.py`** — registers `BUILTIN_COMMANDS["queue"]` so the command is visible in `/help` and the completer.
- **`co_cli/main.py`** — `_handle_one_input` passes `input_queue=queue` at the slash-dispatch `CommandContext` build. `_build_accept_handler` gains a controlled mid-turn bypass: a `/queue` prefix (parsed via `_parse_queue_command`, a literal mirror of dispatch's parse so idle and mid-turn paths cannot diverge) runs the queue-control core via `runtime.schedule_control(...)` — **not** `_arm_turn` — so it never carries the `_drain_next` callback and never arms a new turn. All other mid-turn input still enqueues (Phase 1 invariant preserved). `_build_status_snapshot(deps, mode, queue)` now takes the deque positionally and derives both `queue_depth` and a new `queue_head_preview` internally; `_queue_head_preview` truncates `queue[0]` at a fixed `_QUEUE_PREVIEW_BUDGET = 30` char budget for the toolbar.
- **`co_cli/display/core.py`** — `StatusSnapshot` gains `queue_head_preview: str | None = None`. `render_footer_toolbar` renders `{n} queued: "<preview>"` when a preview is present, falls back to the bare `{n} queued` form, and omits the segment entirely at depth 0. Segment placement (between `mode` and `ctx`) is unchanged.
- **Tests** — `tests/test_flow_queue_command.py` (new, 7 dispatch-level tests covering `/queue`/`pop`/`pop n`/`clear` + out-of-range + non-integer + empty-queue no-op; all assert `LocalOnly` + queue mutation by reference). `tests/test_flow_chat_loop.py::test_queue_command_bypasses_enqueue_mid_turn` exercises the bypass against a held `turn_task` stub and asserts observable state (queue empty, `turn_task is long_task`, regression guard for non-`/queue` mid-turn input). `tests/test_display.py` adds toolbar-preview render + snapshot-builder coverage (populated, truncated past budget, none when empty) and updates the Phase 1 depth test to the new `{n} queued: "…"` form. `tests/integration/test_repl_input_queue.py` migrated to the positional `queue` signature.
- **Spec** — `docs/specs/tui.md` synced: queue paragraph mentions `/queue` + head preview; `CommandContext` row gains `input_queue`; `StatusSnapshot` row adds `queue_head_preview`; `_build_status_snapshot` signature row updated; command-reference table gains `/queue`.
- **Plan**: `docs/exec-plans/completed/2026-05-27-214118-repl-queue-ux.md`.

## [0.8.262]

### Tool Gap Batch 1 — restored article URL-dedup + removed `tool_output_raw` spill bypass

Two surgical fixes shipped together. (1) `tool_output_raw` was the one path by which a tool-call output reached context unbounded — an impl-layer helper (`_http_get_with_retries`) built a terminal `ToolReturn` itself and the ctx-bearing entrypoint forwarded it untouched, skipping `spill_with_span`. The fix routes helper errors back through the tool boundary (`tool_error` → `tool_output` → spill) and deletes `tool_output_raw`. (2) The URL-keyed article-save capability was orphaned in the v0.8 unification refactor: `save_memory_item`'s URL-dedup branch (`_find_article_by_url` + `SourceTypeEnum.WEB_FETCH` + consolidation logic) was tested but had no production caller — re-saving the same URL silently created duplicates. The fix threads `source_url` through `memory_manage(action="create", …)`; no service-layer change needed, the plumbing was already there.

- **`co_cli/tools/web/search.py`, `co_cli/tools/web/fetch.py`** — `_http_get_with_retries` return type changed from `httpx.Response | ToolReturn` to `httpx.Response | str`; the three terminal-error returns become bare error strings; both ctx-bearing entrypoints wrap the helper-error case via `tool_error(resp_or_error, ctx=ctx)` so the spill path always fires.
- **`co_cli/tools/tool_io.py`** — `tool_output_raw` deleted (was the spill bypass); module + `tool_error` docstrings updated to state the invariant: "every tool result is constructed at the ctx-bearing entrypoint via `tool_output()`/`tool_error()`, so all spill". Impl helpers without ctx return raw data or error strings — never a `ToolReturn`.
- **`co_cli/tools/memory/manage.py`** — `memory_manage(action="create", …)` accepts `source_url: str | None = None`; threads through `_handle_create` → `save_memory_item`. When set with `kind="article"`, the existing URL-keyed branch fires: `source_type=web_fetch`, `source_ref=<url>`, `decay_protected=True`; re-saves consolidate on `artifact_id` with existing `related` preserved. Absent `source_url`: today's Jaccard path unchanged. `tags`/caller-supplied `related` were rejected as out-of-scope schema work (not restoration).
- **Tests** — `tests/test_tool_io.py` (new): three regression guards — `tool_output_raw` not exposed, helper return annotation excludes `ToolReturn`, both entrypoints wrap the error case via `tool_error`. `tests/test_flow_memory_item_manage.py`: three new URL-dedup tool-surface tests — WEB_FETCH stamping on first create, consolidation on re-save with the same URL (same `artifact_id`, single .md file, content updated), Jaccard/manual fallback when `source_url` is absent.
- **Spec** — `docs/specs/memory.md` rewritten: "Substrate accumulation (passive)" prose, the lifecycle-table row, and the ASCII diagram all corrected — article ingestion is explicit and agent-mediated, never an auto-wire from `web_fetch`. `memory_manage` signature row updated with `source_type`/`source_url` params and a one-sentence URL-dedup-on-create note. `docs/specs/tools.md` removes the `tool_output_raw` row, states the invariant inline, and fixes the `tool_output`/`tool_error` signature rows to match the actual code.
- **Plan**: `docs/exec-plans/completed/2026-05-27-172716-toolgap-b1-fetch-spill.md`.

## [0.8.260]

### REPL input queue — type-ahead during an active turn enqueues instead of dropping (Phase 1)

While a turn ran, mid-turn submissions were silently dropped (`main.py` BC6 from the Phase 0 single-owner refactor). Now submissions during an active turn **enqueue** (FIFO) and drain one item per turn boundary — both on normal completion and on `Esc`-cancel. Idle submissions still run immediately. Matches the Claude Code / opencode interaction model.

- **`co_cli/display/core.py`** — `StatusSnapshot` gains `queue_depth: int = 0` (last field); `render_footer_toolbar` renders `"{n} queued"` between `mode` and `ctx` (omitted at 0). `update_status` now calls `self._invalidate()` after storing the snapshot so a status push repaints with no co-located render event (the drain path has none); `_invalidate()` is a no-op when no app is bound.
- **`co_cli/display/_app.py`** — `_ReplRuntime` gains an in-memory `queue: deque[str]` (session-lifetime, not persisted). Key remap in `build_key_bindings`: `Esc` cancels the active turn and advances the queue (the turn's done-callback drains); `Ctrl+C` is now **exit-only** (double-press) and no longer cancels the turn; `Ctrl+D` (EOF) unchanged.
- **`co_cli/main.py`** — `_build_accept_handler` rewritten: mid-turn non-blank text enqueues and pushes live depth; idle submits via a new `_arm_turn` helper that attaches a `_drain_next` done-callback so the next queued item advances at every turn boundary. Blank/whitespace submissions never occupy a slot. Live depth (`len(runtime.queue)`) flows to `_build_status_snapshot` only from runtime-aware callers; `_handle_one_input`'s contract is untouched.
- **Tests** — `tests/integration/test_repl_input_queue.py` (new) drives a genuinely-running `app.run_async()` with a real warm Ollama turn: type-ahead during the active turn enqueues (`queue_depth==1` via captured snapshot), both turns drain FIFO, queue returns to 0. Unit coverage in `tests/test_display.py` (toolbar depth render, `update_status` invalidate) and `tests/test_flow_chat_loop.py` (FIFO enqueue/drain, blank-drop, `Esc` interrupt+advance, `Ctrl+C` exit-only).
- **Spec** — `docs/specs/tui.md` synced (flow diagram, REPL-loop prose, interrupt-handling, `StatusSnapshot`/`_build_status_snapshot` signatures).

## [0.8.258]

### Single terminal owner — persistent prompt_toolkit Application replaces the Rich Live / PromptSession baton-pass

Phase 0 behavior-preserving re-architecture (unblocks the `repl-input-queue` plan). The REPL had two libraries each owning the terminal — Rich `Live` (output) and prompt_toolkit `PromptSession` (input) — that could only coexist via a sequential baton-pass, so input and output never ran at the same time. Now a single persistent `Application(full_screen=False)` owns the inline terminal; Rich is demoted to a stateless renderable→ANSI builder. Observable UX is unchanged (inline prompt + scrollback transcript, streaming Markdown, panels, toolbar, approvals, slash completion, FileHistory, Ctrl+C double-press exit, theme).

- **New** — `co_cli/display/_app.py` — `build_repl_app(...)` factory assembles the `Application` (in-flight streaming window + input `TextArea` + toolbar `Window`); `build_key_bindings(...)` (Ctrl+C / Ctrl+D); `_ReplRuntime` is the single owner of turn state (`turn_task` + `control_tasks`), passed by reference — never a module global. Mid-turn Ctrl+C cancels the active turn task then arms the 2 s double-press-exit window (BC2 parity).
- **`co_cli/display/core.py`** — new stateless `render_to_ansi(renderable, *, width)` bridge (the sole renderable→string routine). `TerminalFrontend` rewritten to drive the Application: a single in-flight ANSI buffer updated via `app.invalidate()` for streaming surfaces, committed output via `print_formatted_text(ANSI(...))`. All Rich `Live` surfaces (five sites), `set_input_active`, `_pending_status`, and the `active_*` introspection orphans deleted. `prompt_approval`/`prompt_question`/`prompt_confirm`/`prompt_selection` are now coroutines run via `run_in_terminal`; the SIGINT-handler swap is removed.
- **`co_cli/main.py`** — `_chat_loop` rewritten to construct the app, `bind_app`, wrap `app.run_async()` in `patch_stdout()`, and drive the REPL on a single owned Application. The `accept_handler` schedules a turn via `asyncio.ensure_future` for idle submissions and drops mid-turn submissions (BC6 — the Phase 1 enqueue seam). `PromptSession`/`bottom_toolbar`/`set_input_active` wiring removed. Fixed a pre-existing clean-exit crash: `_drain_and_cleanup` called the nonexistent `MemoryStore.close()` → routed through `.index.close()`.
- **`co_cli/display/headless.py`** — async prompt signatures; `set_input_active` removed.
- **`co_cli/commands/{_utils,memory,resume}.py`, `co_cli/context/orchestrate.py`** — full transitive `await` cascade for the now-async prompts; `_confirm`'s no-frontend fallback stays a direct sync `console.input`.
- **Specs** — `docs/specs/{tui,core-loop,bootstrap}.md` synced to the single-owner model (Application/`run_async`, accept_handler scheduling, async approval prompts via `run_in_terminal`, `patch_stdout`, `_app.py` symbols, toolbar `Window`).
- **Tests** — `tests/integration/test_repl_terminal_owner.py` (new) drives a genuinely-running `app.run_async()` with a real warm Ollama turn, asserting committed output, empty in-flight buffer, zero Rich Live, and the same-loop concurrency invariant. `tests/test_display.py` + `tests/test_flow_chat_loop.py` cover `render_to_ansi`, in-flight/transient parity, `patch_stdout` reflow, async-prompt contract, and accept_handler scheduling + mid-turn Ctrl+C cancel.

## [0.8.256]

### Hermes-parity input-token tracking — drop stale-suppression guard

The proactive/L2 compaction triggers no longer reverse-scan message history for `ModelResponse.usage.input_tokens`. A new `TokenTrackingCapability` writes `runtime.last_reported_input_tokens` from every successful `after_model_request` hook, and `commit_compaction` overwrites it with the post-compaction local estimate so the next trigger pass sees the compacted size — not the stale pre-compaction provider value. The two-field cross-turn stale-suppression guard (`post_compaction_token_estimate`, `message_count_at_last_compaction`) and the reverse-scan helper `latest_response_input_tokens` are deleted; staleness is gone by construction.

- **New** — `co_cli/context/token_tracking.py` — `TokenTrackingCapability(AbstractCapability[CoDeps])` overriding `after_model_request` to record `usage.input_tokens` (>0) onto `runtime.last_reported_input_tokens`.
- **`co_cli/deps.py`** — `CoRuntimeState`: deleted `post_compaction_token_estimate` + `message_count_at_last_compaction`; added single `last_reported_input_tokens: int | None = None`. Cross-turn block docstring + `compaction_applied_this_turn` comment updated to reflect the new mechanism.
- **`co_cli/agent/build.py`** — `TokenTrackingCapability` wired between `ObservabilityCapability` and `CoToolLifecycle` in both orchestrator and task-agent builders. Order-independent for the new capability; existing "Observability FIRST" invariant preserved.
- **`co_cli/context/compaction.py`** — `commit_compaction` simplified to a two-field write (`compaction_applied_this_turn`, `last_reported_input_tokens ← post_token_estimate`). `proactive_window_processor` guard block (~30 lines) collapsed into a single `runtime.last_reported_input_tokens or 0` read; dead OTEL span attributes (`guard_active`, `guard_cleared`, `fresh_responses_after_compact`) removed. Stale `latest_response_input_tokens` import + `__all__` entry deleted.
- **`co_cli/context/{orchestrate,history_processors}.py`** — `enforce_request_size` and the turn-end OTEL ratio in `_check_output_limits` now read `runtime.last_reported_input_tokens` instead of calling the deleted scan helper. Stale imports removed.
- **`co_cli/context/summarization.py`** — `latest_response_input_tokens` function deleted; `ModelResponse` import dropped (no longer used in this module).
- **`co_cli/commands/{clear,new}.py`** — slash command resets collapsed from two-field to one-field assignment.
- **Specs** — `docs/specs/compaction.md` (runtime-fields table, L2 trigger formula, proactive flow STEP 1/STEP 6, contract tables, test-coverage row), `docs/specs/core-loop.md` (output-limit diagnostics) rewritten to describe the tracked-field mechanism. "Task-3 invariant" and "stale-suppression guard" terminology removed in favor of direct "single-writer atomicity" language. Filename fix: `test_flow_slash_commands.py` → `test_flow_compaction_slash_commands.py`.
- **Tests** — `tests/test_flow_compaction_slash_commands.py` asserts on the new field. `tests/test_flow_compaction_proactive.py::test_thrash_counter_not_incremented_for_reported_driven_compaction` and the two `tests/test_flow_compaction_enforce_request_size.py` "high_reported" tests set `deps.runtime.last_reported_input_tokens` directly to simulate what `TokenTrackingCapability` would have written.

### Rename: search-tool breadcrumbs → deferred-tool discoveries

Cross-team rename bundled into this ship. `_preserve_search_tool_breadcrumbs` → `_preserve_deferred_tool_discoveries`; docstring expanded to cite pydantic-ai's `ToolSearchToolset._parse_discovered_tools` walk that consumes these returns. Spec references in `compaction.md` and `self-planning.md` updated consistently. No behavior change — purely terminology alignment with pydantic-ai's deferred-tool model.

## [0.8.255]

### Reduce L0 tool-call cap from 6 to 3

Small ollama models (the primary local backend) lose plan coherence past ~3 parallel tool calls per response, producing malformed JSON, duplicate calls, or off-target tool selection. Halving the L0 admission cap from 6 to 3 trades nominal throughput for stability on the realistic local workload. 3 non-spilling (≤ 4K char) returns aggregate well inside the per-request spill threshold; the constant remains non-configurable.

- **`co_cli/tools/tool_call_limit.py`** — `MAX_TOOL_CALLS_PER_MODEL_REQUEST: 6 → 3`. Comment updated to cite small-ollama-model coherence as the sizing constraint.
- **Specs** — `compaction.md` diagram (§1.1), L0 row in §1.2, §2.1 constant paragraph + rejection JSON example, constants table (§Sizing), and contract table updated to the new value.
- **Tests** — `test_flow_tool_call_limit.py` and `test_flow_model_request_cap.py` literal `6`/`7` references in docstrings/comments rewritten to symbolic `MAX` / `MAX+1`. Assertions were already symbolic via `MAX_TOOL_CALLS_PER_MODEL_REQUEST` and pass at the new value.

## [0.8.254]

### Remove `COMPACTABLE_TOOLS` whitelist — unified clearing policy

Deletes the 7-entry `COMPACTABLE_TOOLS` frozenset that gated tool-return content-clearing eligibility. Proactive (`evict_old_tool_results`, `dedup_tool_results`) and recovery (`strip_all_tool_returns`) paths now share one policy: every tool return is eligible past `COMPACTABLE_KEEP_RECENT = 5` per tool name; eligibility is content-shape only, not tool selectivity. Aligns co with the cross-peer default-clear pattern (Hermes/Openclaw uniform; Opencode 1-entry blacklist) — co was the only peer with a whitelist.

- **`co_cli/tools/categories.py`** — `COMPACTABLE_TOOLS` deleted. `FILE_TOOLS` and `PATH_NORMALIZATION_TOOLS` retained.
- **`co_cli/context/history_processors.py`** — 4 filter guards removed from `_build_durable_call_ids`, the durable-tail-protected loop, `_build_keep_ids`, and `evict_old_tool_results` short-circuit. Per-tool-name keep-recent gate iterates by `part.tool_name` only — no category filter. Docstrings reworded.
- **`co_cli/context/_dedup_tool_results.py`** — `is_dedup_candidate` eligibility is now `string content AND len ≥ 200`; tool-name clause removed.
- **`co_cli/context/_tool_result_markers.py`** — `is_cleared_marker` rewritten to recognize any tool-name prefix via `_MARKER_PREFIX_RE = re.compile(r"^\[[a-z_][a-z0-9_]*\] ")` instead of scanning a fixed set. Static `[tool result cleared` prefix branch retained. Generic per-tool fallback marker (`[{tool_name}] (N chars)`) becomes the path for every tool without an explicit branch.
- **Specs** — `compaction.md` §2.3/§2.7/§4/§5, `core-loop.md`, `prompt-assembly.md` rewritten to drop the whitelist framing. Worked examples re-rendered without the "non-compactable preserved" pathway.
- **Tests** — `test_evict_clears_unknown_tool_via_generic_fallback` added to prove an unknown tool (`memory_create`, not in the old whitelist) gets cleared via the generic fallback path after > `KEEP_RECENT` returns. Recency-protection and last-turn-protection tests preserved. Recovery test docstring + comment updated to document the no-filter rule.

### Surrogate sanitizer follow-up — drop proactive history processor

Reactive `SurrogateRecoveryModel` wrapper from 0.8.252 covers every LLM call path, so the proactive `sanitize_surrogate_codepoints` history processor is redundant. Wrapper removed from `orchestrator.py`'s registered processor tuple; the pure helper `sanitize_surrogate_codepoints_messages` remains in `history_processors.py` for direct callers (the reactive wrapper).

## [0.8.252]

### Surrogate sanitizer hardening + memory `source_type` rename

Closes three gaps in the proactive `sanitize_surrogate_codepoints` history processor and adds a reactive backstop via pydantic-ai's `WrapperModel`. Hermes-parity for surrogate defense across every LLM call path, not just the main agent loop.

- **`co_cli/context/history_processors.py`** — `_replace_surrogates` now does `_LONE_SURROGATE_RE.search()` before `sub()`, returning the same string object on no-surrogate text (the hot path). `_sanitize_structure` (new) recursively walks `dict | list` payloads so dict-form `ToolCallPart.args` are now covered — previously the `isinstance(part.args, str)` check silently skipped them. Pure logic split into `sanitize_surrogate_codepoints_messages(messages)`; the `RunContext`-shaped history-processor wrapper remains backward-compatible at the registration site.
- **`co_cli/llm/surrogate_recovery_model.py`** (new) — `SurrogateRecoveryModel(WrapperModel)` overrides `request()` and `request_stream()` to catch `UnicodeEncodeError` from the SDK's `json.dumps`, re-sanitize via the shared helper, and retry once. Bounded retry: if the retry also raises, propagate. The `request_stream` path scopes the catch to pre-open only (an `opened` flag re-raises post-open consumer errors), preserving asynccontextmanager's single-yield contract.
- **`co_cli/llm/factory.py`** — `build_model` wraps both `OpenAIChatModel` (ollama) and `GoogleModel` (gemini) with `SurrogateRecoveryModel`. Single wire-up point covers every LLM call path: main agent, task agents, daemons, direct `model_request` in `llm/call.py`, compaction/summarization, judge model in evals.
- **Memory `source_type`** — `SourceTypeEnum.DETECTED` removed; default for `save_memory_item` flips to `MANUAL`. `memory_manage(action="create", ...)` gains an explicit `source_type` parameter so the session-end memory reviewer can tag reviewer-extracted facts (`session_review`) distinctly from direct agent saves (`manual`). New `07_memory_protocol.md` rule and tests for default + reviewer-source-type behavior.

## [0.8.250]

### Terminology rename: `llm_iteration` / `model_turn` → `model_request`

Disambiguates the user-level loop (`turn`) from the model-level LLM call. Three synonyms (`llm_iteration`, `model_turn`, bare `iteration`) collapse onto one term — `model_request` — matching pydantic-ai's `ModelRequestNode`. `turn` is reserved exclusively for the user-level `run_turn()` loop.

- **Identifiers**: `TurnResult.llm_iterations` / `_TurnState.llm_iterations` → `model_requests`; `MAX_TOOL_CALLS_PER_MODEL_TURN` → `MAX_TOOL_CALLS_PER_MODEL_REQUEST`; `tool_calls_in_model_turn` → `tool_calls_in_model_request`; `iters_since_skill_review` → `model_requests_since_skill_review`; `_post_turn_hook(turn_iteration_count=...)` → `_post_turn_hook(model_request_count=...)`.
- **Config + env var**: `llm.max_iterations_per_turn` → `llm.max_model_requests_per_turn`; `CO_LLM_MAX_ITERATIONS_PER_TURN` → `CO_LLM_MAX_MODEL_REQUESTS_PER_TURN`. The old env var is **deleted** (zero-back-compat) — shells exporting the old name will silently fall back to the default.
- **Error literal**: tool-cap rejection payload `{"error": "max_tool_calls_per_turn_exceeded"}` → `{"error": "max_tool_calls_per_model_request_exceeded"}`.
- **Span attribute**: `turn.llm_iterations` → `turn.model_requests` on the `co.turn` root span.
- **Specs**: `compaction.md`, `core-loop.md`, `config.md`, `observability.md`, `dream.md` updated to the new vocabulary.
- **Tests**: `tests/test_flow_iteration_cap.py` → `tests/test_flow_model_request_cap.py`; `tests/test_flow_turn_result_tool_iterations.py` → `tests/test_flow_turn_result_model_requests.py`; assertion strings + function names migrated.

## [0.8.249]

### Length-retry wire-level fix: bare-continuation + dead max_tokens cap

Two latent wire-level bugs surfaced while auditing LLM call sites. Both manifest as the length-retry path failing in production on Ollama.

- **`co_cli/context/orchestrate.py`** — length-retry no longer sets `current_input = None`. The old behavior built a `ModelRequest` with empty parts, so the conversation sent to Ollama ended with the truncated assistant message. qwen3.6 enters thinking mode on this "bare continuation" shape regardless of `think=False`, exhausting any token budget on `<think>` content before producing text. The retry now preserves the original user prompt, giving the model a proper user turn that respects `think=False`.
- **`co_cli/config/llm.py`** — `extra_body.max_tokens` mirrors the scalar `max_tokens` in both `reasoning` (4096) and `noreason` (8192) settings for qwen3.6. Pydantic-ai maps the scalar to OpenAI's `max_completion_tokens`, which Ollama ignores. Only `extra_body.max_tokens` (merged at the JSON root) actually caps Ollama output. Before this fix, the cap was dead on the wire and `finish_reason='length'` never fired in production — so the length-retry path was unreachable. Comments explain the duplication.

Combined effect: the length-retry safety net documented at `docs/specs/compaction.md:269` ("Generation budget (max_tokens) | 4,096") is now real on Ollama. Reasoning turns cap at 4096 with automatic doubling on truncation; noreason summaries cap at 8192 (well above the typical ~5000-token ceiling).

## [0.8.248]

### Circuit breaker for embed + rerank (task 12.1)

Prevents repeated 30s timeout penalties when the local TEI embed or rerank service is down.

- **`co_cli/index/_circuit.py`** — new `CircuitBreaker`: opens after 3 consecutive failures, exponential cooldown (5s → 10s cap), half-open probe on expiry, resets fully on success
- **`co_cli/index/_embedding.py`** — `EmbeddingService` owns an embed breaker (skipped for `provider="none"`); `embed()` short-circuits when open, signals success/failure on each real call
- **`co_cli/index/_providers.py`** — `_embed` closure now propagates exceptions instead of swallowing them; `embed()` is the sole error boundary
- **`co_cli/index/_retrieval.py`** — `RetrievalService` owns a rerank breaker; `_rerank()` skips the TEI call when open and signals success/failure on each attempt
- **Tests** — 7 unit tests covering threshold, exponential doubling, cap, half-open, and reset

## [0.8.246]

### Dream daemon: absorbs skill lifecycle — merge + decay (plan2b)

Plan2b `skill-lifecycle-absorption`. Folds skill consolidation and decay into `run_housekeeping()` alongside memory; deletes the orphaned curator subsystem.

- **`merge_skills`** — recall-anchored canonical pick, token-Jaccard clustering (threshold configurable), cluster-scoped LLM merge prompt at `daemons/dream/prompts/skill_merge.md`; `MAX_CLUSTER_SIZE=5`, `MAX_MERGES_PER_CYCLE=10`
- **`decay_skills`** — sidecar-anchored age + recall-window protection; archives via collision-safe rename; `_MAX_DECAY_PER_CYCLE=20`
- **`run_housekeeping` ordering** — `merge_skills` runs inside `asyncio.timeout(max_pass_seconds)` after `merge_memory`; `decay_skills` runs after `decay_memory` outside the timeout
- **Skill recall wiring** — `bump_recall` called at both invocation surfaces (slash dispatch and `skill_view` tool); skill manifest emits descriptions only
- **New config knobs** — `skills.recall_protection_days` (default 30), `skills.decay_after_days` (default 90), `skills.consolidation_similarity_threshold` (default 0.75)
- **`HousekeepingStats` extended** — `skill_merged`, `skill_decayed` counters; `co knowledge stats` and `_dream_state.json` surface them
- **Curator deleted** — `co_cli/skills/curator.py`, `curator_prompts.py`, `fork_deps_for_curator`, `CURATOR_RUNS_DIR`, all `curator_enabled`/`curator_interval_hours` config knobs removed
- **Spec sync** — `dream.md` §2.5 Skill Housekeeping; `skills.md` lifecycle moved to dream; `config.md` new knobs; `01-system.md`, `agents.md`, `bootstrap.md`, `tools.md` curator references purged

## [0.8.244]

### Dream daemon: absorbs memory housekeeping (merge + decay)

Plan2a `dream-housekeeping`. Folds memory consolidation into the daemon's scheduled tick — the legacy `run_dream_cycle` orchestrator is retired and the memory-side `dream.py` module is deleted entirely. Housekeeping now runs on a wall-clock cap inside the same polling loop that drains the review queue.

- **Scheduled tick + sentinel-file manual trigger** — `_loop.py` runs `run_housekeeping` when `now ≥ last_housekeeping_at + run_interval_hours` (clamped to `run_at` time-of-day) and when `DREAM_RUN_TAG` sentinel is present. `co dream run` checks daemon liveness then atomic-writes the sentinel; clean error exit when daemon is down
- **`run_housekeeping(deps, cfg, state)`** — wraps `merge_memory → decay_memory` under `asyncio.timeout(max_pass_seconds)`; persists partial counters on TimeoutError; updates `last_housekeeping_at` on every path
- **`merge_memory`** — recall-anchored canonical pick (`max(cluster, key=(recall_count, created_at))`), excludes `article` kind, cluster-scoped LLM merge prompt moved to `daemons/dream/prompts/memory_merge.md`
- **`decay_memory`** — extends `find_decay_candidates` with separate `recall_protection_days` window (skills/memories recalled within the window are protected even if past `decay_after_days`)
- **Persisted housekeeping state** — new `HousekeepingState` + `HousekeepingStats` pydantic models at `DREAM_DAEMON_DIR/_dream_state.json` (distinct from the in-memory `DaemonState`); `co dream status` and `/memory stats` read from it
- **Config knob churn** — `dream.run_interval_hours`, `dream.run_at`, `dream.max_pass_seconds` added; `memory.recall_protection_days` added; `memory.consolidation_enabled`, `consolidation_trigger`, `consolidation_lookback_sessions` dropped. Jaccard write-time dedup is now always-on (parameter removed from `save_memory_item`)
- **Deleted** — `co_cli/memory/dream.py`, `co_cli/memory/_window.py`, `co_cli/memory/prompts/dream_miner.md`, `/memory dream` slash subcommand. Eval imports point at `merge_memory` from `daemons/dream/_housekeeping`
- **Spec sync** — `dream.md` §2 rewritten; `memory.md`, `config.md`, `observability.md`, `01-system.md` cleaned of consolidation-* knob references and `run_dream_cycle` mentions

## [0.8.241]

### Dream daemon: spec sync + banner/CLI follow-up fixes

Surfaced during the post-0.8.239 spec sync pass.

- **Banner `_socket_status` import was dead since v0.8.234** — `co_cli/bootstrap/banner.py` imported `_socket_status` from `co_cli.commands.dream` which has not existed since sockets were retired. Every banner render raised `ImportError`, caught by a broad `except Exception`, falling through to "enabled but daemon not running" — even when the daemon WAS running. Switched to the file-based `status_daemon(USER_DIR)` directly. The deleted regression test `tests/bootstrap/test_banner_dream_line.py` would have caught this; not adding it back here since coworker's clean-tests pass removed it intentionally
- **`co dream stop --force` CLI flag** — `stop_daemon(force=True)` was wired in 0.8.239 but the typer command didn't expose `--force`. Users had no CLI path to invoke it
- **`co dream start --foreground` help text** — was "Run in the foreground (after double-fork)"; now "skip detached spawn via setsid", matching the renamed `spawn_detached`
- **Spec sync (`docs/specs/dream.md`)** — §1.3 queue-tmp note covers both producers (REPL + daemon now both use atomic tmp writes); §5 `process_review` contract documents the `ValueError`-on-unknown-domain semantics from 0.8.239; §6 `_process.py` purpose says `spawn_detached`; §3 CLI surface lists `co dream stop [--force]`

## [0.8.239]

### Dream daemon: latent-bug sweep (10 fixes)

- **`process_review` raises on unknown domain** — `_reviewer.py` was silently returning on bad domain payloads, causing the main loop to archive corrupt kicks as `done/`. Now raises `ValueError`, which the loop catches and routes to `failed/`. New regression test `test_main_loop_unknown_domain_lands_in_failed`
- **`stop_daemon` honors `force=True`** — was accepting the parameter but always SIGTERM-then-SIGKILL. Now SIGKILL directly when force=True (no 10s grace), polls briefly for exit, unlinks PID file
- **`stop_daemon` always unlinks PID file** — graceful and SIGKILL paths both clean up. SIGKILL bypasses the daemon's own finally cleanup, so stop_daemon is the only path that can guarantee the PID file is gone
- **Signal handlers install before `write_pid`** — `_run_foreground` order was write_pid → install handlers; SIGTERM in that window left a stale PID file. Now: handlers → write_pid
- **Daemon file logging wired up** — `_install_daemon_log_handler` attaches a FileHandler to the root logger writing to `$CO_HOME/logs/dream/<ts>.log`. Previously the spec promised this file but Popen used `stderr=DEVNULL` and no handler was configured. Added `DREAM_LOG_DIR` constant
- **`is_pid_live` returns True on EPERM** — `os.kill(pid, 0)` raising PermissionError means the process exists but is owned by another user; the old broad `except OSError` wrongly reported dead
- **`status_daemon` drops dead `timeout_ms` parameter** — function does only local FS reads, never had a remote round-trip
- **`double_fork_detach` → `spawn_detached`** — the name implied the classic POSIX double-fork pattern; reality is single Popen + `start_new_session=True` (setsid). Renamed to match behavior. Callers in `process.py` and `bootstrap/core.py` updated
- **`write_queue_item` is now atomic** — was bare `path.write_text` for in-place attempt-counter updates; a crash mid-write would truncate the queue file. Now writes to `<name>.json.tmp` and `os.replace`-s into place, matching the REPL's KICK-write pattern
- **`_process_kick_file` accepts payload as arg** — was re-reading the queue file (already read by main_loop). Avoids one redundant FS syscall per item

Spec sync (`docs/specs/dream.md`): §1.4 lifecycle reflects new spawn/stop semantics + log-file wiring; §5 public-interface table updated with `spawn_detached`, refined `stop_daemon` semantics, dropped `timeout_ms` from `status_daemon`.

## [0.8.242]

### Timestamp fields renamed to `_at` suffix

All persisted timestamp identifiers normalized to `_at` suffix across all stores (zero backward-compat).

**Renamed fields:**
- `MemoryItem`: `created` → `created_at`, `updated` → `updated_at`, `last_recalled` → `last_recalled_at`
- Memory YAML frontmatter: `created:` → `created_at:`, `updated:` → `updated_at:`, `last_recalled:` → `last_recalled_at:`
- Session YAML frontmatter: `created:` → `created_at:`, `updated:` → `updated_at:`
- IndexStore `docs` SQLite columns: `created` → `created_at`, `updated` → `updated_at`
- IndexStore `embedding_cache` SQLite column: `created` → `created_at`
- IndexStore `upsert` / `upsert_skill` / `upsert_canon` kwargs: `created=` → `created_at=`, `updated=` → `updated_at=`
- `SearchResult` dataclass fields: `created` → `created_at`, `updated` → `updated_at`

**One-time data migration required** — run before starting co after this upgrade:

```bash
# Memory frontmatter
find ~/.co-cli/memory -name '*.md' -exec sed -i '' \
  -e 's/^created: /created_at: /' \
  -e 's/^updated: /updated_at: /' \
  -e 's/^last_recalled: /last_recalled_at: /' {} \;

# Session frontmatter
find ~/.co-cli/sessions -name '*.md' -exec sed -i '' \
  -e 's/^created: /created_at: /' \
  -e 's/^updated: /updated_at: /' {} \;

# Search index — drop and let next run rebuild
rm ~/.co-cli/co-cli-search.db
```

## [0.8.238]

### Dream daemon: flatten two-layer loop + interruptible retry backoff

- **Single `main_loop` in `co_cli/daemons/dream/_loop.py`** — collapsed the outer poll-or-drain loop and inner `_drain_queue` into one while-loop with three branches (idle-poll, process-item, retry-backoff). Deleted `_drain_queue` and `_initial_drain` — cold-start drain is now implicit (first iterations process pending files before any sleep). FIFO order, skip-sleep-when-busy, and between-items shutdown checks are all preserved
- **Interruptible retry backoff** — the previous `await asyncio.sleep(cfg.retry_backoff_seconds)` was not woken by `shutdown.set()`, so a SIGTERM landing during retry backoff (default 30s) could blow past the 10s SIGTERM→SIGKILL budget. Now uses `await asyncio.wait_for(shutdown.wait(), timeout=retry_backoff_seconds)` — same pattern as the idle poll, wakes immediately on signal
- **New regression test** — `test_main_loop_shutdown_interrupts_retry_backoff` configures `retry_backoff_seconds=10` and asserts main_loop exits in under 5s when shutdown fires mid-backoff (runs in 0.05s post-fix). Guards against future re-introduction of non-interruptible sleeps
- **Test migration** — 4 tests in `tests/daemons/dream/test_loop.py` + `test_timeout_retry.py` migrated from calling `_drain_queue` directly to driving `main_loop` with scheduled `shutdown.set()`. Matches observable-behavior testing pattern (no internal-helper coupling)
- **Spec sync** — `docs/specs/dream.md` §1.1 ASCII diagram replaced with a cleaner two-process + shared-FS sketch (pseudocode moved out of the diagram into prose). §1.4 worker loop pseudocode consolidated to one block; Clean-shutdown bound paragraph rewritten to reflect interruptible sleeps and honestly bounded by `review_timeout_seconds`

## [0.8.236]

### Memory chunker: structure-aware sentence-split + heading boundaries

- **Sentence-split fallback in `_split_para_into_chunks`** — when a paragraph contains a single line that exceeds `chunk_tokens`, the chunker now splits on `[.!?]\s+(?=[A-Z])` and packs sentences up to budget before falling through to character split. Closes the gap where externally-ingested content (Obsidian notes, Drive docs, wall-of-text web articles) produced mid-sentence / mid-word chunk boundaries
- **ATX heading as hard section boundary** — `^#{1,6}\s` lines force flush of the current accumulator AND suppress overlap into the heading-starting chunk. Previously a chunk could span the tail of section A + `# Section B` + the head of section B, mixing unrelated topics in one embedding. Strict ATX form only — `#hashtag` / `#1234` (no space) are correctly NOT treated as headings
- **New `tests/test_memory_chunker.py`** — 8 unit tests covering: short-circuit, sentence-split, char-split fallback, heading boundary + overlap suppression, multi-level headings (`##` / `###`), non-heading hash variants, line-number citation metadata, intra-section overlap correctness
- **Clean-tests pass on `tests/test_flow_memory_store.py`** — removed 2 schema-only tests (`test_nochunk_produces_one_chunk_per_file`, `test_nochunk_chunk_index_is_zero`) that accessed `index._conn` for shape-only assertions already covered by `test_get_chunk_content_returns_full_body`

## [0.8.234]

### Dream daemon decouple + unified bootstrap

- **Filesystem-only IPC** — Unix socket IPC removed (`_ipc.py` deleted). Daemon main loop is now pure polling: drain queue, sleep `poll_interval_seconds` (default 5s, range 1–60), skip-sleep-when-busy. Producer `_send_review_kick` collapses to a single atomic file write — no socket nudge, no best-effort signaling
- **POSIX-native daemon control** — `co dream stop` sends SIGTERM with 10s SIGKILL fallback (no socket round-trip). `co dream status` reads the PID file + queue directory directly. `co dream start` exits non-zero on a live PID and overwrites stale PID files. Signal handlers register in `_run_foreground` *before* `create_deps` so SIGTERM during bootstrap still terminates cleanly
- **Unified deps bootstrap** — daemon and REPL now share `create_deps(*, on_status, stack=None, theme_override=None)`. `_deps.py` deleted entirely. Daemon passes `on_status=logger.info, stack=None` to skip MCP; REPL passes `on_status=frontend.on_status, stack=stack`. Fixes a latent bug where the daemon's `CoDeps` was missing `index_store` / `memory_store` / `skill_index` — production reviewer agents would have crashed on the first `memory_search` / `skill_view` call
- **Spec sync** — `docs/specs/dream.md` rewritten: polling architecture diagram, no-socket key properties, file-based inspectability surfaces, `poll_interval_seconds` config row, `create_deps` public-interface entry
- **Test cleanup** — clean-tests pass purged 15 redundant unit tests subsumed by integration coverage; 20 behavioral tests remain. Stale singleton-no-op test in `test_auto_spawn_race.py` removed (replaced by SystemExit-on-conflict contract verified by `test_daemon_lifecycle.py`)

## [0.8.232]

### Per-skill usage sidecars + backward-compat smell purge

- **Per-skill sidecars** — skill usage tracking moves from one shared `~/.co-cli/skills/.usage.json` to per-skill `<name>.usage.json` next to each skill. Bounds the blast radius of concurrent writes to a single skill; eliminates whole-library rewrites on every bump
- **New `usage.py` API** — `read_record(deps, name)`, `write_record(deps, name, record)`, `iter_records(deps)` replace shared-dict `read_records` / `write_records`. Public `bump_*` / `record_create` / `forget` / `set_pinned` signatures unchanged
- **Curator refactor** — `apply_state_transitions(records, ...)` split into pure `apply_state_transition_one(name, record, ...)` + `compute_pending_transitions(deps, ...)` orchestrator; phase 1 iterates per-skill, writes per-skill
- **Zero-backward-compat purge** — removed `.setdefault(...)` backfills (`recall_days`, `version`) in `read_record` / `iter_records` / `write_record` / `bump_recall`; replaced `.get(field, default)` patterns with direct field access across curator + `/skills usage` display. Dead canon-frontmatter `artifact_kind or kind` fallback in `bootstrap/core.py` stripped to `kind="canon"`

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
