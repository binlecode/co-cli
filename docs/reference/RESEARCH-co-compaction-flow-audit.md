# RESEARCH: co-cli Compaction Flow Audit

Date: 2026-04-25

Scope: read-only end-to-end trace of co-cli's current compaction flow to identify
logical defects, functional gaps, and common happy, edge, and negative paths.

Working tree note: this refresh treats the uncommitted workspace as the latest
source of truth. Pre-existing changes were present in compaction code, specs,
tests, this reference doc, and `uv.lock`; only this reference doc was edited.

## Sources Read

- `co_cli/agent/_core.py`
- `co_cli/context/compaction.py`
- `co_cli/context/_compaction_boundaries.py`
- `co_cli/context/_compaction_markers.py`
- `co_cli/context/_history_processors.py`
- `co_cli/context/_dedup_tool_results.py`
- `co_cli/context/_http_error_classifier.py`
- `co_cli/context/summarization.py`
- `co_cli/context/orchestrate.py`
- `co_cli/commands/_commands.py`
- `co_cli/knowledge/_distiller.py`
- `co_cli/main.py`
- `co_cli/memory/transcript.py`
- `co_cli/tools/tool_io.py`
- `docs/specs/compaction.md`
- `docs/specs/memory-knowledge.md`
- `tests/context/test_context_compaction.py`
- `tests/context/test_history.py`
- `tests/memory/test_transcript.py`

## Current Verdict

The compaction stack has moved from the old monolithic `_history.py` shape into
a split implementation:

- Public entry surface: `co_cli/context/compaction.py`
- Boundary planner: `co_cli/context/_compaction_boundaries.py`
- Marker and enrichment helpers: `co_cli/context/_compaction_markers.py`
- Request-time processors: `co_cli/context/_history_processors.py`
- Token estimation and summarizer call: `co_cli/context/summarization.py`

Prior audit findings around pre-turn hygiene, compaction-boundary extraction,
and manual `/compact` degradation are now addressed in code. The live high-risk
remaining issue is transcript persistence accounting after a write failure. A
smaller provider-validation risk remains around `search_tools` breadcrumb
preservation retaining whole mixed `ModelRequest`s.

## End-to-End Flow

1. Startup builds the orchestrator agent with four request-time history
   processors in order: `dedup_tool_results`, `truncate_tool_results`,
   `enforce_batch_budget`, and `summarize_history_window`.
   Source: `co_cli/agent/_core.py:135-148`.

2. Each foreground turn starts in `run_turn()`. Runtime state resets, then
   pre-turn hygiene runs with `deps` and a provider-reported token count read
   from existing history before the first stream segment begins.
   Source: `co_cli/context/orchestrate.py:548-579`.

3. Pre-turn hygiene now reads the model wrapper from `deps.model`, resolves the
   budget from `deps.config`, uses `max(estimate, reported_input_tokens)`, and
   builds a `RunContext` with the raw provider model only after deciding to
   compact.
   Source: `co_cli/context/compaction.py:378-407`.

4. Tool output can be bounded before it enters message history. `tool_output()`
   resolves a per-tool threshold from `ToolInfo.max_result_size` or
   `config.tools.result_persist_chars`; oversized display strings are persisted
   through `persist_if_oversized()` and replaced with a preview placeholder.
   Source: `co_cli/tools/tool_io.py:204-225`.

5. Before every model request, pydantic-ai runs the processor chain:
   - `dedup_tool_results()` collapses older identical compactable tool returns
     to back-reference markers while preserving original `tool_call_id`.
     Source: `co_cli/context/_history_processors.py:124-157`.
   - `truncate_tool_results()` protects the last user turn and replaces older
     compactable returns with semantic markers, keeping the five most recent per
     compactable tool type.
     Source: `co_cli/context/_history_processors.py:219-251`.
   - `enforce_batch_budget()` spills the current tool-return batch largest-first
     when aggregate content exceeds `config.tools.batch_spill_chars`.
     Source: `co_cli/context/_history_processors.py:315-367`.
   - `summarize_history_window()` performs token-pressure window compaction.
     Source: `co_cli/context/compaction.py:305-375`.

6. Window compaction resolves a budget, computes token pressure as
   `max(estimate_message_tokens(messages), latest_response_input_tokens(messages))`,
   and triggers above `max(int(budget * proactive_ratio),
   min_context_length_tokens)`. If compaction already ran this turn, the stale
   API-reported count is suppressed by using reported `0`.
   Source: `co_cli/context/compaction.py:330-346`.

7. The boundary planner keeps the head through the first substantive model
   response, groups history by user turns, walks backward to retain a tail sized
   by `tail_fraction * budget`, always keeps at least the last group, anchors
   the latest user prompt into the tail, and returns `None` on head/tail overlap.
   Source: `co_cli/context/_compaction_boundaries.py:154-212`.

8. Compaction gathers side-channel summarizer context from dropped-range file
   tool args, active session todos, and prior summary markers. The context is
   capped at 4000 chars.
   Source: `co_cli/context/_compaction_markers.py:88-179`.

9. The summarizer uses `summarize_messages()`, a no-tools `llm_call()` with a
   handoff-summary prompt and a security system prompt that frames history as
   data to summarize, not instructions to execute.
   Source: `co_cli/context/summarization.py:112-229`.

10. If summarization succeeds, `summary_marker()` wraps the summary in a
    defensive continuation envelope. If there is no model, the circuit breaker
    is active, or summarization fails, `static_marker()` is used instead.
    Sources: `co_cli/context/compaction.py:126-167`,
    `co_cli/context/_compaction_markers.py:39-85`.

11. `apply_compaction()` assembles:
    `head + marker + optional todo snapshot + search_tools breadcrumbs + tail`.
    It sets `history_compaction_applied` and `compacted_in_current_turn`, then
    awaits compaction-boundary knowledge extraction before returning.
    Source: `co_cli/context/compaction.py:182-218`.

12. Compaction-boundary extraction now drains any in-flight cadence extraction,
    extracts from the pre-compact tail inline, then pins
    `last_extracted_message_idx` to `len(post_compact)`.
    Source: `co_cli/knowledge/_distiller.py:207-261`.

13. Provider context-overflow errors are classified by `is_context_overflow()`.
    HTTP 413 is always overflow; HTTP 400 requires explicit overflow evidence in
    the body.
    Source: `co_cli/context/_http_error_classifier.py:35-84`.

14. Overflow recovery is one-shot per turn. The turn loop materializes pending
    user input into history, runs planner-based `recover_overflow_history()`,
    then falls back to `emergency_recover_overflow_history()` if planning
    returns `None`. On success it sets `current_input = None` and retries once.
    Sources: `co_cli/context/orchestrate.py:514-537`,
    `co_cli/context/orchestrate.py:614-641`.

15. `emergency_recover_overflow_history()` bypasses budget planning and LLM
    summarization. It keeps first group, static marker, optional todo snapshot,
    dropped-range `search_tools` breadcrumbs, and last group. It returns `None`
    when there are two or fewer groups.
    Source: `co_cli/context/compaction.py:266-302`.

16. Post-turn finalization branches compacted history to a child transcript when
    `history_compaction_applied` is true. Normal turns append only the
    unpersisted tail.
    Sources: `co_cli/main.py:131-143`,
    `co_cli/memory/transcript.py:84-107`.

17. Manual `/compact [focus]` routes through the shared `apply_compaction()`
    helper with full-history bounds `(0, n, n)`, appends an assistant ack, and
    returns `ReplaceTranscript(compaction_applied=True)`.
    Source: `co_cli/commands/_commands.py:329-381`.

18. Command-loop handling for `ReplaceTranscript` persists compacted slash-command
    history immediately as a child transcript and advances
    `persisted_message_count`.
    Source: `co_cli/main.py:246-258`.

## Resolved Since Prior Audit

### Pre-turn hygiene production wiring

Prior state: `run_turn()` passed `agent.model` into hygiene, while hygiene read
`model.context_window`; the raw provider model did not expose that attribute.

Current state: `maybe_run_pre_turn_hygiene()` takes `deps`, reads
`deps.model.context_window`, and only passes the raw model into the synthetic
`RunContext` after deciding to compact. Regression tests cover triggering,
no-op zones, latest-user survival, provider-reported token triggering, the
minimum context floor, and anti-thrashing reset behavior.

Sources: `co_cli/context/orchestrate.py:568-573`,
`co_cli/context/compaction.py:378-407`,
`tests/context/test_context_compaction.py:374-613`.

### Compaction-boundary extraction skip

Prior state: compaction scheduled extraction through the shared fire-and-forget
single-flight launcher, then advanced the extraction cursor even if another
extraction was already in flight.

Current state: `extract_at_compaction_boundary()` drains the in-flight cadence
task, runs extraction inline over the pre-compact delta with
`advance_cursor=False`, then pins the cursor to the compacted history length.

Source: `co_cli/knowledge/_distiller.py:207-261`.

### Manual `/compact` degradation parity

Prior state: manual `/compact` could fail without replacing history when the
summarizer provider failed.

Current state: `_cmd_compact()` calls shared `apply_compaction()` and therefore
inherits no-model static fallback, circuit-breaker static fallback, summarizer
exception fallback, personality-aware summarizer prompt assembly, dropped-range
enrichment, active todo snapshots, `search_tools` breadcrumbs, and
compaction-boundary extraction.

Sources: `co_cli/commands/_commands.py:329-381`,
`co_cli/context/compaction.py:126-218`,
`tests/context/test_history.py:1025-1154`.

## Logical Defects

### 1. Transcript write failure can corrupt persistence accounting

`_finalize_turn()` catches `OSError` from `persist_session_history()` and
surfaces a status message, but it then unconditionally sets
`deps.session.persisted_message_count = len(turn_result.messages)`.

Source: `co_cli/main.py:131-143`.

If compaction caused a child transcript branch and that write failed, the
session can believe compacted messages were persisted even though they were not.
Later normal appends can skip messages based on the advanced count.

Impact: possible transcript data loss after a persistence failure.

Likely fix direction: advance `persisted_message_count` only when
`persist_session_history()` succeeds. Apply the same scrutiny to command-loop
`ReplaceTranscript` persistence, which currently has no local `OSError` guard.
Source: `co_cli/main.py:246-258`.

### 2. `search_tools` breadcrumb preservation can leak non-search orphan returns

`_preserve_search_tool_breadcrumbs()` keeps an entire `ModelRequest` if any
part is a `ToolReturnPart` named `search_tools`.

Source: `co_cli/context/compaction.py:170-179`.

The documented invariant is narrower: preserve `search_tools` breadcrumbs only,
because they are intentionally orphaned from their dropped `ToolCallPart`.

If a parallel batch contains a `search_tools` return and another tool return in
the same `ModelRequest`, the whole request is preserved. The non-`search_tools`
return may become an orphan because its matching `ToolCallPart` was dropped.

Impact: provider/tool-call validation risk if mixed tool-result batches occur.

Likely fix direction: rebuild preserved breadcrumb messages with only the
`search_tools` `ToolReturnPart` entries instead of retaining the whole
`ModelRequest`.

## Functional Gaps

### 1. Re-compaction has no dedicated iterative-update prompt

The current prompt tells the summarizer to integrate prior summaries and move
pending questions to resolved questions when applicable.

Source: `co_cli/context/summarization.py:148-154`.

There is no separate prompt mode that treats the previous summary as ground
truth and asks only for an update. Existing research still lists this as a low
risk gap.

Impact: repeated compactions rely on general summarization discipline, which may
increase drift risk.

### 2. `search_tools` breadcrumbs have no cap or relevance filtering

`_preserve_search_tool_breadcrumbs()` retains every dropped-range
`ModelRequest` containing a `search_tools` return.

Source: `co_cli/context/compaction.py:170-179`.

There is no cap, `tool_call_id` dedup, relevance filtering, or part-level
rebuilding. The current spec text also claims breadcrumb dedup by kept IDs, but
the live implementation has no `kept_ids` parameter.

Impact: likely low today, but stale tool discovery state can survive across
multiple compactions and consume context.

### 3. Trigger estimation does not include static prompt or tool-schema tokens

`estimate_message_tokens()` counts message part content, structured dict/list
content, and `ToolCallPart.args`, but not static instructions, dynamic
instructions, or tool schemas.

Source: `co_cli/context/summarization.py:35-60`.

Provider-reported usage often compensates after one request, but local-only or
provider-no-usage paths can under-trigger for tool-heavy requests.

Impact: context overflow recovery remains important because proactive compaction
can still miss true request size.

### 4. Legacy `emergency_compact()` is still exported but not production-wired

`emergency_compact()` keeps only first group, static marker, and last group. It
does not preserve active todo snapshots, `search_tools` breadcrumbs, runtime
flags, or compaction-boundary extraction.

Source: `co_cli/context/compaction.py:96-110`.

The production overflow path uses `emergency_recover_overflow_history()` instead,
which preserves the newer continuity state.

Sources: `co_cli/context/orchestrate.py:514-537`,
`co_cli/context/compaction.py:266-302`.

Impact: low while the helper remains test-only, but it is an attractive footgun
because it is exported from `co_cli.context.compaction`.

## Common Happy Paths

### Small normal turn

1. `run_turn()` resets runtime state.
2. Pre-turn hygiene no-ops below the hygiene threshold.
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
4. `apply_compaction()` inserts summary marker, optional todo snapshot,
   `search_tools` breadcrumbs, and tail.
5. Runtime flags mark compaction as applied.
6. Compaction-boundary extraction runs inline.
7. Turn proceeds under a smaller context.

### Summarizer unavailable but compaction possible

1. M3 token pressure exceeds threshold.
2. Planner returns valid boundaries.
3. `ctx.deps.model` is missing, the circuit breaker skips, or the LLM raises.
4. Static marker replaces dropped middle.
5. Todo snapshot and breadcrumbs are still preserved.
6. The turn continues.

### Overflow after estimator miss

1. Provider returns HTTP 413 or HTTP 400 with overflow evidence.
2. `run_turn()` materializes pending input into history.
3. Planner-based recovery compacts and retries.
4. If planning fails, structural emergency recovery keeps first and last groups
   plus continuity state.
5. Retry succeeds.

### User-invoked `/compact`

1. User runs `/compact` or `/compact <focus>`.
2. Whole current history is compacted through shared `apply_compaction()`.
3. New history becomes compaction marker, optional todo snapshot,
   `search_tools` breadcrumbs, and ack response.
4. Command outcome persists a child transcript.

## Common Edge Paths

### Last turn exceeds tail budget

The planner still keeps the last turn group because `_MIN_RETAINED_TURN_GROUPS`
is hardcoded to `1`. This preserves the active user turn even if it alone
exceeds `tail_fraction * budget`.

Source: `co_cli/context/_compaction_boundaries.py:40-46`,
`co_cli/context/_compaction_boundaries.py:191-202`.

### Head/tail overlap

When all turn groups fit into the planned tail, `tail_start <= head_end` and the
planner returns `None`.

Source: `co_cli/context/_compaction_boundaries.py:207-212`.

Proactive M3 returns messages unchanged. Overflow recovery can then use
structural emergency fallback.

### No model on compaction path

Automatic and manual compaction use a static marker when `ctx.deps.model` is
missing. This is expected for reduced-deps contexts.

Source: `co_cli/context/compaction.py:134-136`.

### Circuit breaker trip and probe

After three consecutive summarizer failures, compaction skips LLM summarization
and uses static markers except on probe cadence. The first probe occurs at
failure count `13`, then every ten skips.

Source: `co_cli/context/compaction.py:87-123`.

### Active todos

Active `pending` and `in_progress` todos are included in summarizer enrichment
and also inserted as a standalone snapshot after the compaction marker.

Sources: `co_cli/context/_compaction_markers.py:108-136`,
`co_cli/context/compaction.py:203-213`.

### Re-compaction over existing summary and todo snapshot

Prior summary text is gathered from the dropped range and passed as additional
context. A fresh todo snapshot is rebuilt from session state. Tests assert that
re-compaction does not duplicate todo snapshots.

Sources: `co_cli/context/_compaction_markers.py:139-179`,
`tests/context/test_history.py:989-1017`.

## Common Negative Paths

### First-turn or two-group overflow

If there are two or fewer turn groups, structural emergency recovery returns
`None`; there is no middle range to drop.

Source: `co_cli/context/compaction.py:280-282`.

Outcome: terminal context overflow.

### Second overflow in the same turn

`turn_state.overflow_recovery_attempted` gates overflow recovery to one attempt
per turn. A second overflow after retry is terminal.

Source: `co_cli/context/orchestrate.py:619-641`.

### Manual `/compact` with no model or tripped circuit breaker

Manual `/compact` still replaces history. It uses a static marker and appends
the acknowledgement response.

Sources: `co_cli/commands/_commands.py:355-381`,
`tests/context/test_history.py:1057-1093`.

### Compaction-boundary extraction failure

Extraction failures are caught inside `_run_extraction_async()`. The cursor still
pins to `len(post_compact)` after the boundary hook returns.

Sources: `co_cli/knowledge/_distiller.py:130-168`,
`co_cli/knowledge/_distiller.py:228-261`.

Impact: compaction proceeds, but reusable knowledge from the dropped range can
be missed if extraction itself fails. This is a deliberate best-effort
degradation, not a concurrency bug.

### Transcript child write fails after compaction

The foreground turn path catches `OSError` and shows a status message, but
persistence counters advance anyway.

Source: `co_cli/main.py:131-143`.

Outcome: possible transcript data loss; tracked above as the remaining high-risk
logical defect.

## Notes On Stale Research And Specs

Older references to `co_cli/context/_history.py` are stale. The live source is
split across `compaction.py`, `_compaction_boundaries.py`,
`_compaction_markers.py`, and `_history_processors.py`.

`docs/specs/compaction.md` has at least one stale breadcrumb-detail claim: it
describes `_preserve_search_tool_breadcrumbs` as deduping by kept IDs, but the
current implementation takes only `dropped` and keeps whole matching
`ModelRequest`s.

Sources: `docs/specs/compaction.md:488-491`,
`co_cli/context/compaction.py:170-179`.

## Verification Performed

This was a code-trace and doc refresh, not a regression run. No pytest suite was
executed for this documentation-only change.
