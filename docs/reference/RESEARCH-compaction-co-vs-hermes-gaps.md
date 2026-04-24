# RESEARCH: Context Compaction — co-cli vs hermes-agent Gap Analysis

Scope: deep-code comparison of co-cli's compaction stack against hermes-agent,
identifying gaps in co-cli to adopt or learn from.

Method: full function-body reads of both implementations on 2026-04-21.
Last co-cli sync: 2026-04-23 (ninth pass — current workspace closes the former handoff-prefix gap via a defensive `_summary_marker()` and the former todo-survival gap via `_build_todo_snapshot()` injected after compaction and by `/compact`; architecture and remaining-gap numbering updated to match the live implementation).
Last hermes-agent sync: 2026-04-22 (all 15 claim areas verified — no drift detected; no compaction-related commits since April 2026).

co-cli sources (read):
- `co_cli/context/_history.py`
- `co_cli/context/summarization.py`
- `co_cli/context/orchestrate.py`
- `co_cli/context/_http_error_classifier.py`
- `co_cli/tools/categories.py`
- `co_cli/commands/_commands.py`
- `co_cli/config/_compaction.py`, `_llm.py`, `_tools.py`
- `co_cli/tools/tool_io.py`
- `co_cli/agent/_core.py`

hermes-agent sources (read):
- `agent/context_compressor.py`
- `agent/context_engine.py`
- `agent/error_classifier.py`
- `agent/model_metadata.py`
- `run_agent.py`
- `gateway/run.py`

---

## Remaining Gaps — Ranked by Impact

| # | Risk | Gap | Section |
|---|------|-----|---------|
| 1 | Low | Dedicated iterative-update prompt framing prior summary as ground truth | §3 |
| 2 | Low | `/compact <focus>` escape hatch when anti-thrash gate blocks | §8 |
| 3 | Low | Time-based circuit-breaker cooldown (bounded recovery interval) | §7 |
| 4 | Low | Wire `emergency_compact` into production overflow recovery as final fallback | §7 |
| 5 | Low | Session rollover with parent lineage for audit trail | §9 |
| 6 | Low | Cross-turn anti-thrash accumulation (gate is currently per-turn only) | §8 |

Shipped and rejected items from prior passes have been removed; see git history of
this file for the earlier evolution record (commits c700eb5, 4d6e463, cb9e57a, the
semantic-marker ship on 2026-04-23, and the dedup ship on 2026-04-23). The current
workspace also contains the defensive summary wrapper and durable todo snapshot
changes described above.

---

## Architecture Overview

### co-cli: six-mechanism pipeline

```
M0  Pre-turn hygiene (88 % threshold) — fires at run_turn() entry, before agent loop
    Token count: max(estimate_message_tokens(message_history), reported_input_tokens)
                 reported_input_tokens = deps.runtime.turn_usage.input_tokens read
                 BEFORE reset_for_turn() clears it (prior-turn API count)
    Threshold: hygiene_ratio × budget (default 0.88)
    When exceeded: calls summarize_history_window(ctx, message_history)
                   — same planner as M3; LLM summary or static marker
    Anti-thrash gate: cleared unconditionally after hygiene returns, ensuring M3's
                      gate starts fresh each turn regardless of whether M0 fired
    Fails open: any exception returns message_history unchanged

M1  Emit-time cap — tool result → disk at persist time, never re-enters context
    Per-tool max_result_size (persist_if_oversized in tool_io.py):
      30 K chars shell; 50 K default; math.inf file_read (prevents persist→read→persist loop)
    file_read in-tool caps (co_cli/tools/files/read.py):
      500-line default when no range given (+ continuation hint when more lines remain)
      2000-line hard ceiling on any explicit range
      2000-char per-line truncation with ...[truncated] marker
      500 KB file size gate blocks full-file reads with no range (explicit ranges proceed)

M2a dedup_tool_results (processor #1 — always-on, sync, no LLM):
    Boundary: last UserPromptPart index (_find_last_turn_start); protects last turn entirely
    Candidate gate: COMPACTABLE_TOOLS ∩ string content ∩ len(content) ≥ 200 chars
    Reverse-scan older history; keep only the latest occurrence per
      (tool_name, sha256_prefix(content))
    Earlier identical returns collapse to:
      "[Duplicate tool output — identical to more recent <tool> call (call_id=...)]"
    Preserves each original ToolReturnPart.tool_call_id so ToolCallPart ↔ ToolReturnPart
      pairing stays valid

M2b truncate_tool_results (processor #2 — always-on, sync, no LLM):
    Boundary: last UserPromptPart index (_find_last_turn_start); protects last turn entirely
    Keep COMPACTABLE_KEEP_RECENT=5 most-recent ToolReturnParts per compactable tool type
    Compactable set: file_read, shell, file_grep, file_glob, web_search, web_fetch,
                     knowledge_article_read, obsidian_read
    Clear older with per-tool semantic markers via semantic_marker(...), e.g.:
      "[shell] ran `uv run pytest` → exit 0, 47 lines"
      "[file_read] foo.py (full, 1,200 chars)"
    Non-string content falls back to static placeholder
      "[tool result cleared — older than 5 most recent calls]"
    Does NOT touch ToolCallPart.args (never truncated — load-bearing for M3 token estimate)
    Does NOT touch non-compactable tools (e.g. search_tools, memory)

M2c enforce_batch_budget (processor #3 — always-on, sync, disk I/O):
    Current batch: ToolReturnParts after last ModelResponse containing a ToolCallPart
    If aggregate > batch_spill_chars (200 K chars default):
      spill largest non-persisted candidates via persist_if_oversized(max_size=0), repeat
      until aggregate ≤ threshold or no eligible candidates remain
    Skipped in sub-agent delegation chains (short-lived, unnecessary overhead)

M3  summarize_history_window (processor #4 — async, LLM call):
    Threshold: max(int(budget × proactive_ratio), min_context_length_tokens)
               = max(0.75 × budget, 64 K tokens) — two-way floor
    Token count: max(estimate_message_tokens(messages), api_reported_tokens)
                 api_reported = 0 when compacted_in_current_turn flag set
                 (prevents spurious re-trigger after same-turn compaction)
    Anti-thrash gate: skip when consecutive_low_yield_proactive_compactions ≥
                      proactive_thrash_window (default 2); does NOT block M0 or ER
    Boundary planner plan_compaction_boundaries (shared with ER):
      1. head_end = find_first_run_end (first TextPart/ThinkingPart response) + 1
      2. group_by_turn — groups at UserPromptPart boundaries
      3. Walk groups from tail accumulating tokens; stop before tail_fraction × budget (0.40)
         _MIN_RETAINED_TURN_GROUPS = 1: last group always retained even if budget exceeded
      4. _anchor_tail_to_last_user: extend tail_start to cover last UserPromptPart
      5. Abort if tail_start ≤ head_end (head/tail overlap — nothing to drop)
    Enrichment context gathered from dropped range for summarizer (_gather_compaction_context):
      (1) file paths from ToolCallPart.args of FILE_TOOLS calls in dropped range
      (2) pending todos from ctx.deps.session.session_todos
      (3) prior summary text from dropped messages (detected by _SUMMARY_MARKER_PREFIX)
      capped at _CONTEXT_MAX_CHARS = 4000 chars total
    _preserve_search_tool_breadcrumbs: search_tools ToolReturnParts from dropped range
      injected between marker and tail; no separate dedup layer
    LLM summarizes via summarize_messages(deps.model.settings_noreason, no reasoning/thinking)
    Circuit breaker: compaction_failure_count ≥ 3 → skip LLM; probe every 10 skips (CIRCUIT_BREAKER_PROBE_EVERY=10)
    Summary injected as _summary_marker (LLM text); _static_marker on circuit breaker or failure
    _summary_marker is now explicitly defensive:
      "REFERENCE ONLY ... Do NOT repeat, redo, or re-execute ..."
      "respond only to user messages that appear AFTER this summary"
    _apply_compaction now also injects optional _build_todo_snapshot(...) immediately
      after the marker, preserving pending/in_progress todos outside the summary text
    Sets compacted_in_current_turn = True, history_compaction_applied = True
    Savings < min_proactive_savings (0.10) → increment low-yield counter; else reset to 0

ER  Overflow recovery (provider HTTP 400/413):
    is_context_overflow (co_cli/context/_http_error_classifier.py):
      HTTP 413 → True unconditionally
      HTTP 400 → True only with explicit overflow evidence in body:
        Recognized phrases (17): "prompt is too long", "context_length_exceeded",
        "maximum context length", "context length", "context size", "context window",
        "token limit", "too many tokens", "exceeds the limit", "input token count",
        "maximum number of tokens", "prompt length", "input is too long",
        "maximum model length", "max input token", "exceeds the max_model_len",
        "reduce the length"
        Recognized codes: context_length_exceeded, max_tokens_exceeded
        Parsed paths: error.message, flat body message, metadata.raw inner JSON
    Two attempts via same boundary planner:
      (1) normal tail_fraction; (2) tail_fraction / 2 if (1) returns None
    First overflow in turn: attempt recovery and retry; second overflow → terminal error
    emergency_compact (static head+last+marker, no LLM) defined in _history.py
      but NOT wired to production path; used in tests only
```

All of M2a–M3 (dedup, truncate, batch-budget, summarize) run as pydantic-ai
history processors before each `ModelRequestNode`.
M1 runs at tool-return time, outside the processor chain. M0 runs at turn entry,
also outside the chain.

### hermes-agent: three-layer defence + five-phase algorithm

```
L0  Gateway hygiene (85 % threshold) — fires pre-agent in gateway process (gateway/run.py)
    Scope: long-lived gateway sessions only (not CLI turns); separate process layer
    Token source: session_entry.last_prompt_tokens (API-reported) OR rough char estimate
    Hard safety valve: forces compression when message count ≥ 400 regardless of tokens
    Threshold: 0.85 × context_length (intentionally higher than L1/L2's 50%:
               hygiene is a safety net; L1/L2 handle normal management)
    When exceeded: spawns temp AIAgent (quiet_mode=True, skip_memory=True, max_iterations=4)
                   filters to user/assistant messages only; calls _compress_context
    Session rollover: new session_id with parent_session_id link; transcript rewritten in store

L1  Preflight compression (50 % threshold) — fires at top of run_turn, before main loop
    Scope: applies to all sessions (CLI and gateway); handles model-switch / session rehydration
    Token count: estimate_request_tokens_rough(messages, system_prompt, tools)
                 — includes tool schema tokens (20-30K+ for large toolsets)
    Threshold: context_compressor.threshold_tokens = max(0.50 × context_length, 64K)
    Up to 3 passes (for very large sessions or small context windows)
    Each pass calls _compress_context; resets empty-content retry counters after compression

L2  In-loop compression (50 % threshold) — fires after each tool batch, inside main loop
    Location: after tool execution, before the next LLM call in the tool-calling loop
    Token count: last_prompt_tokens + last_completion_tokens (API-reported);
                 fallback: estimate_messages_tokens_rough when last_prompt_tokens == 0
    should_compress() includes anti-thrash: _ineffective_compression_count ≥ 2 → False
    When fires: calls _compress_context

ER  Error-triggered compression — via classify_api_error() in error_classifier.py
    Fires on: context_overflow (HTTP 400 with overflow phrases) or payload_too_large (413)
    Up to max_compression_attempts per turn; each attempt calls _compress_context
    Falls through to normal error handling if compression exhausted or didn't reduce size

_compress_context wrapper (called by L0, L1, L2, ER):
    Pre-compression:
      flush_memories(messages, min_turns=0) — agent memory extraction before context lost
      memory_manager.on_pre_compress(messages) — external provider notification
    Core:
      context_compressor.compress(messages, focus_topic) — Phase 1–5 below
    Post-compression:
      todo_snapshot = todo_store.format_for_injection(); append as user message
        (unconditional — ensures active tasks survive even if summary omits them)
      system prompt rebuild: _invalidate_system_prompt() + _build_system_prompt()
      last_prompt_tokens updated to post-compression estimate (system + messages)
    Session rollover (when session_db present):
      session_db.end_session(old_id, reason="compression")
      new session_id = timestamp + uuid6 hex
      session_db.create_session(new_id, parent_session_id=old_id, model=…)
      title propagation: get_next_title_in_lineage (auto-numbering)
      session_log_file updated; _last_flushed_db_idx reset to 0

ContextCompressor.compress (Phases 1–5):

    Phase 1  Tool output pruning (full message list, no LLM):
      Tail boundary: protect_tail_tokens (token-budget walk from end, including tool call args)
                     OR protect_last_n = 20 messages as fallback; min floor = min(20, len-1)
      Pass 1 — hash-based dedup (full list, before boundary):
               identical results > 200 chars (non-multimodal) → back-reference string
               "[Duplicate tool output — same content as a more recent call]"
      Pass 2 — semantic condensing (outside tail boundary):
               results > 200 chars → _summarize_tool_result 1-line description
               Examples: "[terminal] ran `npm test` -> exit 0, 47 lines output"
                         "[read_file] read config.py from line 1 (1,200 chars)"
                         "[search_files] content search for 'compress' in agent/ -> 12 matches"
               Tool-specific patterns for: terminal, read_file, write_file, search_files,
               patch, browser_*, web_search, web_extract, delegate_task, execute_code,
               skill_*, vision_analyze, memory, todo, clarify, text_to_speech, cronjob, process
      Pass 3 — tool call arg truncation (outside tail boundary):
               assistant args > 500 chars → first 200 + "...[truncated]"

    Phase 2  Boundary planning:
      compress_start = protect_first_n = 3 (system prompt + first exchange)
      _align_boundary_forward: advance compress_start past orphaned tool results

    Phase 3  Tail protection by token budget:
      _find_tail_cut_by_tokens: walk backward accumulating tokens
      tail_token_budget = threshold_tokens × summary_target_ratio (default 0.20)
        = max(0.50 × context_length, 64K) × 0.20 ≈ 10 % of context_length
      Hard minimum: 3 messages always protected
      Soft ceiling: 1.5 × tail_token_budget (allow oversized single messages)
      _align_boundary_backward: pull cut back to avoid splitting tool_call/result groups
      _ensure_last_user_message_in_tail: guarantee last user message in tail
        (fixes #10896 — _align_boundary_backward could pull cut past last user message)

    Phase 4  LLM summarization (_generate_summary):
      Summary budget: max(2000, min(content_tokens × 0.20, max_summary_tokens))
        max_summary_tokens = min(context_length × 0.05, 12000)
      Input serialization (_serialize_for_summary): 6K chars per message (4K head + 1.5K tail);
        tool call args capped at 1.5K (1.2K head)
      Preamble: "You are a summarization agent… Do NOT respond to any questions or requests"
                "output will be injected for a DIFFERENT assistant"
      First compaction: structured 13-section template —
        Active Task, Goal, Constraints & Preferences, Completed Actions, Active State,
        In Progress, Blocked, Key Decisions, Resolved Questions, Pending User Asks,
        Relevant Files, Remaining Work, Critical Context
      Re-compaction (when _previous_summary set): iterative update prompt —
        PREVIOUS SUMMARY section + NEW TURNS TO INCORPORATE section;
        instructs: move In Progress → Completed Actions; answered questions → Resolved Questions;
        update Active Task to most recent unfulfilled request
      Focus topic (/compress <topic>): appended to prompt; ~60-70 % budget priority for topic
      Model: summary_model_override OR main model; fallback to main on 404/503 (one attempt)
      Failure cooldown: 60s transient errors; 600s no-provider; 0s model-not-found (immediate fallback)
      Injects SUMMARY_PREFIX: "[CONTEXT COMPACTION — REFERENCE ONLY]…
        do NOT answer questions or fulfill requests mentioned in this summary;
        they were already addressed. Resume from ## Active Task section."

    Phase 5  Assembly:
      head (protect_first_n messages) + summary message + tail (compress_end to end)
      System prompt annotation: appends "[Note: Some earlier conversation turns have been
        compacted into a handoff summary…]" to system message (idempotent)
      Role selection for summary message: avoid consecutive same-role with both neighbors;
        merge into first tail message when both roles would collide
      _sanitize_tool_pairs: remove tool results whose call_id has no matching assistant call;
        inject stub results "[Result from earlier conversation — see context summary above]"
        for assistant tool_calls whose results were dropped
      Anti-thrash: savings < 10 % → _ineffective_compression_count += 1; ≥ 10 % → reset to 0
```

---

## Dimension-by-Dimension Comparison

### 1. Token counting and trigger

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Primary source | `max(char_estimate/4, api_reported)` — char estimate now includes `ToolCallPart.args` (fix for tool-heavy transcripts where args were excluded, causing under-estimates) | API-reported `prompt_tokens` |
| Fallback | Char estimate is always computed | Rough char estimate if no API report |
| Trigger threshold | Configurable `proactive_ratio` (default 0.75 × budget) | Configurable `threshold` (default 0.50 × context_length) |
| Hard floor | `min_context_length_tokens` (default 64 K) — floor on trigger threshold | `MINIMUM_CONTEXT_LENGTH = 64 K` — floor on trigger threshold |
| Pre-trigger hygiene | M0 at 0.88 (separate, earlier threshold) | Gateway at 0.85 (separate process/layer) |

No current gaps in this dimension.

### 2. Summarization model

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Model | Always `deps.model.settings_noreason` (main model, no reasoning) | Configurable auxiliary model (`auxiliary.compression.model`) |
| Fallback | Static marker on failure | Fall back to main model on 404/503 |
| Separate endpoint | No | Yes — `auxiliary.compression.base_url`, `provider` |
| Timeout | Inherits LLM client default | Configurable (default 120 s) |

**Not a gap:** `summarize_messages` calls `llm_call` with
`deps.model.settings_noreason`, which disables extended reasoning/thinking — the
dominant cost driver for large models. The auxiliary-model comparison is also
inconsistent with co-cli's single-model architecture (see Design decisions below).

### 3. Iterative recompression / summary updates

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| On second compaction | Single fixed prompt for all compactions; prior summary injected as `## Additional Context`; LLM instructed to merge transitions | Separate re-compaction prompt with `PREVIOUS SUMMARY` section; LLM told to PRESERVE existing info and ADD new progress |
| Prior summary detection | `_gather_prior_summaries()` — scans dropped range for _SUMMARY_MARKER_PREFIX, appends as additional context | `_previous_summary` instance variable — stored in-memory and passed directly |
| Transition instructions | In prompt: Pending→Resolved when answered; Resolved carry forward; carry all other info forward | In iterative update prompt: move In Progress → Completed Actions; answered questions → Resolved; update Active Task |
| Risk | Prior summary injected as `## Additional Context` alongside new content — LLM must merge both; contradictions possible if LLM rewrites prior facts | State machine driven by LLM fidelity; imprecise merges possible; `_previous_summary` in-memory so survives only within same session object |

**co-cli gap (risk reduced):** co-cli's summarizer now receives prior summaries as
`## Additional Context` and has explicit transition instructions. The remaining gap
is structural: co-cli uses one fixed prompt for all compactions (no separate "update
mode"); hermes switches to a dedicated iterative update prompt that explicitly frames
the prior summary as ground truth to preserve. Both rely on LLM fidelity for merging.

**Gap #1 — Risk: Low. Hermes: dedicated iterative-update prompt with prior summary as ground truth; co-cli single prompt + additional context injection.**

### 4. Tool result handling before summarization

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Pre-LLM cheap pass | M2a dedup (identical-to-more-recent collapse) + M2b keep-5 semantic clearing per compactable tool + M2c batch spill — all sync, no LLM | Phase 1 `_prune_old_tool_results()` — collapses old tool results to semantic 1-line descriptions |
| Semantic condensing | Yes — `semantic_marker` in `co_cli/context/_tool_result_markers.py` dispatches per-tool handlers (e.g. `[shell] ran \`uv run pytest\` → exit 0, 47 lines`, `[file_read] foo.py (full, 1,200 chars)`) with a generic fallback; call_id → args index built in `_history.py:_build_call_id_to_args` so markers can reference the original call's args. Non-string (multimodal) content falls back to the static `_CLEARED_PLACEHOLDER`. Shipped 2026-04-23. | Yes — generates compact human-readable summaries e.g. "`[terminal] ran npm test → exit 0, 47 lines output`" |
| Deduplication | Yes — `dedup_tool_results` in `co_cli/context/_history.py` (shipped 2026-04-23). Reverse-scans `messages[:_find_last_turn_start]` for ToolReturnParts eligible per `is_dedup_candidate` (compactable tool, string content, ≥ 200 chars); records the `tool_call_id` of the most-recent occurrence per `(tool_name, sha256_prefix(content))` key; rewrites earlier identical returns to back-reference markers via `build_dedup_part` while preserving each return's original `tool_call_id`. Registered as the first history processor so the kept recent window is already collapsed before M2b's recency clearing runs. Non-string content and non-compactable tools pass through. | Hash-based dedup of identical tool result content |
| Max result threshold | M1 (per-tool): 30 K shell, 50 K default, `math.inf` file_read — no cap | 6 K per-message serialization cap for summarizer input |

Shipped 2026-04-23. Previously Gap #2 tracked the absence of identical-content
dedup, leaving the kept recent window (5 most-recent per compactable tool) to
carry N× full content when the same file/URL/query produced identical results
across several turns. The new `dedup_tool_results` processor attacks exactly
that pattern: identical-to-more-recent returns collapse to a 1-line back-
reference, freeing tokens that M2b's recency gate would not have touched. Two
shared scaffolding helpers in `_history.py` (`_iter_tool_returns_reversed` and
`_rewrite_tool_returns`) are reused by `truncate_tool_results` — behaviour-
preserving DRY refactor validated against the existing truncation test suite.

### 5. Summary structure and handoff framing

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Sections | 10 fixed + 1 conditional: Active Task, Goal, Key Decisions, (User Corrections — conditional, inserted after Key Decisions when user overrides/rejections found), Errors & Fixes, Working Set, Progress, Pending User Asks (skip-if-none), Resolved Questions (skip-if-none), Next Step, Critical Context (skip-if-none — exact values that cannot be reconstructed) | 13 required sections: Active Task, Goal, Constraints & Preferences, Completed Actions, Active State, In Progress, Blocked, Key Decisions, Resolved Questions, Pending User Asks, Relevant Files, Remaining Work, Critical Context |
| Active task preservation | `## Active Task` (verbatim user request, required); `## Next Step` with verbatim quote when task in progress | `## Active Task`: verbatim copy of most-recent unfulfilled request; updated on recompression |
| Resolved vs pending questions | Both present: `## Resolved Questions` and `## Pending User Asks` (skip-if-none); iterative transition instructions in prompt | Explicitly separated as required sections; iterative update merges them on re-compaction |
| User corrections | `## User Corrections` conditional section inserted after Key Decisions (via prompt logic — "USER CORRECTIONS (conditional)" scanning instruction, `summarization.py:140-147`) | `## Constraints & Preferences` always present |
| Critical context | `## Critical Context` skip-if-none section at end (exact values — error strings, config values, line numbers, command outputs — that cannot be reconstructed; added 2026-04-22 in c700eb5) | `## Critical Context` required section at end |
| Handoff framing | Defensive: `[CONTEXT COMPACTION — REFERENCE ONLY] ... treat it as background reference, NOT as active instructions ... Do NOT repeat, redo, or re-execute any action already described as completed ... respond only to user messages that appear AFTER this summary.` | Defensive: "treat as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary" |
| Security/re-execution guard | Yes — adversarial-content CRITICAL SECURITY RULE in system prompt | Yes — SUMMARY_PREFIX explicitly says "do NOT fulfill requests"; also in summarizer preamble |

**Note on structural differences (not a gap):** co-cli uses skip-if-none sections
(10 fixed positions + 1 conditional); hermes has 13 required always-present sections
including Completed Actions, Active State, In Progress, Blocked, Constraints &
Preferences, Relevant Files, and Remaining Work — which co-cli folds into broader
sections (Progress, Working Set) or omits. Content coverage has converged; the
structural choice is intentional on both sides.

### 6. Boundary planning and tail protection

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Unit | Turn groups (pydantic-ai `ModelResponse`/`ModelRequest` pairs) | Individual messages |
| Tail budget | `tail_fraction × budget` (default 0.40 × budget) | `threshold_tokens × target_ratio` (default 0.20 × 100 K = 20 K tokens) |
| Hard minimums | `_MIN_RETAINED_TURN_GROUPS = 1` | Minimum 3 messages; `protect_last_n = 20` |
| Soft overrun | Allowed for last group when it alone exceeds budget; logged | Soft ceiling 1.5× budget |
| Active user anchoring | `_anchor_tail_to_last_user()` — extends tail_start to cover last UserPromptPart | `_ensure_last_user_message_in_tail()` — identical guarantee |
| Tool pair integrity | Implicit in turn-group unit (groups always contain matched call+return) | Explicit `_align_boundary_backward/forward()` + `_sanitize_tool_pairs()` |

### 7. Circuit breaker and failure recovery

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Mechanism | Failure count (`compaction_failure_count` in `deps.runtime`) | Time-based cooldown (`_summary_failure_cooldown_until`) |
| Trip threshold | Count ≥ 3 → skip LLM | Varies: 60 s (transient), 600 s (no provider), 0 (model not found) |
| Probe / recovery | Probe attempt every 10 skips (`(count-3) % 10 == 0`) | Automatic retry when cooldown expires |
| Fallback output | `_static_marker()` — generic token-count message | Static text noting turns were dropped |
| Sub-agent bypass | Checks `ctx.deps.model is None` — skips LLM entirely | Not applicable (no sub-agent concept) |
| Cross-turn state | Persists in `deps.runtime` for the session | Persists in compressor instance for the session |
| Overflow recovery | Two-attempt per turn: (1) normal `tail_fraction`; (2) `tail_fraction / 2` if (1) returns None. Second overflow in same turn → unrecoverable. `emergency_compact` (static first+last+marker, no LLM) defined in `_history.py` but NOT wired to production — tests only. | Up to `max_compression_attempts` per turn (context_overflow and payload_too_large paths); each attempt calls `_compress_context` (full LLM summary); falls back to static summary text when `_generate_summary` returns None |

**co-cli gap:** `emergency_compact` is defined in `_history.py` (static head+last+marker, no LLM) but not wired into the production overflow path — currently used only in tests. When both `recover_overflow_history` attempts return None, the session falls to an unrecoverable error state rather than degrading gracefully.

**Gap #4 — Risk: Low. Hermes: no equivalent; static text fallback is production there.**

**co-cli gap:** Failure count is a monotone counter; recovery resets to 0. If provider
issues are intermittent, a string of failures could push count to very high values,
making probes sparse (`count-3` growing). Hermes's time-based cooldown is bounded.

**Gap #3 — Risk: Low. Hermes: time-based cooldown (`_summary_failure_cooldown_until`) with bounded, deterministic recovery interval.**

### 8. Anti-thrashing

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Mechanism | Consecutive low-yield counter (configurable count + min fraction) | Consecutive ineffective-compression counter |
| Config | `proactive_thrash_window` (default 2), `min_proactive_savings` (default 0.10) | Hardcoded: 2 consecutive < 10 % savings → block |
| Scope | Blocks M3 (proactive) only; M0 hygiene and overflow recovery are never blocked | Blocks `should_compress()` entirely; error-triggered path bypassed separately |
| Reset | Cleared by M0 hygiene, overflow recovery, and post-hygiene in `run_turn()` | Reset to 0 on next effective (≥ 10 %) compression |
| Escape hatch | Hygiene fires at higher threshold; overflow always fires | User must run `/new` or `/compress <focus>` |

**co-cli gap:** No guided `/compact <focus>`. co-cli does have a plain `/compact`, but it
always uses the default summarizer prompt and cannot steer preservation priorities the
way hermes's `/compress <focus_topic>` can when anti-thrashing blocks or the user wants
topic-biased retention.

**Gap #2 — Risk: Low. Hermes: `/compress <focus_topic>` parameter passes a topic hint to the summarizer.**

**co-cli note (gate is per-turn, not cross-turn):** The Reset row above is technically
accurate but incomplete. `run_turn()` (`orchestrate.py:565`) resets `consecutive_low_yield_proactive_compactions`
**unconditionally at every turn entry**, not only when M0 actually fires:
```python
message_history = await maybe_run_pre_turn_hygiene(...)
deps.runtime.consecutive_low_yield_proactive_compactions = 0  # always, regardless of whether M0 ran
```
This makes the anti-thrash gate per-turn only — it can accumulate entries only across
`ModelRequestNode` boundaries within a single turn (multi-tool-call chains). A session
where M3 fires once per turn with < 10 % savings on every turn never trips the gate.
The simplified counter implementation makes the control flow easier to see, but the
behavioral limit is unchanged: anti-thrash still does not accumulate across turns.

**Gap #6 — Risk: Low. Hermes: no equivalent. M0 hygiene provides a backstop; within-turn gate still works.**

### 9. Session continuity and post-compaction state

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Session identity | Unchanged — turn_state updated in-place; no new session | New session ID created; old session marked "compression" with `parent_id` link |
| Audit trail | No record that compaction occurred beyond marker in transcript | Full session lineage tree in session DB |
| Memory extraction | Enrichment context passed to summarizer (file paths, todos, prior summaries) | Explicit `_memory_manager.on_pre_compress(messages)` call before rollover |
| Todo handling | Both: todos passed as enrichment context to the summarizer, and `_build_todo_snapshot()` injects a standalone `[ACTIVE TODOS — PRESERVED ACROSS CONVERSATION COMPACTION]` message immediately after the marker (also mirrored by `/compact`) | Todo snapshot injected directly into compressed message list |
| Session rollover | No | Yes — new log file, new DB session, parent link |

**co-cli gap:** No session rollover means the transcript file grows without a clear
marker that "this section is post-compaction-N." The summary marker is in-line but
there is no external record. Hermes's session lineage enables tools like "show me
what changed before and after compaction."

**Gap #5 — Risk: Low. Hermes: new session ID + parent link recorded in session DB on each compaction.**

### 10. Search-tool breadcrumb preservation

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Implementation | `_preserve_search_tool_breadcrumbs()` — keeps `search_tools` returns from dropped range | Not present |
| Dedup | No explicit breadcrumb dedup step; repeated compactions reinsert only the current dropped breadcrumb messages, so one live copy survives without separate marker logic | N/A |
| Rationale | `search_tools` returns contain tool registry entries; LLM needs them to call tools again | hermes tools are always-visible; no discovery-via-search mechanism |

**co-cli note:** This mechanism is critical for co-cli — without it, compaction would
silently break tool discovery for the remainder of the session. Not applicable to hermes
(tools are always-visible there).

### 11. Configurable surface

| Setting group | co-cli (count) | hermes-agent (count) |
|--------------|----------------|----------------------|
| Compaction ratios / thresholds | 7 (`_compaction.py`) | 4 (`compression.*`) |
| LLM budget / context | 4 (`_llm.py`) | 3 (context detection in `model_metadata.py`) |
| Tool result sizing | 2 (`_tools.py`) | 0 (hardcoded constants in `context_compressor.py`) |
| Auxiliary summarizer | 0 | 4 (`auxiliary.compression.*`) |
| Anti-thrashing | 2 configurable in co-cli | 0 configurable in hermes |

**Not a current gap:** same as §2. co-cli intentionally keeps compaction on the
main model in `settings_noreason` mode rather than exposing a second provider /
model surface just for summarization.

---

## Design decisions that appear intentional (not gaps)

- **Single-model summarization (noreason mode):** `summarize_messages` calls `llm_call`
  with `deps.model.settings_noreason`, suppressing extended reasoning/thinking — the
  dominant cost driver. Consistent with co-cli's "single-model architecture" documented
  in `docs/specs/llm-models.md`. An auxiliary model would require a second provider
  config and capability surface.

- **Always-on tool-result pruning (recency-weighted reasoning):** co-cli runs
  dedup + recency clearing before every model request, not only when full
  compaction fires. This is intentional: stale or repeated tool results are
  noise that can cause the model to reason from outdated observations (file
  states, command outputs) rather than the most recent. Hermes's token-budget-
  triggered pruning keeps old results alive until forced to drop them —
  preserving more history at the cost of recency weighting. co-cli's approach
  is a deliberate stance that lean, recent context produces better in-loop
  reasoning.

- **Turn-group compaction unit:** Pydantic-ai's message model naturally pairs
  tool_call/return inside a group, making orphan cleanup unnecessary. This is a
  structural advantage over message-unit approaches that must explicitly align boundaries.
