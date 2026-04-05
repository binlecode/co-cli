# TODO: Context Compaction Quality — Summary Enrichment, Overflow Recovery, Message Normalization

**Task type:** code-feature + code-refactor
**Origin:** Five-way peer comparison (§7b, §8 in `docs/reference/RESEARCH-peer-session-compaction.md`)
**Depends on:** `TODO-tool-output-compaction.md` TASK-0 (turn grouping primitive — used by overflow recovery)

## Context

`TODO-tool-output-compaction.md` handles **tool output** size control (per-tool-result capping, per-tool-type recency clearing). This TODO handles the **full history** compaction lifecycle — the quality of summaries when compaction fires, recovery when the context window is exceeded despite pre-emptive thresholds, and per-message size normalization for messages that aren't tool results.

The inline synchronous compaction (shipped via `TODO-sync-compaction.md`) provides the execution path: `truncate_history_window()` calls `summarize_messages()` when the 85% threshold is crossed. This TODO improves what happens inside that path and adds a new recovery path for when the threshold isn't enough.

### Current State

- **Summarization prompt** (`_compaction.py:116-130`): bare free-form — "Distill the conversation history into a handoff summary." No structured sections, no plan/tool/file context injected. Personality addendum exists.
- **Summarizer input**: only the dropped messages (`messages[head_end:tail_start]`). No side-channel context about active plan, registered tools, or recent file paths.
- **Overflow recovery**: none. If a 413/context-length error reaches `_orchestrate.py:486`, it surfaces as a terminal error. The user must manually `/compact`.
- **Per-message normalization**: none beyond tool-output compaction. A 50K assistant response (e.g., from a reasoning model) stays in context at full size until the sliding window drops it entirely.

### Peer Convergence Evidence

| Gap | Convergence | Evidence |
|-----|-------------|----------|
| Structured summary template | **4/5** | gemini-cli `<state_snapshot>` XML, opencode Goal/Instructions/Discoveries/Accomplished/Files, codex "handoff summary" structured prompt, fork-cc rich prompt context |
| Rich compaction prompt (plan/tool/file context) | **3/5** | fork-cc (plan + memory + file state + tools), gemini-cli (plan path + step status), opencode (overridable via plugin hook) |
| Overflow/413 recovery | **4/5** | fork-cc (reactive compact, 2-stage), opencode (`ContextOverflowError` → compact), codex (post-sampling compact + remove-oldest retry), gemini-cli B (proactive prevention) |
| Two-tier per-message normalization | **3/5** | gemini-cli (12K recent / 2.5K older per message), opencode (prune thresholds with turn protection), fork-cc (micro-compact with compactable set) |
| Prior-snapshot integration | **2/5** | gemini-cli (new `<state_snapshot>` integrates prior), codex (`SUMMARY_PREFIX` tells model to build on prior) |

## Problem & Outcome

**Problem:** Compaction fires correctly (threshold + circuit breaker are solid) but produces low-quality summaries and has no recovery for context overflow.

1. **Summary quality**: bare free-form text loses structure across compaction cycles. The model forgets file paths, plan progress, and tool state because the summarizer has no context beyond the dropped messages.
2. **Overflow vulnerability**: a single large tool-calling turn can push past the 85% threshold. The provider returns 413, the turn fails, and the user must manually recover. 4/5 peers handle this automatically.
3. **Non-tool message bloat**: large assistant responses (reasoning traces, long explanations) are never size-capped. They stay full-size until the sliding window drops them entirely — no gradual degradation.

**Outcome:**
1. Structured summary template with sections (Goal, Key Decisions, Working Set, Remaining Work, Files) that survive multiple compaction cycles
2. Overflow recovery in the orchestration loop — catch 413, emergency compact, retry
3. Two-tier per-message normalization — grace zone for recent messages, aggressive cap for older ones

## Scope

**In scope:**
- Structured summary template in `_compaction.py` — replace `_SUMMARIZE_PROMPT` with sectioned template
- Context enrichment in `truncate_history_window()` — gather active plan path, recent file paths from tool results, registered tool names; pass as additional context to `summarize_messages()`
- Prior-snapshot integration — detect prior summary in messages, instruct summarizer to integrate it
- Overflow recovery in `_execute_stream_segment()` — catch 413/context-length, emergency compact (static marker, no LLM — the LLM just refused us), retry once
- Two-tier per-message normalization — new logic in `truncate_tool_returns` (or new processor) that caps older non-tool messages at a threshold while preserving recent ones at a higher limit

**Out of scope:**
- Tool output size control (covered by `TODO-tool-output-compaction.md`)
- Token-based tail sizing (P3 — `max(4, len(messages) // 2)` is functional)
- JSON-density ratio in fallback estimator (P3 — `chars // 4` is good enough)
- Warning tiers / context budget indicator (P3 — nice UX, not load-bearing)
- Provider-delegated compaction (codex-only pattern, 1/5)

## Behavioral Constraints

1. **Structured summary is a prompt change, not a parser.** The template instructs the LLM to produce sectioned output. No parsing of the summary is required — it enters the message list as a single `UserPromptPart` string. If the LLM ignores the structure, the summary still works (free-form fallback is the current behavior).
2. **Context enrichment is best-effort.** If no active plan exists, the plan section is omitted. If no recent file paths are found, that section is omitted. Missing context never blocks summarization.
3. **Overflow recovery is one-shot.** On 413, compact with static marker (no LLM call — the provider just told us context is too large), retry once. If the retry also fails, surface the error. No spiral. This matches the simplest peer pattern (opencode).
4. **Overflow recovery uses turn groups.** Emergency compact drops complete turn groups from the middle (using `group_by_turn()` from `TODO-tool-output-compaction.md` TASK-0), never splits tool call/result pairs.
5. **Per-message normalization preserves tool results.** Tool output trimming is handled by `TODO-tool-output-compaction.md`. This processor targets `TextPart` and `ThinkingPart` content in `ModelResponse` messages — large assistant responses, not tool results.
6. **Grace zone is dynamic.** Recent messages (within last N tokens of history) get a higher size cap. Older messages get a lower cap. The boundary shifts as conversation grows, matching gemini-cli's pattern.
7. **Normalization is head+tail preserving.** When truncating a large message, keep the first K% and last (100-K)% of the content, matching gemini-cli's proportional truncation (25% head + 75% tail). Do not discard either end entirely.

## High-Level Design

### Structured Summary Template

Replace `_SUMMARIZE_PROMPT` with:

```text
Distill the conversation history into a structured handoff summary.
Write from the user's perspective. Start with 'I asked you...'

Use these sections:

## Goal
What the user is trying to accomplish. Include constraints and preferences.

## Key Decisions
Important decisions made and why. Include rejected alternatives if relevant.

## Working Set
Files read, edited, or created. URLs fetched. Tools actively in use.

## Progress
What has been accomplished. What is in progress. What remains.

## Next Steps
Immediate next actions. Any blockers or pending dependencies.

If a prior summary exists in the conversation, integrate its content —
do not discard it. Update sections with new information.

Be concise — this replaces the original messages to save context space.
Prioritize recent actions and unfinished work over completed early steps.
```

Personality addendum appended when `personality_active=True` (unchanged).

### Context Enrichment

In `truncate_history_window()`, before calling `summarize_messages()`:

```text
context_parts = []
if active_plan_path exists in .co-cli/:
    context_parts.append(f"Active plan: {plan_path}")
recent_files = extract file paths from ToolReturnPart metadata in last N messages
if recent_files:
    context_parts.append(f"Recent files: {', '.join(recent_files[:20])}")
tool_names = [t for t in deps.tool_index if deps.tool_index[t].always_load]
if tool_names:
    context_parts.append(f"Available tools: {', '.join(sorted(tool_names)[:15])}")

enriched_prompt = prompt
if context_parts:
    enriched_prompt += "\n\nContext for summary:\n" + "\n".join(context_parts)
```

Passed to `summarize_messages(dropped, resolved, prompt=enriched_prompt, ...)`.

### Overflow Recovery

In `_execute_stream_segment()` at `_orchestrate.py:486`, extend the `ModelHTTPError` handler:

```text
except ModelHTTPError as e:
    code = e.status_code
    if code == 400 and tool_reformat_budget > 0:
        ... (existing 400 handler)

    # NEW: 413 / context-length recovery
    if _is_context_overflow(e) and not turn_state.overflow_recovery_attempted:
        turn_state.overflow_recovery_attempted = True
        groups = group_by_turn(turn_state.current_history)
        if len(groups) > 2:
            # Drop middle groups, keep head + last group, inject static marker
            compacted = emergency_compact(groups)
            turn_state.current_history = compacted
            turn_state.current_input = None
            frontend.on_status("Context overflow — compacting and retrying...")
            continue
    # All other HTTP errors — terminal
    ...
```

`_is_context_overflow(e)`: checks `e.status_code == 413` or `"prompt is too long"` / `"context_length_exceeded"` in error body. Matches fork-cc's `isPromptTooLongMessage` pattern.

`emergency_compact(groups)`: keeps first group (head) + last group (current turn) + static marker in between. No LLM call. Uses `groups_to_messages()` from TASK-0.

### Two-Tier Per-Message Normalization

New processor or extension to existing processor chain. Runs after `truncate_tool_returns` (which handles tool results), targets `ModelResponse` messages:

```text
RECENT_MSG_MAX_CHARS = 12_000    # grace zone (matches gemini-cli retainedMaxTokens)
OLDER_MSG_MAX_CHARS  = 2_500     # older messages (matches gemini-cli normalMaxTokens)
GRACE_ZONE_TOKENS    = 40_000    # last 40K tokens of history get the higher limit

For each ModelResponse before the safe tail:
    For each TextPart/ThinkingPart:
        if in grace zone: cap at RECENT_MSG_MAX_CHARS
        else: cap at OLDER_MSG_MAX_CHARS
    Truncation: proportional head(25%) + tail(75%)
```

## Implementation Plan

### TASK-0: Structured summary template + context enrichment

files:
- `co_cli/context/_compaction.py`
- `co_cli/context/_history.py`

Changes:
1. Replace `_SUMMARIZE_PROMPT` with the sectioned template (Goal, Key Decisions, Working Set, Progress, Next Steps). Keep personality addendum unchanged.
2. Add prior-snapshot detection: scan dropped messages for `"[Summary of"` prefix (the marker from `truncate_history_window`). If found, append instruction: "A prior summary exists — integrate its content, do not discard it."
3. In `truncate_history_window()`, gather context enrichment (active plan path, recent file paths from `ToolReturnPart` metadata, always-loaded tool names) and pass as enriched prompt to `summarize_messages()`.
4. `summarize_messages()` gains an optional `context: str | None = None` parameter. If provided, appended to prompt after the template.

prerequisites: none
done_when: `grep -n "## Goal\|## Key Decisions\|## Working Set\|## Progress\|## Next Steps" co_cli/context/_compaction.py` returns matches AND `uv run pytest tests/test_history.py tests/test_context_compaction.py -x` passes.
success_signal: N/A (summary quality — observable in long sessions, not unit-testable without mocks)

### TASK-1: Overflow/413 recovery in orchestration loop

files:
- `co_cli/context/_orchestrate.py`
- `co_cli/context/_history.py` (imports `group_by_turn`, `groups_to_messages`)

Changes:
1. Add `overflow_recovery_attempted: bool = False` to `_TurnState` (or equivalent turn-scoped state in `_orchestrate.py`).
2. Add `_is_context_overflow(e: ModelHTTPError) -> bool`: checks `status_code == 413` or known context-length error patterns in `e.body` (`"prompt is too long"`, `"context_length_exceeded"`, `"maximum context length"`).
3. In `_execute_stream_segment()` `ModelHTTPError` handler (line 486): after the existing 400 handler, add overflow detection. If overflow and not already attempted: set flag, call `emergency_compact()`, update `turn_state.current_history`, `continue` the segment loop.
4. `emergency_compact(messages: list[ModelMessage]) -> list[ModelMessage]`: uses `group_by_turn()`, keeps first group + last group + static marker between. Returns flattened message list.
5. If retry also fails: surface error as today (terminal).

prerequisites: `TODO-tool-output-compaction.md` TASK-0 (provides `group_by_turn`, `groups_to_messages`)
done_when: `grep -n "overflow_recovery_attempted\|emergency_compact\|_is_context_overflow" co_cli/context/_orchestrate.py` returns matches AND `uv run pytest tests/test_history.py -x` passes.
success_signal: N/A (triggered only by 413 errors — rare in normal use, testable via integration test with artificially small context window)

### TASK-2: Two-tier per-message normalization

files:
- `co_cli/context/_history.py`

Changes:
1. Add constants: `RECENT_MSG_MAX_CHARS = 12_000`, `OLDER_MSG_MAX_CHARS = 2_500`, `GRACE_ZONE_TOKENS = 40_000`.
2. Add `_truncate_proportional(text: str, max_chars: int, head_ratio: float = 0.25) -> str`: keeps first `head_ratio` and last `(1 - head_ratio)` of content, inserts `[…truncated]` marker. Returns text unchanged if under limit.
3. Add `normalize_message_sizes()` processor (or extend `truncate_tool_returns`):
   - Runs after tool-output compaction processor, before `truncate_history_window`
   - Scans messages in reverse to compute grace zone boundary (accumulate token estimates until `GRACE_ZONE_TOKENS` reached)
   - For each `ModelResponse` before safe tail: truncate `TextPart.content` and `ThinkingPart.content` exceeding the per-zone limit via `_truncate_proportional`
   - Does NOT touch `ToolReturnPart` (handled by tool-output compaction)
   - Does NOT touch `UserPromptPart` (user input is sacred)
4. Register as history processor after `truncate_tool_returns`, before `detect_safety_issues`.

prerequisites: none (independent of tool-output compaction, but benefits from it running first)
done_when: `grep -n "normalize_message_sizes\|RECENT_MSG_MAX_CHARS\|OLDER_MSG_MAX_CHARS" co_cli/context/_history.py` returns matches AND `uv run pytest tests/test_history.py -x` passes.
success_signal: N/A (observable in long sessions with large assistant responses)

### TASK-3: Tests

files:
- `tests/test_history.py`
- `tests/test_context_compaction.py`

Changes — structured summary (TASK-0):
1. Test: `summarize_messages()` with enriched prompt includes context sections in output (verify prompt is passed through — no output parsing needed).
2. Test: prior-snapshot detection — when dropped messages contain a prior summary marker, the prompt includes integration instruction.

Changes — overflow recovery (TASK-1):
3. Test: `emergency_compact()` with 5 turn groups → returns 2 groups (first + last) + static marker.
4. Test: `emergency_compact()` with 2 or fewer groups → returns messages unchanged (nothing to drop).
5. Test: `_is_context_overflow()` correctly identifies 413 and context-length error patterns.

Changes — per-message normalization (TASK-2):
6. Test: `_truncate_proportional()` with text over limit → preserves 25% head + 75% tail + marker.
7. Test: `normalize_message_sizes()` — old `ModelResponse` with 50K `TextPart` truncated to 2.5K; recent one within grace zone truncated to 12K.
8. Test: `ToolReturnPart` and `UserPromptPart` content never touched by normalization.

prerequisites: [TASK-0, TASK-1, TASK-2]
done_when: `uv run pytest tests/test_history.py tests/test_context_compaction.py -v` passes with all tests green.
success_signal: N/A

## Testing

- TASK-0: Summary quality is not unit-testable without mocks. Tests verify prompt construction (enrichment, prior-snapshot detection), not LLM output.
- TASK-1: `emergency_compact()` and `_is_context_overflow()` are pure functions — fully testable. Integration test with real 413 requires artificially small context window (Ollama `llm_num_ctx=100`).
- TASK-2: `_truncate_proportional()` is a pure function. `normalize_message_sizes()` tested with constructed message lists.
- No mocks. Real `CoDeps`, real `RunContext`. Timeouts on any LLM call.

## Priority Order

TASK-0 (summary quality) and TASK-2 (normalization) are independent — can be developed in parallel. TASK-1 (overflow recovery) depends on `group_by_turn()` from the tool-output compaction TODO.

Critical path: `TODO-tool-output-compaction` TASK-0 → this TODO's TASK-1.
Parallel path: this TODO's TASK-0 + TASK-2 (no external dependency).

## Open Questions

None.
