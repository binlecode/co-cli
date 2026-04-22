# RESEARCH: Context Compaction — co-cli vs hermes-agent Gap Analysis

Scope: deep-code comparison of co-cli's compaction stack against hermes-agent,
identifying gaps in co-cli to adopt or learn from. Complements the broader
peer survey in `RESEARCH-peer-compaction-survey.md`.

Method: full function-body reads of both implementations on 2026-04-21.
Last co-cli sync: 2026-04-21 (third pass — gap #15 closed, min_context_length guard shipped, docstring gap #14 confirmed open).

co-cli sources (read):
- `co_cli/context/_history.py`
- `co_cli/context/summarization.py`
- `co_cli/context/orchestrate.py`
- `co_cli/context/tool_categories.py`
- `co_cli/config/_compaction.py`, `_llm.py`, `_tools.py`
- `co_cli/tools/tool_io.py`
- `co_cli/agent/_core.py`

hermes-agent sources (read):
- `agent/context_compressor.py`
- `run_agent.py`
- `gateway/run.py`
- `hermes_cli/config.py`
- `agent/model_metadata.py`
- `agent/context_engine.py`

---

## Architecture Overview

### co-cli: five-mechanism pipeline

```
M0  Pre-turn hygiene (88 % threshold) ─ fires before agent loop
    Token count: max(char estimate, prior-turn API-reported tokens) — same dual-source as M3
    When threshold exceeded: calls summarize_history_window() → LLM summary or static marker

M1  Emit-time cap ─ tool result → disk at persist time, never re-enters context
    Thresholds (ToolInfo.max_result_size > result_persist_chars default = 50 K):
      30 K chars shell; file_read = math.inf (never spills — prevents persist→read→persist loop)
    file_read in-tool caps (shipped 2026-04-21, co_cli/tools/files/read.py):
      500-line default when no range given (+ continuation hint when more lines remain)
      2000-line hard ceiling on any explicit range
      2000-char per-line truncation with ...[truncated] marker
      500 KB file size gate blocks full-file reads with no range (explicit ranges proceed)

M2a Truncate tool results (always-on, every turn, no LLM):
    Boundary: last turn start (_find_last_turn_start — last UserPromptPart boundary)
    Keep 5 most-recent returns per compactable tool type (file_read, shell, file_grep, file_glob,
    web_search, web_fetch, knowledge_article_read, obsidian_read); clear older with
    static placeholder "[tool result cleared — older than 5 most recent calls]"
    Does NOT touch ToolCallPart args or non-compactable tools
    Recency-weighting rationale: stale tool outputs cause model reasoning drift

M2b Batch budget enforcement (always-on, every turn, no LLM):
    Targets current turn's tool batch only (ToolReturnParts after last ToolCallPart)
    If aggregate > 200 K chars: spill largest non-persisted result to disk, repeat
    No equivalent in hermes

M3  Window compaction (token-pressure-driven, LLM call):
    Trigger: max(proactive_ratio × budget, min_context_length_tokens)
             = max(0.75 × budget, 64 K) — matches hermes two-way floor semantics
    Anti-thrash: savings ring buffer — blocked if last N runs saved < 10 % each
    Boundary planner (shared with ER):
      1. head_end = find_first_run_end (first TextPart/ThinkingPart response) + 1
      2. group_by_turn — groups by UserPromptPart boundaries
      3. Walk groups from tail accumulating tokens; stop before tail_fraction × budget (0.40)
         _MIN_RETAINED_TURN_GROUPS = 1: last group always retained even if it exceeds budget
         soft_overrun_multiplier = 1.25: allow 1.25 × tail_fraction × budget for last group before hard accept
      4. _anchor_tail_to_last_user: extend tail_start to cover last UserPromptPart
    LLM summarizes dropped middle → structured summary injected as marker message
    Static marker fallback when circuit breaker trips (≥ 3 failures; probe every 10 skips)

ER  Overflow recovery (provider HTTP 400/413):
    Two attempts via same boundary planner: (1) normal tail_fraction; (2) tail_fraction / 2
    emergency_compact (static first+last+marker, no LLM) defined but NOT wired to production
```

All of M2a–M3 run as pydantic-ai history processors before each `ModelRequestNode`.
M1 runs at tool-return time, outside the processor chain. M0 runs at turn entry,
also outside the chain.

### hermes-agent: two-layer defence + four-phase algorithm

```
Layer 1  Gateway hygiene (85 %) ─ fires pre-agent, rough char estimate
Layer 2  Agent ContextCompressor (50 %) ─ in-loop, API-reported tokens

Phase 1  Tool result pruning (full message list, no LLM):
           Pass 1 — hash-based dedup: identical results → back-reference string
           Pass 2 — semantic condensing: results > 200 chars → 1-line description
                    (tool name + first 2 args + char count; no LLM)
           Pass 3 — tool call arg truncation: args > 500 chars → first 200 + "...[truncated]"
           Boundary: token-budget-driven (walk backward from tail); count floor = protect_last_n
           Scope: live message list modified in-place
           No equivalent for current-turn batch aggregate cap (co-cli M2b)
Phase 2  Boundary planning ─ token-budget tail protection
Phase 3  LLM summarization:
           _serialize_for_summary caps each message at 6 K chars (4 K head + 1.5 K tail)
           before feeding to the LLM — summarizer input only, does NOT modify live messages
           Auxiliary model (configurable), iterative updates on recompression
Phase 4  Assembly ─ head + summary + tail, orphaned-pair cleanup, session rollover
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

~~**co-cli gap:** No minimum-context guard at startup.~~ **SHIPPED + CORRECTED:** `min_context_length_tokens`
(default 64 K) is now a floor on the trigger threshold —
`threshold = max(budget × proactive_ratio, min_context_length_tokens)` — matching
hermes's semantics. Earlier impl used it as a binary budget gate (`if budget < 64K: skip`), which is
a weaker invariant: for models with budget > 64K the gate never fired, and the threshold could
still fall below 64K on low proactive_ratio configs. Architecture overview updated.

~~**co-cli note (stale token count after same-turn compaction):**~~ **SHIPPED:** `compacted_in_current_turn`
flag in `CoRuntimeState` closes this. `summarize_history_window` sets the flag on compaction
(`_history.py:826`); the next invocation zeroes out the API-reported count:
`reported = 0 if ctx.deps.runtime.compacted_in_current_turn else latest_response_input_tokens(messages)`.
Spurious `plan_compaction_boundaries` calls no longer occur. See gap #15 — closed.

### 2. Summarization model

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Model | Always `deps.model.settings_noreason` (main model, no reasoning) | Configurable auxiliary model (`auxiliary.compression.model`) |
| Fallback | Static marker on failure | Fall back to main model on 404/503 |
| Separate endpoint | No | Yes — `auxiliary.compression.base_url`, `provider` |
| Timeout | Inherits LLM client default | Configurable (default 120 s) |

**Rejected (not a gap):** `summarize_messages` calls `llm_call` with
`deps.model.settings_noreason`, which disables extended reasoning/thinking — the
dominant cost driver for large models. The auxiliary-model comparison is also
inconsistent with co-cli's single-model architecture (see Design decisions below).
The "burns a full LLM slot" framing in the original draft was inaccurate.

### 3. Iterative recompression / summary updates

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| On second compaction | Re-summarizes fresh; prior summaries injected as context | Updates prior summary in-place: moves "In Progress" → "Completed Actions", "Pending User Asks" → "Resolved Questions", refreshes "Active Task" |
| Prior summary detection | `_gather_prior_summaries()` — finds marker prefix, appends as `## Additional Context` | Full prompt section "Prior Summary" — LLM is told to merge and evolve |
| Risk | Re-summarizing from scratch can contradict or lose prior summary facts | State machine is driven by LLM fidelity; imprecise merges possible |

**co-cli gap:** Iterative evolution avoids contradiction between summaries that cover
overlapping ranges. co-cli nests prior summaries as context but may lose the distinction
between "resolved" and "pending" across compression cycles.

### 4. Tool result handling before summarization

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Pre-LLM cheap pass | M2a (keep 5 most-recent per compactable tool — set: `file_read`, `shell`, `file_grep`, `file_glob`, `web_search`, `web_fetch`, `knowledge_article_read`, `obsidian_read`) + M2b (batch spill) — both sync, no LLM | Phase 1 `_prune_old_tool_results()` — collapses old tool results to semantic 1-line descriptions |
| Semantic condensing | No — placeholders are static strings like "tool result cleared" | Yes — generates compact human-readable summaries e.g. "`[terminal] ran npm test → exit 0, 47 lines output`" |
| Deduplication | No | Hash-based dedup of identical tool result content |
| Max result threshold | M1 (per-tool): 30 K shell, 50 K default, `math.inf` file_read — no cap | 6 K per-message serialization cap for summarizer input |

**co-cli gap (summarization fidelity only):** When M3 fires, the static placeholder
`"[tool result cleared — older than 5 most recent calls]"` gives the summarizer no
signal about what those calls returned. Hermes's semantic 1-line descriptions
(`[shell] ran npm test → exit 0, 47 lines output`) preserve intent without token cost,
producing richer summaries. This gap is scoped to summarization quality — M2a's
always-on pruning is intentional (see Design decisions below).

**co-cli gap:** No deduplication of identical tool results. Repeated `file_read` of
the same file across many turns accumulates token cost unnecessarily.

### 5. Summary structure and handoff framing

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Sections | 7 optional sections (Goal, Key Decisions, Errors & Fixes, Working Set, Progress, Next Step, User Corrections) | 11 required sections (Active Task, Goal, Constraints & Preferences, Completed Actions, Active State, In Progress, Blocked, Key Decisions, Resolved Questions, Pending User Asks, Relevant Files, Remaining Work, Critical Context) |
| Active task preservation | `## Next Step` with verbatim quote when task in progress | `## Active Task`: verbatim copy of most-recent unfulfilled request; updated on recompression |
| Resolved vs pending questions | No distinction | Explicitly separated (`Resolved Questions` vs `Pending User Asks`) |
| Handoff framing | "This session is being continued from a previous conversation that ran out of context. Recent messages are preserved verbatim." | Defensive: "treat as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary" |
| Security/re-execution guard | Yes — adversarial-content CRITICAL SECURITY RULE in system prompt | Yes — SUMMARY_PREFIX explicitly says "do NOT fulfill requests"; also in summarizer preamble |

**co-cli gap:** No explicit "Resolved Questions" vs "Pending User Asks" distinction.
When a summary spans multiple open questions, the LLM resuming may not know which were
already answered. Hermes's split is load-bearing for multi-step interactive sessions.

**co-cli gap:** The handoff framing is purely informational ("continued from a previous
conversation"). Hermes's prefix is actively defensive — it tells the LLM not to re-do
listed work. This matters when the summary contains successful shell commands or file
writes that must not be repeated.

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
| Overflow recovery | Two-attempt per turn: (1) normal `tail_fraction`; (2) `tail_fraction / 2` if (1) returns None. Second overflow in same turn → unrecoverable. `emergency_compact` (static first+last+marker, no LLM) is defined in `_history.py` but NOT wired into the production path — currently used only in tests. | Single attempt; falls back to static text |

**co-cli gap:** Failure count is a monotone counter; recovery resets to 0. If provider
issues are intermittent, a string of failures could push count to very high values,
making probes sparse (`count-3` growing). Hermes's time-based cooldown is bounded.

**co-cli gap:** `_CONTEXT_OVERFLOW_PATTERNS` in `orchestrate.py` (`"prompt is too long"`,
`"context_length_exceeded"`, `"maximum context length"`) covers Ollama/OpenAI-compatible
error bodies only. Gemini (now a supported provider) returns 400s with different wording
— e.g. `"Request payload size exceeds the limit"` or `"Input token count exceeds the
maximum"`. `_is_context_overflow` returns False for Gemini context overflows, so the
turn falls through to the **reformulation handler** (`orchestrate.py:661`), not generic
error handling. Full failure sequence:
1. Gemini returns HTTP 400 (context length exceeded).
2. `_is_context_overflow` → False; falls to `if code == 400 and tool_reformat_budget > 0`.
3. Reformulation handler injects `UserPromptPart("Your previous tool call was rejected: [token error]")` into `turn_state.current_history` — **making the history longer**.
4. Retries; same 400. Budget decremented twice total (default budget = 2).
5. Budget exhausted → falls to `frontend.on_status(f"Provider error (HTTP {code}): {e.body}")` — generic unhelpful message.
6. `_build_error_turn_result` returns `turn_state.current_history` (since `latest_result` is None after all failed segments). The 2 injected reformulation `UserPromptPart`s are **persisted into the REPL's `message_history`** for subsequent turns, contaminating the model's context.

The design invariant comment at `orchestrate.py:633` — `"NEVER falls through to the 400 reformulation handler"` for context overflow — is violated for Gemini. Regression introduced when Gemini provider support was added.

### 8. Anti-thrashing

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Mechanism | Savings ring buffer (configurable window + min fraction) | Consecutive ineffective-compression counter |
| Config | `proactive_thrash_window` (default 2), `min_proactive_savings` (default 0.10) | Hardcoded: 2 consecutive < 10 % savings → block |
| Scope | Blocks M3 (proactive) only; M0 hygiene and overflow recovery are never blocked | Blocks `should_compress()` entirely; error-triggered path bypassed separately |
| Reset | Cleared by M0 hygiene, overflow recovery, and post-hygiene in `run_turn()` | Reset to 0 on next effective (≥ 10 %) compression |
| Escape hatch | Hygiene fires at higher threshold; overflow always fires | User must run `/new` or `/compress <focus>` |

**co-cli gap:** No guided `/compact <focus>`. Hermes's `/compress <focus_topic>` lets users
direct what to preserve when anti-thrashing blocks. co-cli users have no escape short of `/new`.

**co-cli note (gate is per-turn, not cross-turn):** The Reset row above is technically
accurate but incomplete. `run_turn()` (`orchestrate.py:588`) clears `recent_proactive_savings`
**unconditionally at every turn entry**, not only when M0 actually fires:
```python
message_history = await maybe_run_pre_turn_hygiene(...)
deps.runtime.recent_proactive_savings.clear()  # always, regardless of whether M0 ran
```
This makes the anti-thrash gate per-turn only — it can accumulate entries only across
`ModelRequestNode` boundaries within a single turn (multi-tool-call chains). A session
where M3 fires once per turn with < 10 % savings on every turn never trips the gate.
The `CoRuntimeState` docstring (`deps.py:144`) says `recent_proactive_savings`
"persists across turns within a session" — this is misleading for the proactive savings
field specifically. The behavioral analysis here is confirmed correct; the docstring fix
is still pending in source.

### 9. Session continuity and post-compaction state

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Session identity | Unchanged — turn_state updated in-place; no new session | New session ID created; old session marked "compression" with `parent_id` link |
| Audit trail | No record that compaction occurred beyond marker in transcript | Full session lineage tree in session DB |
| Memory extraction | Enrichment context passed to summarizer (file paths, todos, prior summaries) | Explicit `_memory_manager.on_pre_compress(messages)` call before rollover |
| Todo handling | Todos passed as enrichment context to summarizer | Todo snapshot injected directly into compressed message list |
| Session rollover | No | Yes — new log file, new DB session, parent link |

**co-cli gap:** No session rollover means the transcript file grows without a clear
marker that "this section is post-compaction-N." The summary marker is in-line but
there is no external record. Hermes's session lineage enables tools like "show me
what changed before and after compaction."

**co-cli gap:** Todos are passed to the summarizer as enrichment (informing the
summary text) but are not injected as a standalone message post-compaction. If the
summary omits a todo, it is lost from the active context. Hermes appends the todo
snapshot unconditionally.

### 10. Search-tool breadcrumb preservation

| Aspect | co-cli | hermes-agent |
|--------|--------|--------------|
| Implementation | `_preserve_search_tool_breadcrumbs()` — keeps `search_tools` returns from dropped range | Not present |
| Dedup | Object-identity dedup prevents quadratic accumulation | N/A |
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

**co-cli gap:** No config for auxiliary summarizer. Single knob for per-tool result
size; no per-provider or per-model tuning of the summarizer.

---

## Summary of co-cli Gaps by Priority

| # | Gap | Hermes approach | Risk if not addressed |
|---|-----|-----------------|----------------------|
| 1 | ~~No auxiliary summarization model~~ — **REJECTED**: `settings_noreason` already suppresses reasoning cost; single-model architecture is intentional | `auxiliary.compression.model` config | N/A |
| 2 | Static tool-result truncation placeholders | Semantic 1-line descriptions in Phase 1 | Medium — summarizer gets less signal from cleared older turns |
| 3 | No resolved/pending question split in summary | `Resolved Questions` vs `Pending User Asks` sections | Medium — LLM may re-answer already-resolved questions after compaction |
| 4 | Weak handoff framing | Defensive `SUMMARY_PREFIX` with explicit "do NOT re-execute" | Medium — risk of repeated tool calls (writes, shells) described in summary |
| 5 | No tool result deduplication | Hash-based dedup in Phase 1 | Low-medium — repeated reads accumulate token cost; common in file-heavy sessions |
| 6 | No session rollover / compaction audit trail | New session ID + parent link | Low — no external record of compaction events; harder to debug |
| 7 | Todos injected as enrichment, not standalone | Unconditional todo snapshot appended to compressed messages | Low — todo loss on summarizer omission is possible |
| 8 | No guided `/compact <focus>` | `/compress <focus_topic>` parameter | Low — users cannot direct what to preserve when anti-thrashing blocks |
| 9 | No iterative summary evolution on recompression | LLM merges prior summary with new progress | Low — duplicate or contradicting summaries possible across compression cycles |
| 10 | Monotone failure counter — probes grow sparse | Time-based cooldown is bounded | Low — intermittent provider issues push probe cadence increasingly far apart |
| 11 | `emergency_compact` defined but unwired | N/A — no equivalent; static fallback is production | Low — both LLM recovery attempts failing leaves session in error state rather than degrading gracefully |
| 12 | Gemini context overflow not detected — `_CONTEXT_OVERFLOW_PATTERNS` covers Ollama/OpenAI error bodies only; Gemini 400s fall into the reformulation handler, injecting 2 error `UserPromptPart`s into history (making it longer), exhausting `tool_reformat_budget`, and persisting those messages into subsequent turns via `_build_error_turn_result`. Design invariant "NEVER falls through to 400 reformulation handler" violated for Gemini. | N/A | High — Gemini sessions hitting context limit get worse history, confusing model on next turn, no compaction attempted |
| 13 | ~~`file_read` no safety-net size limit~~ — **SHIPPED 2026-04-21**: four in-tool caps added (500-line default, 2000-line ceiling, 2000-char/line, 500 KB gate); `max_result_size` stays `math.inf` to prevent persist→read→persist loop | N/A | ~~Medium~~ — resolved |
| 14 | Anti-thrash gate is per-turn only — `run_turn()` unconditionally clears `recent_proactive_savings` at every turn entry; cross-turn thrashing (repeated low-yield M3 runs across turns) is never blocked | N/A | Low — M0 hygiene provides a backstop for true thrashing scenarios; within-turn gate still works |
| 15 | ~~Stale `latest_response_input_tokens` after same-turn compaction~~ — **SHIPPED:** `compacted_in_current_turn` flag (`_history.py:826`) zeroes out the API-reported count on the next invocation, preventing spurious `plan_compaction_boundaries` calls | N/A | ~~Low~~ — resolved |

---

## Design decisions that appear intentional (not gaps)

- **Single-model summarization (noreason mode):** `summarize_messages` calls `llm_call`
  with `deps.model.settings_noreason`, suppressing extended reasoning/thinking — the
  dominant cost driver. Consistent with co-cli's "single-model architecture" documented
  in `docs/specs/llm-models.md`. An auxiliary model would require a second provider
  config and capability surface.

- **Always-on M2a pruning (recency-weighted reasoning):** M2a runs before every model
  request, not only when compaction fires. This is intentional: stale tool results are
  noise that can cause the model to reason from outdated observations (file states,
  command outputs) rather than the most recent. Hermes's token-budget-triggered pruning
  keeps old results alive until forced to drop them — preserving more history at the
  cost of recency weighting. co-cli's approach is a deliberate stance that lean,
  recent context produces better in-loop reasoning.

- **Turn-group compaction unit:** Pydantic-ai's message model naturally pairs
  tool_call/return inside a group, making orphan cleanup unnecessary. This is a
  structural advantage over message-unit approaches that must explicitly align boundaries.
