# RESEARCH: co-cli Compaction Flow Audit

Date: 2026-04-23

Scope: read-only end-to-end trace of co-cli's compaction flow to identify logical
defects, functional gaps, and common happy, edge, and negative paths.

Sources read:
- `co_cli/agent/_core.py`
- `co_cli/context/_history.py`
- `co_cli/context/summarization.py`
- `co_cli/context/orchestrate.py`
- `co_cli/context/_http_error_classifier.py`
- `co_cli/context/transcript.py`
- `co_cli/context/session.py`
- `co_cli/commands/_commands.py`
- `co_cli/knowledge/_distiller.py`
- `co_cli/tools/tool_io.py`
- `co_cli/agent/_native_toolset.py`
- `co_cli/tools/categories.py`
- `co_cli/context/_dedup_tool_results.py`
- `co_cli/llm/_factory.py`
- `docs/specs/compaction.md`
- `docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md`
- `tests/context/test_history.py`
- `tests/context/test_context_compaction.py`
- `tests/context/test_transcript.py`

Working tree note: `uv.lock` was already modified before this audit and was not
touched.

## End-to-End Flow

1. Startup registers request-time history processors on the orchestrator agent:
   `dedup_tool_results`, `truncate_tool_results`, `enforce_batch_budget`, and
   `summarize_history_window`.
   Source: `co_cli/agent/_core.py:142-147`.

2. Each foreground turn starts in `run_turn()`. It resets per-turn runtime state,
   then calls `maybe_run_pre_turn_hygiene()` before the agent loop begins.
   Source: `co_cli/context/orchestrate.py:568-574`.

3. Tool output can be bounded before it enters message history. `tool_output()`
   reads the tool's `max_result_size` metadata or falls back to
   `config.tools.result_persist_chars`; oversized display strings are persisted
   through `persist_if_oversized()` and replaced with a preview placeholder.
   Source: `co_cli/tools/tool_io.py:204-225`.

4. Before every model request, pydantic-ai runs the history processor chain:
   - `dedup_tool_results()` collapses older identical compactable tool returns to
     back-reference markers while preserving original `tool_call_id`.
     Source: `co_cli/context/_history.py:424-457`.
   - `truncate_tool_results()` protects the last user turn and semantic-markers
     compactable returns older than the five most recent per tool type.
     Source: `co_cli/context/_history.py:524-556`.
   - `enforce_batch_budget()` spills the current tool-return batch largest-first
     when aggregate content exceeds `config.tools.batch_spill_chars`.
     Source: `co_cli/context/_history.py:625-677`.
   - `summarize_history_window()` performs token-pressure window compaction.
     Source: `co_cli/context/_history.py:998-1073`.

5. Window compaction resolves a budget, computes token pressure as
   `max(estimate_message_tokens(messages), latest_response_input_tokens(messages))`,
   and triggers above `max(int(budget * proactive_ratio),
   min_context_length_tokens)`. If compaction already ran this turn, the stale
   API-reported count is suppressed by using reported `0`.
   Source: `co_cli/context/_history.py:1023-1037`.

6. The boundary planner keeps the head through the first substantive model
   response, groups history by user turns, walks backward to retain a tail sized
   by `tail_fraction * budget`, always keeps at least the last group, anchors the
   latest user prompt into the tail, and returns `None` on head/tail overlap.
   Source: `co_cli/context/_history.py:253-308`.

7. Compaction gathers side-channel context for the summarizer from dropped-range
   file tool args, active session todos, and prior summary markers. The context is
   capped at 4000 chars.
   Source: `co_cli/context/_history.py:693-784`.

8. The summarizer uses `summarize_messages()`, a no-tools `llm_call()` with a
   handoff-summary prompt and a security system prompt that frames history as data
   to summarize, not instructions to execute.
   Source: `co_cli/context/summarization.py:112-228`.

9. If summarization succeeds, `_summary_marker()` wraps the summary in a
   defensive continuation envelope. If there is no model, the circuit breaker is
   active, or summarization fails, `_static_marker()` is used instead.
   Sources: `co_cli/context/_history.py:173-221`, `co_cli/context/_history.py:822-861`.

10. `_apply_compaction()` assembles:
    `head + marker + optional todo snapshot + search_tools breadcrumbs + tail`.
    It sets `history_compaction_applied` and `compacted_in_current_turn`, then
    schedules extract-before-discard knowledge extraction.
    Source: `co_cli/context/_history.py:876-906`.

11. Provider context-overflow errors are classified by `is_context_overflow()`.
    HTTP 413 is always overflow; HTTP 400 requires explicit overflow evidence in
    the body.
    Source: `co_cli/context/_http_error_classifier.py:40-52`.

12. Overflow recovery is one-shot per turn. The turn loop materializes pending
    user input into history, runs planner-based `recover_overflow_history()`, then
    falls back to `emergency_recover_overflow_history()` if planning returns
    `None`. On success it sets `current_input = None` and retries once.
    Sources: `co_cli/context/orchestrate.py:617-642`,
    `co_cli/context/orchestrate.py:517-539`.

13. `emergency_recover_overflow_history()` bypasses budget planning and LLM
    summarization. It keeps first group, static marker, optional todo snapshot,
    dropped-range `search_tools` breadcrumbs, and last group. It returns `None`
    when there are two or fewer groups.
    Source: `co_cli/context/_history.py:954-990`.

14. Post-turn finalization branches compacted history to a child transcript when
    `history_compaction_applied` is true. Normal turns append only the unpersisted
    tail.
    Sources: `co_cli/main.py:131-143`, `co_cli/context/transcript.py:87-111`.

15. Manual `/compact` summarizes the entire current history into one compaction
    marker, optionally appends a todo snapshot, appends an assistant ack response,
    schedules compaction extraction, and returns `ReplaceTranscript`.
    Source: `co_cli/commands/_commands.py:329-387`.

## Logical Defects

### 1. Pre-turn hygiene is effectively disabled in production

`run_turn()` passes `agent.model` into `maybe_run_pre_turn_hygiene()`:
`co_cli/context/orchestrate.py:569-573`.

`maybe_run_pre_turn_hygiene()` reads `model.context_window`:
`co_cli/context/_history.py:1094`.

In production, `agent.model` is the raw pydantic-ai model, not the co-cli
`LlmModel` wrapper. Local introspection showed `build_agent(...).model` is an
`OpenAIChatModel` and has no `context_window` attribute. The broad exception
handler then logs and returns the original history unchanged:
`co_cli/context/_history.py:1110-1112`.

Impact: M0 pre-turn hygiene does not run on the normal foreground path. Token
pressure is handled later by request-time M3 or provider overflow recovery.

Likely fix direction: pass `deps.model` or a context-window integer into
`maybe_run_pre_turn_hygiene()` instead of `agent.model`, or make the hygiene
function resolve budget directly from `deps.model`.

### 2. `search_tools` breadcrumb preservation can leak non-search orphan returns

`_preserve_search_tool_breadcrumbs()` keeps an entire `ModelRequest` if any part
is a `ToolReturnPart` named `search_tools`:
`co_cli/context/_history.py:864-873`.

The documented invariant is narrower: preserve `search_tools` breadcrumbs only,
because they are intentionally orphaned from their dropped `ToolCallPart`.

If a parallel batch contains a `search_tools` return and another tool return in
the same `ModelRequest`, the whole request is preserved. The non-`search_tools`
return may become an orphan because its matching `ToolCallPart` was dropped.

Impact: provider/tool-call validation risk if mixed tool-result batches occur.

Likely fix direction: rebuild preserved breadcrumb messages with only the
`search_tools` `ToolReturnPart` entries instead of retaining the whole
`ModelRequest`.

### 3. Compaction extraction can skip content it is meant to rescue

`schedule_compaction_extraction()` reads the current extraction cursor, schedules
background extraction over the pre-compact tail, then immediately pins
`last_extracted_message_idx` to `len(post_compact)`:
`co_cli/knowledge/_distiller.py:227-238`.

`fire_and_forget_extraction()` skips scheduling when another extraction is
already in flight:
`co_cli/knowledge/_distiller.py:190-192`.

If compaction happens while another extraction task is still running, the
pre-compact content can be discarded from future extraction while the cursor is
still advanced as if extraction happened.

Impact: knowledge artifacts from soon-to-be-dropped history can be lost.

Likely fix direction: make compaction extraction queue or merge pending deltas,
or avoid advancing the cursor when scheduling is skipped.

**Status:** addressed by exec-plan
`docs/exec-plans/active/2026-04-23-234616-sync-compaction-extraction.md` —
replaces the shared single-flight launcher with `extract_at_compaction_boundary()`,
an awaited compaction-boundary path that drains any in-flight cadence task
before extracting and pinning the cursor.

### 4. Transcript write failure can corrupt persistence accounting

`_finalize_turn()` catches `OSError` from `persist_session_history()` and surfaces
a status message:
`co_cli/main.py:131-142`.

It then unconditionally sets:
`deps.session.persisted_message_count = len(turn_result.messages)`
at `co_cli/main.py:143`.

If compaction caused a child transcript branch and that write failed, the session
can believe compacted messages were persisted even though they were not. Later
normal appends can skip messages based on the advanced count.

Impact: possible transcript data loss after a persistence failure.

Likely fix direction: advance `persisted_message_count` only when
`persist_session_history()` succeeds.

## Functional Gaps

### 1. Manual `/compact` does not share automatic compaction's degradation behavior

Manual `/compact` catches `ModelHTTPError` and `ModelAPIError`, prints provider
failure, and returns without changing history:
`co_cli/commands/_commands.py:353-363`.

Unlike automatic compaction, it does not use:
- static marker fallback
- circuit breaker state
- personality-aware summarizer addendum
- dropped-range enrichment
- `search_tools` breadcrumb preservation

It does preserve active todos via `_build_todo_snapshot()`.

Impact: the user-visible escape hatch can fail in cases where automatic
compaction would continue with a static marker.

### 2. Re-compaction has no dedicated iterative-update prompt

The current prompt tells the summarizer to integrate prior summaries and move
pending questions to resolved questions when applicable:
`co_cli/context/summarization.py:148-153`.

There is no separate prompt mode that treats the previous summary as ground
truth and asks only for an update. Existing research still lists this as a low
risk gap.

Impact: repeated compactions rely on general summarization discipline, which may
increase drift risk.

### 3. `search_tools` breadcrumbs have no cap or relevance filtering

`_preserve_search_tool_breadcrumbs()` retains all matching dropped-range
breadcrumb messages:
`co_cli/context/_history.py:864-873`.

There is no cap, `tool_call_id` dedup, or filtering to currently relevant
capability sets.

Impact: likely low today, but stale tool discovery state can survive across
multiple compactions and consume context.

### 4. Trigger estimation does not include static prompt or tool-schema tokens

`estimate_message_tokens()` counts message part content and `ToolCallPart.args`,
but not static instructions, dynamic instructions, or tool schemas:
`co_cli/context/summarization.py:35-60`.

Provider-reported usage often compensates after one request, but local-only or
provider-no-usage paths can under-trigger for tool-heavy requests.

Impact: context overflow recovery remains important because proactive compaction
can still miss true request size.

## Common Happy Paths

### Small normal turn

1. `run_turn()` resets runtime state.
2. Pre-turn hygiene should no-op below threshold.
3. M2 processors no-op or lightly trim older history.
4. M3 sees token count below threshold and returns messages unchanged.
5. Provider request succeeds.
6. `_finalize_turn()` appends the new transcript tail.

### Large single tool output

1. Tool returns a display string larger than its threshold.
2. `tool_output()` persists full output to `tool-results`.
3. Model sees a preview placeholder.
4. Later processors operate on the smaller placeholder instead of the full
   output.

### Long multi-turn session with proactive compaction

1. Request-time M3 token pressure exceeds threshold.
2. Planner selects head, dropped middle, and recent tail.
3. Summarizer succeeds.
4. `_apply_compaction()` inserts summary marker, optional todo snapshot,
   `search_tools` breadcrumbs, and tail.
5. Runtime flags mark compaction as applied.
6. Turn proceeds under a smaller context.

### Summarizer unavailable but compaction possible

1. M3 token pressure exceeds threshold.
2. Planner returns valid boundaries.
3. `ctx.deps.model` is missing, the circuit breaker skips, or the LLM raises.
4. Static marker replaces dropped middle.
5. The turn continues.

### Overflow after estimator miss

1. Provider returns HTTP 413 or HTTP 400 with overflow evidence.
2. `run_turn()` materializes pending input into history.
3. Planner-based recovery compacts and retries.
4. If planning fails, structural emergency recovery keeps first and last groups.
5. Retry succeeds.

### User-invoked `/compact`

1. User runs `/compact` or `/compact <focus>`.
2. Whole current history is summarized.
3. New history becomes compaction marker, optional todo snapshot, and ack
   response.
4. Command outcome persists a child transcript.

## Common Edge Paths

### Last turn exceeds tail budget

The planner still keeps the last turn group because `_MIN_RETAINED_TURN_GROUPS`
is hardcoded to `1`. This preserves the active user turn even if it alone
exceeds `tail_fraction * budget`.

Source: `co_cli/context/_history.py:92-98`, `co_cli/context/_history.py:293-299`.

### Head/tail overlap

When all turn groups fit into the planned tail, `tail_start <= head_end` and the
planner returns `None`.

Source: `co_cli/context/_history.py:306-308`.

Proactive M3 returns messages unchanged. Overflow recovery can then use
structural emergency fallback.

### No model on compaction path

Automatic compaction uses a static marker when `ctx.deps.model` is missing.
This is expected for sub-agent or reduced-deps contexts.

Source: `co_cli/context/_history.py:829-831`.

### Circuit breaker trip and probe

After three consecutive summarizer failures, compaction skips LLM summarization
and uses static markers. The failure count keeps increasing, and probes occur on
the configured cadence.

Source: `co_cli/context/_history.py:809-819`.

### Active todos

Active `pending` and `in_progress` todos are included in summarizer enrichment
and also inserted as a standalone snapshot after the compaction marker.

Sources: `co_cli/context/_history.py:720-741`,
`co_cli/context/_history.py:893-900`.

### Re-compaction over existing summary and todo snapshot

Prior summary text is gathered from the dropped range and passed as additional
context. A fresh todo snapshot is rebuilt from session state. Tests assert that
re-compaction does not duplicate todo snapshots.

Sources: `co_cli/context/_history.py:744-756`,
`tests/context/test_history.py:987-1014`.

## Common Negative Paths

### First-turn or two-group overflow

If there are two or fewer turn groups, structural emergency recovery returns
`None`; there is no middle range to drop.

Source: `co_cli/context/_history.py:968-970`.

Outcome: terminal context overflow.

### Second overflow in the same turn

`turn_state.overflow_recovery_attempted` gates overflow recovery to one attempt
per turn. A second overflow after retry is terminal.

Source: `co_cli/context/orchestrate.py:620-642`.

### Manual `/compact` summarizer provider failure

Manual `/compact` prints "Compact failed" and leaves history unchanged when the
summarizer raises a provider API error.

Source: `co_cli/commands/_commands.py:357-363`.

### Manual `/compact` unexpected exception

Manual `/compact` catches only `ModelHTTPError` and `ModelAPIError`.
Unexpected exceptions bubble to the outer chat loop.

Source: `co_cli/commands/_commands.py:353-357`.

### Extraction already in flight during compaction

The compaction hook can skip scheduling extraction, then still advance the
cursor. This can drop pre-compact content from future knowledge extraction.

Sources: `co_cli/knowledge/_distiller.py:190-192`,
`co_cli/knowledge/_distiller.py:227-238`.

### Transcript child write fails after compaction

The user sees a status message, but persistence counters advance anyway, so
later writes can skip compacted messages.

Source: `co_cli/main.py:131-143`.

## Notes On Stale Research

`docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md` contains some stale
claims about overflow recovery:
- It says production does not wire the structural emergency fallback.
- It says overflow recovery has two planner attempts with `tail_fraction / 2`.

The live code now uses one planner-based attempt and then
`emergency_recover_overflow_history()`:
`co_cli/context/orchestrate.py:531-537`.

This audit treats the live implementation as source of truth.

## Verification Performed

This was a code-trace audit, not a test run. No pytest suite was executed.

One local introspection command verified that `build_agent(config=settings).model`
is a raw `OpenAIChatModel`, has no `context_window` attribute, and therefore does
not match the object shape expected by `maybe_run_pre_turn_hygiene()`.
