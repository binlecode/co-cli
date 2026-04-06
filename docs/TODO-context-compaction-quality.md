# TODO: Context Compaction Quality — Smart Summarization, Overflow Recovery, Response Compaction

**Task type:** code-feature
**Origin:** Five-way peer comparison (`docs/reference/RESEARCH-peer-session-compaction.md` §2, §6b)
**Depends on:** none — `group_by_turn()` and `groups_to_messages()` shipped in commit 6191bfd (`_history.py:101–137`)
**Post-ship:** sync ordering rationale and processor chain docs into `docs/DESIGN-context.md` and `docs/DESIGN-core-loop.md` via `/sync-doc`

## Context

The compaction pipeline runs as a history processor chain before every model API call. The chain is registered in `agent.py:289–294`:

```text
1. truncate_tool_results      [sync]   — content-clear old compactable tool results
2. detect_safety_issues        [sync]   — doom-loop + shell error detection
3. inject_opening_context      [async]  — memory recall on new user turns
4. summarize_history_window    [async]  — drop middle messages + LLM summary
```

### The Root Problem

When `summarize_history_window` (processor #4) fires, it passes `dropped = messages[head_end:tail_start]` to `summarize_messages()` (`_history.py:365`). Because `truncate_tool_results` (processor #1) has already run, those dropped messages have `"[tool result cleared]"` placeholders instead of real tool output. The summarizer produces low-quality summaries from hollow input.

However, `truncate_tool_results` only clears **tool result content** (`ToolReturnPart.content`) for 6 compactable tools, and only beyond the 5 most recent per type. What survives in the dropped messages:

- **Tool call args** (`ToolCallPart.args`) — always intact, never truncated. Contains file paths, search patterns, commands.
- The 5 most recent results per tool type — full content
- All non-compactable tool results — full content
- All assistant text, user messages, thinking — untouched
- The current turn — fully protected

This makes original-content preservation **less critical** than the TODO originally assumed. The summarizer can reconstruct the working set from tool call args even when outputs are cleared. What matters more is giving the summarizer **structured guidance and side-channel context** — the strategy fork-cc uses successfully.

### Peer Strategies (research §2, §6c)

| Strategy | Peer | Complexity |
|----------|------|-----------|
| **Rich side-channel context** | fork-cc — accepts cleared input, injects plan/memory/file/tool context into summarizer | Medium |
| **Feed originals to summarizer** | gemini-cli A — saves full output to disk, sends un-truncated to summarizer | Medium |
| **Non-destructive marking** | opencode — marks `time.compacted` but keeps full content in message list | Medium |
| **No pre-trimming** | codex (inline) — sends entire unmodified history to summarizer | Low |

**Chosen approach: fork-cc strategy (rich side-channel context)** — best ROI for co-cli. The selective truncation (6 tools, keep 5 recent) already preserves most content. Adding structured guidance + context enrichment addresses the quality gap without architectural changes to the processor pipeline.

## Problem & Outcome

**Problem:** The summarizer operates with a flat prompt, no structural guidance, no side-channel context, and cleared tool results in the dropped slice. 4/4 peers compensate; co-cli does not.
**Failure cost:** In long sessions, compaction summaries lose critical context (active file set, decisions, pending work), causing the model to repeat completed work, forget constraints, or lose track of multi-step tasks. Users must re-explain context or start new sessions prematurely.

1. **Summarizer prompt quality** (critical): flat bullet-point guidance loses structure across compaction cycles. 4/5 peers use structured sections.
2. **Missing context enrichment** (critical): summarizer has no knowledge of the working set or active tasks beyond what's in the (partially cleared) messages. 3/5 peers inject side-channel context.
3. **No prior-summary integration**: when a prior summary exists in the dropped messages, it gets re-summarized without explicit integration instruction. 2/5 peers handle this explicitly.
4. **Overflow vulnerability**: 413/context-length errors are terminal. 4/5 peers auto-recover.
5. **Non-tool message bloat**: large assistant responses stay full-size until dropped entirely.

**Outcome:**
1. Structured summary template with sections that survive multiple compaction cycles
2. Rich context enrichment — file working set, session todos, always-on memories injected into summarizer prompt
3. Prior-summary detection and integration instruction
4. Overflow recovery — catch 413, emergency compact, retry
5. Per-message normalization for non-tool content — last turn group untouched, older messages capped (`compact_assistant_responses`)

## Scope

**In scope:**
- Structured summary template — replace `_SUMMARIZE_PROMPT` with sectioned template
- Context enrichment — gather file working set, session todos, always-on memories into summarizer prompt
- Prior-snapshot integration — detect and integrate prior summaries in dropped messages
- `summarize_messages()` gains optional `context: str | None` parameter
- Overflow/413 recovery in `run_turn()` error handler
- `compact_assistant_responses` processor for `TextPart`/`ThinkingPart` in `ModelResponse`

**Out of scope:**
- ~~Persistent history architecture~~ (dropped — selective truncation makes original preservation less critical)
- ~~Non-destructive processor pattern~~ (dropped — side-channel context compensates)
- ~~Copy-on-write prompt view~~ (dropped — unnecessary complexity)
- Tool output size control (shipped in 6191bfd)
- Token-based tail sizing (P3)
- Size-aware trimming (token-budget-based clearing instead of recency count — separate TODO)

## Behavioral Constraints

1. **Structured summary is a prompt change, not a parser.** Free-form fallback if the model ignores structure.
2. **Context enrichment is best-effort and capped.** Missing context never blocks summarization. The assembled context string is truncated to 4K chars before injection into the summarizer prompt — prevents the summarizer itself from hitting token limits when many file paths, todos, and memories are present.
3. **File working set is extracted from `ToolCallPart.args`, not `ToolReturnPart.content`.** Tool call args are never truncated — they survive `truncate_tool_results` intact.
4. **`tool_output()` 50K persist-to-disk still applies.** Individual tool results >50K are already capped with a 2K preview at return time. Only cumulative size matters.
5. **Overflow recovery is one-shot.** On 413/400-with-context-length-body: static marker (no LLM), retry once, terminal on second failure. Known limitation: double-overflow (e.g., very large tool results in the surviving tail after emergency compaction) is terminal — monitor post-ship and revisit if observed.
6. **Per-message normalization preserves tool results and user input.** Targets `TextPart`/`ThinkingPart` in `ModelResponse` only.
7. **In-place mutation in `truncate_tool_results` and `compact_assistant_responses` is acceptable.** The REPL's `message_history` is overwritten with `turn_result.messages` each turn (`_orchestrate.py:471`, `main.py:88`). Transcript writes only new messages per turn (`main.py:100–101`), which are clean. The mutations affect only the model's working context and the summarizer's dropped slice — the latter is compensated by context enrichment. The summarizer sees proportionally truncated assistant text and cleared tool results, but the structured template and side-channel context (file paths from `ToolCallPart.args`, todos, always-on memories) provide sufficient signal.

## High-Level Design

### Structured Summary Template

Replace `_SUMMARIZE_PROMPT` (`_summarization.py:116–130`) with:

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
Skip sections that have no content — do not generate filler.

Be concise — this replaces the original messages to save context space.
Prioritize recent actions and unfinished work over completed early steps.
```

Personality addendum (`_summarization.py:132–138`) appended when `personality_active=True` (unchanged).

### Context Enrichment (fork-cc aligned)

In `summarize_history_window()`, before calling `summarize_messages()`, gather side-channel context from sources that survive truncation:

```text
context_parts = []

# 1. File working set — extracted from ToolCallPart.args (never truncated)
file_paths = set()
for msg in messages:                     # full message list (head + dropped + tail)
    if isinstance(msg, ModelResponse):
        for part in msg.parts:
            if isinstance(part, ToolCallPart) and part.tool_name in FILE_TOOLS:  # public constant
                args = part.args_as_dict()
                path = args.get("path") or args.get("file_path")
                if path:
                    file_paths.add(path)
if file_paths:
    context_parts.append(f"Files touched: {', '.join(sorted(file_paths)[:20])}")

# 2. Session todos — in-memory, always current
todos = ctx.deps.session.session_todos
if todos:
    pending = [t for t in todos if t.get("status") not in ("completed", "cancelled")]
    if pending:
        todo_lines = [f"- [{t.get('status','pending')}] {t.get('content','?')}" for t in pending[:10]]
        context_parts.append(f"Active tasks:\n" + "\n".join(todo_lines))

# 3. Always-on memories — standing context the model always sees
from co_cli.tools.memory import load_always_on_memories  # deferred import
memories = load_always_on_memories(ctx.deps.config.memory_dir)
if memories:
    mem_lines = [m.content[:200] for m in memories[:5]]
    context_parts.append(f"Standing memories:\n" + "\n".join(mem_lines))

# 4. Cap total context to 4K chars — prevent summarizer token overflow
_CONTEXT_MAX_CHARS = 4_000
result = "\n\n".join(context_parts)
return result[:_CONTEXT_MAX_CHARS] if result else None
```

`FILE_TOOLS`: `frozenset({"read_file", "write_file", "edit_file", "find_in_files", "list_directory"})` — tools whose args contain file paths (public constant, same pattern as `COMPACTABLE_TOOLS`).

`summarize_messages()` (`_summarization.py:158`) gains optional `context: str | None = None`. Prompt assembly extracted into `_build_summarizer_prompt(template, context, personality_active) -> str` pure function. Assembly order: template + context addendum (`\n\n## Additional Context\n{context}`) + personality addendum. Personality always last — it modifies tone, while context provides factual input.

### Prior-Summary Detection

In `summarize_history_window()`, scan the dropped messages for the `"[Summary of"` prefix (marker at `_history.py:379`). If found, the structured template already includes the integration instruction ("If a prior summary exists in the conversation, integrate its content"). Additionally append the prior summary text to context enrichment so the summarizer sees it explicitly even if the marker message is in the dropped slice.

### Overflow Recovery

In the `ModelHTTPError` handler (`_orchestrate.py:486–506`), insert the overflow check **before** the existing HTTP 400 reformulation handler. OpenAI returns 400 (not 413) for context-length exceeded, so the overflow check must run first to avoid the 400 handler consuming it as a tool-reformulation error:

```text
    if _is_context_overflow(e):
        if not turn_state.overflow_recovery_attempted:
            turn_state.overflow_recovery_attempted = True
            compacted = emergency_compact(turn_state.current_history)
            if compacted is not None:
                turn_state.current_history = compacted
                turn_state.current_input = None
                frontend.on_status("Context overflow — compacting and retrying...")
                continue
        # Either: (a) already attempted (second overflow after retry), or
        # (b) compaction impossible (≤2 groups). Both are terminal per
        # behavioral constraint #5. MUST NOT fall through to the 400
        # reformulation handler — it would send a misleading "reformulate
        # your tool call" message for a context overflow.
        frontend.on_status("Context overflow — unrecoverable.")
        turn_state.outcome = "error"
        return _build_error_turn_result(turn_state)
    # Existing 400 reformulation handler follows...
    # All other HTTP errors — terminal
```

`_is_context_overflow(e)`: checks `status_code in (400, 413)` AND `str(e.body)` contains `"prompt is too long"` / `"context_length_exceeded"` / `"maximum context length"`. Must coerce `e.body` to `str` before matching — `ModelHTTPError.body` is typed `object | None` (OpenAI sends `dict`, Ollama may send `str`). Both status code AND body pattern must match — bare 400 without a context-length message falls through to the existing reformulation handler.

`emergency_compact(messages)`: calls `group_by_turn()`, returns `None` if ≤2 groups. Otherwise keeps first group + last group + `_static_marker(dropped_count)` between (reuses the existing marker at `_history.py:163` for consistency with `summarize_history_window`), returns via `groups_to_messages()`. `dropped_count = sum(len(g.messages) for g in groups[1:-1])` — total messages in dropped groups, not number of groups. No LLM call. Lives in `_history.py` (public — imported by `_orchestrate.py`, tested directly).

### compact_assistant_responses Processor

New processor registered after `truncate_tool_results`, before `detect_safety_issues`:

```text
OLDER_MSG_MAX_CHARS = 2_500     # older messages (matches gemini-cli)

Protection: last turn group is untouched (same pattern as truncate_tool_results).
All older ModelResponse messages get capped.

For each ModelResponse outside the last turn group:
    For each TextPart/ThinkingPart:
        if len(content) > OLDER_MSG_MAX_CHARS: cap via _truncate_proportional
    Truncation: _truncate_proportional — keeps head(25%) + tail(75%) + "[...truncated...]" marker
    Does NOT touch ToolCallPart (args are critical — TASK-1 file path extraction depends on them)
    Does NOT touch ToolReturnPart or UserPromptPart
    Does NOT touch ModelRequest messages (user input, tool returns)
```

Post-impl processor chain:

```text
1. truncate_tool_results        [sync]   — content-clear old compactable tool results
2. compact_assistant_responses   [sync]   — cap large TextPart/ThinkingPart in ModelResponse
3. detect_safety_issues          [sync]   — doom-loop + shell error detection
4. inject_opening_context        [async]  — memory recall on new user turns
5. summarize_history_window      [async]  — drop middle messages + LLM summary with context enrichment
```

**Ordering rationale:**

- **#1–2 before #5**: truncation and response capping run before summarization. The summarizer sees partially cleared content but receives rich side-channel context (file working set from `ToolCallPart.args`, session todos, always-on memories) to compensate. This avoids architectural complexity (persistent history, non-destructive processors) while matching fork-cc's proven strategy.
- **#3 before #5**: `detect_safety_issues` scans recent tool calls for doom loops and shell error streaks. Running it before summarization ensures it scans the full un-compacted history — if summarization drops the middle first, streak evidence in the dropped slice would be missed. In practice streaks are in the recent tail (not the dropped middle), but the current order is defensive and costs nothing (sync, no I/O, scans at most 10 messages).
- **#4 before #5**: `inject_opening_context` appends recalled memories at the tail. Order relative to summarization doesn't matter — the injection is outside the summarizer's dropped slice. Placed before summarization to keep the costliest processor (LLM call) last.
- **All sync processors before async**: sync processors (#1–3) run inline with zero overhead. Async processors (#4–5) are awaited directly on the event loop (no executor dispatch) since their `async def` signatures are detected by pydantic-ai's `is_async_callable()` check.

## Implementation Plan

### ✓ DONE — TASK-1: Structured summary template + context enrichment

files:
- `co_cli/context/_summarization.py`
- `co_cli/context/_history.py`

Changes:
1. Replace `_SUMMARIZE_PROMPT` (`_summarization.py:116–130`) with the sectioned template (Goal, Key Decisions, Working Set, Progress, Next Steps). Keep `_PERSONALITY_COMPACTION_ADDENDUM` (`_summarization.py:132–138`) and `_SUMMARIZER_SYSTEM_PROMPT` (`_summarization.py:140–147`) unchanged.
2. `summarize_messages()` (`_summarization.py:158–177`) gains optional `context: str | None = None`. Prompt assembly order: template + context addendum (`\n\n## Additional Context\n`) + personality addendum. Personality always last (tone modifier). Extract prompt assembly into `_build_summarizer_prompt(template: str, context: str | None, personality_active: bool) -> str` pure function — testable without LLM call. Guard context insertion with `if context:` (truthy — handles both `None` and `""`).
3. Add `FILE_TOOLS: frozenset[str]` constant in `_history.py` for tools whose args contain file paths (public — same pattern as `COMPACTABLE_TOOLS`).
4. Add `_gather_compaction_context(ctx: RunContext[CoDeps], messages: list[ModelMessage], dropped: list[ModelMessage]) -> str | None` helper in `_history.py`. Extracts 3 sources: (a) file working set from `ToolCallPart.args_as_dict()` across all `messages` (full list — head + dropped + tail), (b) pending session todos from `ctx.deps.session.session_todos`, (c) always-on memories via `load_always_on_memories(ctx.deps.config.memory_dir)` (the summarizer is a separate agent that does not share the main agent's `add_always_on_memories` system prompt dynamic). Additionally detects prior-summary text from `dropped` messages matching the `"[Summary of"` prefix (marker at `_history.py:379`) and appends it. Import `load_always_on_memories` as a deferred import inside `_gather_compaction_context()` (matches the pattern used by `inject_opening_context` at `_history.py:450` — context layer importing from tools layer uses deferred imports for consistency). Cap the assembled context string at `_CONTEXT_MAX_CHARS = 4_000` before returning (prevents summarizer token overflow — see behavioral constraint #2). Returns `None` when no context was gathered.
5. In `summarize_history_window()`, after computing `dropped = messages[head_end:tail_start]`, call `_gather_compaction_context(ctx, messages, dropped)` and pass as `context=` to `summarize_messages()`.
6. `index_session_summary` intentionally does not pass `context=` — session checkpoint summaries operate on recent messages only, not a compaction window.

prerequisites: none
done_when: ALL grep checks return matches AND pytest passes:
  `grep -n "## Goal" co_cli/context/_summarization.py` (template section 1/5)
  `grep -n "## Next Steps" co_cli/context/_summarization.py` (template section 5/5)
  `grep -n "_build_summarizer_prompt" co_cli/context/_summarization.py` (prompt assembly extracted)
  `grep -A5 "def summarize_messages" co_cli/context/_summarization.py | grep "context"` (context param in multi-line sig)
  `grep -n "FILE_TOOLS" co_cli/context/_history.py` (file-tool constant)
  `grep -n "_gather_compaction_context" co_cli/context/_history.py` (context helper exists)
  `grep -n "_CONTEXT_MAX_CHARS" co_cli/context/_history.py` (4K cap)
  `uv run pytest tests/test_history.py tests/test_context_compaction.py -x` (no regressions)
success_signal: N/A (summary quality observable in long sessions)

### ✓ DONE — TASK-2: Overflow/413 recovery

files:
- `co_cli/context/_orchestrate.py`
- `co_cli/context/_history.py`

Changes:
1. Add `overflow_recovery_attempted: bool = False` to `_TurnState` (`_orchestrate.py:80–116`).
2. Add `_is_context_overflow(e: ModelHTTPError) -> bool` in `_orchestrate.py`. Checks `status_code in (400, 413)` AND `str(e.body)` contains context-length pattern (`"prompt is too long"`, `"context_length_exceeded"`, `"maximum context length"`). Must coerce `e.body` via `str()` — `body` is typed `object | None` (OpenAI sends `dict`, Ollama may send `str`). Both conditions must match — bare 400 without context-length message falls through to reformulation.
3. In `ModelHTTPError` handler (`_orchestrate.py:486–506`): insert overflow check **before** the existing HTTP 400 reformulation handler (lines 490–502). Critical design invariant: **any time `_is_context_overflow` returns True, the overflow handler must resolve the error completely (compact+retry OR terminal) — it must NEVER fall through to the 400 reformulation handler.** This covers three cases: (a) first overflow, compaction succeeds → retry; (b) first overflow, compaction impossible (≤2 groups) → terminal; (c) second overflow after retry (behavioral constraint #5) → terminal. The outer guard is `if _is_context_overflow(e):` (always enters), the inner guard is `if not overflow_recovery_attempted:` (controls first vs retry).
4. `emergency_compact(messages: list[ModelMessage]) -> list[ModelMessage] | None`: uses `group_by_turn()` (`_history.py:101`). Returns `None` if ≤2 groups (nothing to compact). Otherwise keeps first group + last group + `_static_marker(dropped_count)` between (reuses existing marker for consistency), returns via `groups_to_messages()` (`_history.py:132`). `dropped_count` is the total number of **messages** in the dropped groups: `sum(len(g.messages) for g in groups[1:-1])` — not the number of groups.

prerequisites: none
done_when: ALL grep checks return matches AND pytest passes:
  `grep -n "overflow_recovery_attempted" co_cli/context/_orchestrate.py` (field on _TurnState)
  `grep -n "_is_context_overflow" co_cli/context/_orchestrate.py` (helper function)
  `grep -n "emergency_compact" co_cli/context/_history.py` (function in _history)
  `grep -n "emergency_compact" co_cli/context/_orchestrate.py` (integration wiring)
  `uv run pytest tests/test_history.py -x` (no regressions)
success_signal: N/A (triggered by 413 — testable via Ollama `llm_num_ctx=100`)

### ✓ DONE — TASK-3: compact_assistant_responses processor

files:
- `co_cli/context/_history.py`
- `co_cli/agent.py`

Changes:
1. Add constant: `OLDER_MSG_MAX_CHARS = 2_500`.
2. Add `_truncate_proportional(text: str, max_chars: int, head_ratio: float = 0.25) -> str`. Returns `text` unchanged when `len(text) <= max_chars` (defensive no-op — caller also guards, but function must be safe standalone).
3. Add `compact_assistant_responses()` processor: runs after `truncate_tool_results`, before `detect_safety_issues`. Protects the last turn group (same `group_by_turn()` boundary as `truncate_tool_results`). Caps `TextPart`/`ThinkingPart` in `ModelResponse` outside the last turn group at `OLDER_MSG_MAX_CHARS`. Does NOT touch `ToolCallPart` (args are critical — TASK-1 file path extraction depends on them), `ToolReturnPart`, or `UserPromptPart`. In-place mutation is acceptable (same pattern as `truncate_tool_results`).
4. Register in `agent.py`: `truncate_tool_results` → **`compact_assistant_responses`** → `detect_safety_issues` → `inject_opening_context` → `summarize_history_window`.

prerequisites: none
done_when: ALL grep checks return matches AND pytest passes:
  `grep -n "compact_assistant_responses" co_cli/context/_history.py` (processor function)
  `grep -n "OLDER_MSG_MAX_CHARS" co_cli/context/_history.py` (threshold constant)
  `grep -n "_truncate_proportional" co_cli/context/_history.py` (truncation helper)
  `grep -n "compact_assistant_responses" co_cli/agent.py` (registration wiring)
  `uv run pytest tests/test_history.py -x` (no regressions)
success_signal: N/A (observable in long sessions with large assistant responses)

### ✓ DONE — TASK-4: Tests

files:
- `tests/test_history.py`
- `tests/test_context_compaction.py`

Changes — structured summary + context enrichment (TASK-1):
1. Test: `_build_summarizer_prompt()` — 4 combinations: (a) `context=None, personality_active=False` → returns template unchanged; (b) `context="...", personality_active=False` → template + `## Additional Context` + context; (c) `context=None, personality_active=True` → template + personality addendum (the `index_session_summary` / `/compact` path); (d) `context="...", personality_active=True` → template + context + personality (personality always last). Pure function — no LLM call needed.
2. Test: `_gather_compaction_context()` extracts file paths from `ToolCallPart.args_as_dict()` in constructed messages (scans full list, not just dropped).
3. Test: `_gather_compaction_context()` includes pending session todos (filters out done).
4. Test: `_gather_compaction_context()` extracts prior-summary text from dropped messages containing `"[Summary of"` prefix.
5. Test: `_gather_compaction_context()` returns `None` when no context sources produce data.
6. Test: always-on memory extraction uses real `.md` files in `tmp_path` with YAML frontmatter containing `always_on: true`.
6b. Test: `_gather_compaction_context()` with >4K combined sources → output truncated to `_CONTEXT_MAX_CHARS` (4000 chars).

Changes — overflow recovery (TASK-2):
7. Test: `emergency_compact()` with 5 turn groups → 2 groups + static marker.
8. Test: `emergency_compact()` with ≤2 groups → returns `None`.
9. Test: `_is_context_overflow()` identifies 413 and context-length patterns. Must cover both `str` body and `dict` body (OpenAI sends `dict`, Ollama may send `str`). Also test bare 400 without context-length body → returns `False`.

Changes — compact_assistant_responses (TASK-3):
10. Test: `_truncate_proportional()` preserves 25% head + 75% tail + marker.
11. Test: `compact_assistant_responses()` — old 50K `TextPart` → 2.5K; last turn group untouched.
12. Test: `ToolReturnPart` and `UserPromptPart` untouched by `compact_assistant_responses`.

prerequisites: [TASK-1, TASK-2, TASK-3]
done_when: `uv run pytest tests/test_history.py tests/test_context_compaction.py -v` green.

## Testing

- TASK-1: `_build_summarizer_prompt()` is pure — testable without LLM. `_gather_compaction_context()` is NOT pure — it does filesystem I/O (always-on memories) and accesses `ctx.deps.session` state. Tests need real `RunContext[CoDeps]` with `tmp_path` for memory files (see TASK-4 test #6). Summary quality not unit-testable.
- TASK-2: `emergency_compact()` and `_is_context_overflow()` are pure functions. Integration: Ollama `llm_num_ctx=100`.
- TASK-3: `_truncate_proportional()` is pure. `compact_assistant_responses()` testable with constructed messages.
- No mocks. Real `CoDeps`, real `RunContext`. Timeouts on any LLM call.

## Priority Order

All three tasks are independent — no dependency chain.

```text
TASK-1 (structured template + context enrichment) ──→ TASK-4 (tests)
TASK-2 (overflow recovery) ─────────────────────────┘
TASK-3 (compact_assistant_responses) ───────────────┘
```

Recommended order: TASK-1 first (highest quality impact), then TASK-2 (safety net for overflow), then TASK-3 (response compaction — lowest priority, defer if TASK-1 alone brings summary quality to acceptable level).

## Open Questions

None.

## Review History

### Round 1 — TL + Core Dev + PO

Plan approved. C2 review: Core Dev `Blocking: none`, PO `Blocking: none`. All C1 fixes verified. C2 minors (3 CD + 2 PO) all adopted or rejected with rationale applied to plan above.

### Round 2 — TL + PO (2026-04-05)

Deep pass on plan against current code state. All line references verified correct. Four fixes applied:

1. **I3 (TASK-3 grace zone)**: `estimate_message_tokens()` is list-level — cannot compute per-message grace zone boundary. Fixed: TASK-3 now specifies inline `len(content) // 4` accumulation from the tail backward. Updated both the High-Level Design pseudocode and TASK-3 implementation step.
2. **P5 (context enrichment cap)**: `_gather_compaction_context` output was uncapped — risk of summarizer token overflow with many file paths/todos/memories. Fixed: added `_CONTEXT_MAX_CHARS = 4_000` cap. Updated behavioral constraint #2 and pseudocode.
3. **B4 (deferred import)**: `load_always_on_memories` import in `_history.py` was specified as top-level, inconsistent with `inject_opening_context`'s deferred-import pattern for cross-layer (context → tools) imports. Fixed: TASK-1.4 now specifies deferred import inside `_gather_compaction_context()`.
4. **I4 (emergency_compact marker)**: `emergency_compact` "static marker" was ambiguous. Fixed: explicitly reuses `_static_marker()` (`_history.py:163`) for consistency with `summarize_history_window()`. Updated both High-Level Design and TASK-2.4.

Verdict: **Gate 1 PASS** — right problem, correct scope, all blocking and important issues resolved.

### Round 3 — TL + PO (2026-04-05) — First-Principles Pass

Traced each design choice against the functional goal (better compaction summaries, overflow safety, bloat reduction). Five fixes applied:

1. **B1 (overflow fallthrough)**: When `_is_context_overflow` matches but `emergency_compact` returns `None` (≤2 groups), code fell through to the 400 reformulation handler — sending a misleading "reformulate your tool call" message for a context overflow. Fixed: go terminal immediately when overflow is detected but compaction is impossible. Updated pseudocode and TASK-2.3.
2. **B2 (`body: object | None`)**: `ModelHTTPError.body` is typed `object | None`, not `str`. OpenAI sends `dict` (`{"error": {"message": "context_length_exceeded"}}`), Ollama may send `str`. Plan assumed string matching. Fixed: `_is_context_overflow` must use `str(e.body)` before pattern matching. Updated design section and TASK-2.2. TASK-4 test #9 updated to cover both body types.
3. **B3 (testing claim)**: Testing section claimed `_gather_compaction_context()` is a "pure extraction function" — it does filesystem I/O (always-on memories) and accesses `ctx.deps.session`. Fixed: corrected the testing section description.
4. **I1 (4K cap in TASK-1.4)**: `_CONTEXT_MAX_CHARS = 4_000` was in behavioral constraint #2 and pseudocode but not in TASK-1.4 implementation step. Fixed: added to TASK-1.4.
5. **I2 (TASK-3 over-engineering)**: Token-accumulation grace zone (40K tokens, two thresholds, per-message reverse scanning) added complexity for marginal benefit. `truncate_tool_results` already uses simpler last-turn-group protection. Fixed: simplified to same pattern — last turn group untouched, everything else capped at `OLDER_MSG_MAX_CHARS = 2_500`. Dropped `RECENT_MSG_MAX_CHARS` and `GRACE_ZONE_TOKENS`. Updated design section, TASK-3 steps, TASK-4 tests, done_when, and behavioral constraint #7.

Verdict: **Gate 1 PASS** — all blocking and important issues resolved. Design aligned with functional goals.

### Round 4 — TL + PO (2026-04-05) — End-to-End Flow Trace

Traced every data flow against functional goal. Verified: `ModelHTTPError.body` is `object | None`, `ThinkingPart.content` is `str`, `/compact` command backward-compatible with new `context` param (default `None`). Five fixes applied:

1. **B1 (second overflow fallthrough)**: Round 3's B1 fix only covered the first-attempt path. On **retry** after successful emergency compact, a second overflow skipped the if-block (`overflow_recovery_attempted=True`) and fell through to the 400 reformulation handler — same misleading message bug. Fixed: restructured guard so `_is_context_overflow` is the **outer** check that always resolves the error (compact+retry OR terminal) — inner `if not overflow_recovery_attempted` controls first vs retry. Updated pseudocode and TASK-2.3 with explicit design invariant.
2. **I1 (`emergency_compact` dropped_count)**: Plan said `_static_marker(dropped_count)` but never defined how to compute `dropped_count` from groups. Risk: developer counts groups instead of messages. Fixed: specified `sum(len(g.messages) for g in groups[1:-1])` in both design section and TASK-2.4.
3. **I2 (stale Outcome #5)**: Said "Two-tier" — stale after Round 3 simplification to single-tier. Fixed.
4. **I3 (missing 4K cap test)**: Behavioral constraint #2 mandates 4K cap but no TASK-4 test covered it. Fixed: added test 6b.
5. **M1 (`_truncate_proportional` no-op)**: No spec for `len(text) <= max_chars` case. Fixed: specified defensive no-op return in TASK-3.2.

Verdict: **Gate 1 PASS** — all paths traced, all edge cases covered. Plan aligned with functional goals.

### Round 5 — TL + PO (2026-04-05) — Delivery Concreteness + Test Completeness

Systematic audit of done_when concreteness for each task, test coverage matrix against functional deliverables, and cross-task dependency verification. Verified `load_always_on_memories` → `load_memories` is exception-safe (per-file try/except at `memory.py:131`), `args_as_dict()` never raises by default, `session_todos` is plain attribute access. Four fixes applied:

1. **TASK-1 done_when non-concrete**: Only grepped for template sections (OR-match — any 1 of 5 passes). Didn't verify `_build_summarizer_prompt`, `context` param, `FILE_TOOLS`, `_gather_compaction_context`, or `_CONTEXT_MAX_CHARS`. A developer could add only the template and pass done_when. Fixed: expanded to 7 targeted grep checks covering all functional deliverables.
2. **TASK-4 test #1 missing (None, True) case**: `_build_summarizer_prompt(template, None, True)` — personality but no context — is the `index_session_summary` and `/compact` path. Not covered in the 3-case spec. Fixed: expanded to 4 explicit combinations (all permutations of context × personality).
3. **TASK-3 ToolCallPart not in "Does NOT touch" list**: `ToolCallPart.args` in `ModelResponse` is the source for TASK-1's file path extraction. The TextPart/ThinkingPart iteration naturally skips it, but the cross-task dependency should be documented. Fixed: added `ToolCallPart` to both design pseudocode and TASK-3.3 implementation step.
4. **`_build_summarizer_prompt` guard condition**: Plan didn't specify whether to check `if context is not None:` vs `if context:`. The truthy check handles both `None` and `""` defensively. Fixed: specified `if context:` in TASK-1.2.

Verdict: **Gate 1 PASS** — all done_when concrete, all test cases cover functional deliverables, cross-task dependencies documented.

### Round 6 — TL + PO (2026-04-05) — Systematic Verification

Walked every execution path in the design. Verified all 7 behavioral constraints reflected in implementation steps. Tested every done_when grep command against the current codebase for false-positive risk. Checked design↔implementation↔test consistency across all 4 tasks and cross-task dependencies.

**Design & functional goal: clean.** All paths traced, all constraints enforced, cross-task dependency (TASK-3 preserves ToolCallPart.args that TASK-1 reads) safe and documented.

Three done_when fixes applied (same bug class: commands that pass with incomplete implementation):

1. **TASK-1 done_when #4 — broken grep pipeline**: `grep "context" | grep "def summarize_messages"` always fails on multi-line function signatures. `"context"` param and `"def summarize_messages"` are on different lines. Verified against current code: piped grep returns empty. Fixed: `grep -A5 "def summarize_messages" | grep "context"` (reads 5 lines after the function definition).
2. **TASK-2 done_when — OR match**: `overflow_recovery_attempted\|_is_context_overflow` passes if either exists. Fixed: split into separate greps (field + function + integration wiring — 4 checks).
3. **TASK-3 done_when — OR match**: `compact_assistant_responses\|OLDER_MSG_MAX_CHARS` passes if either exists. Fixed: split into separate greps (processor + constant + helper + registration — 4 checks).

No design, functional, or test coverage issues found. Severity converging to minor (done_when syntax only).

Verdict: **Gate 1 PASS.**

### Round 7 — TL + PO (2026-04-05) — Adversarial First-Principles Pass

Attempted to break each design element rather than find incremental issues. For each task: verified problem→design alignment, checked implementation steps for ambiguity, tested done_when for false positives, traced edge cases (empty history, 1-2 turns, no ToolCallPart, all context sources empty, status 500 with context body, overflow retry with `current_input=None`).

**No issues found.** Plan has converged (R2: 4 fixes, R3: 5, R4: 5, R5: 4, R6: 3, R7: 0).

Verdict: **Gate 1 PASS.**

> Ready to proceed: `/orchestrate-dev context-compaction-quality`

## Delivery Summary — 2026-04-05

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep checks (7/7) + pytest green | ✓ pass |
| TASK-2 | grep checks (4/4) + pytest green | ✓ pass |
| TASK-3 | grep checks (4/4) + pytest green | ✓ pass |
| TASK-4 | `uv run pytest tests/test_history.py tests/test_context_compaction.py -v` green (40/40) | ✓ pass |

**Tests:** full suite — 331 passed, 0 failed (93s)
**Independent Review:** 1 blocking (fixed: `compact_assistant_responses` was constructing new `TextPart`/`ThinkingPart` objects, dropping metadata — changed to in-place `part.content` mutation) / 3 minor (module docstring updated; pre-existing table separator fixed; out-of-scope renames noted)
**Doc Sync:** fixed (DESIGN-context.md: processor chain 4→5 in 5 locations + context enrichment + overflow recovery descriptions; DESIGN-core-loop.md: processor chain 4→5 + roles table)

**Overall: DELIVERED**
All 4 tasks shipped. Structured summary template with context enrichment, overflow/413 recovery, and assistant response compaction are live. 17 new tests cover all functional deliverables.

## Implementation Review — 2026-04-06

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | grep checks (7/7) + pytest green | ✓ pass | `_summarization.py:116-134` — 5-section structured template (Goal, Key Decisions, Working Set, Progress, Next Steps). `_summarization.py:155-170` — `_build_summarizer_prompt()` pure function, assembly order: template → context → personality. `_summarization.py:181-186` — `summarize_messages()` gains `context: str \| None = None`. `_history.py:220-222` — `FILE_TOOLS` frozenset. `_history.py:369-426` — `_gather_compaction_context()` extracts 4 sources (file paths, todos, always-on memories, prior summary), deferred import at :408, cap at `_CONTEXT_MAX_CHARS=4000` (:426). `_history.py:508` — enrichment passed to `summarize_messages(context=enrichment)` at :516. `_summarization.py:203-222` — `index_session_summary` intentionally omits `context=`. |
| TASK-2 | grep checks (4/4) + pytest green | ✓ pass | `_orchestrate.py:108` — `overflow_recovery_attempted: bool = False` on `_TurnState`. `_orchestrate.py:438-448` — `_is_context_overflow()` checks status_code ∈ {400,413} AND `str(e.body)` pattern match (coerces `object\|None`). `_orchestrate.py:516-530` — overflow handler BEFORE 400 reformulation (:533), outer guard resolves completely (compact+retry OR terminal — never falls through). `_history.py:434-444` — `emergency_compact()` uses `group_by_turn()`, returns `None` if ≤2 groups, `dropped_count=sum(len(g.messages) for g in groups[1:-1])`, reuses `_static_marker()`. |
| TASK-3 | grep checks (4/4) + pytest green | ✓ pass | `_history.py:313` — `OLDER_MSG_MAX_CHARS = 2_500`. `_history.py:316-327` — `_truncate_proportional()` with defensive no-op. `_history.py:330-358` — `compact_assistant_responses()` protects last turn group, caps `TextPart`/`ThinkingPart` via in-place `part.content` mutation (:353), does NOT touch `ToolCallPart`/`ToolReturnPart`/`UserPromptPart`. `agent.py:290-296` — registered as processor #2 in chain. |
| TASK-4 | 40/40 tests green | ✓ pass | `test_context_compaction.py:196-232` — 4 `_build_summarizer_prompt` combinations. `test_history.py:404-503` — 7 `_gather_compaction_context` tests (file paths, todos, prior summary, empty, always-on memories, 4K cap). `test_history.py:511-547` — 2 `emergency_compact` tests. `test_history.py:555-582` — 5 `_is_context_overflow` tests (413, 400+dict, 400+str, bare 400, 500). `test_history.py:590-664` — 4 `compact_assistant_responses`/`_truncate_proportional` tests. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Missing overflow recovery row in error matrix | `docs/DESIGN-core-loop.md:251` | minor | Added context-overflow row documenting `_is_context_overflow` + `emergency_compact()` one-shot recovery |

### Tests
- Command: `uv run pytest -v`
- Result: 331 passed, 0 failed
- Log: `.pytest-logs/20260406-review-impl.log`

### Doc Sync
- Scope: narrow — DESIGN-core-loop.md section 2.5 error matrix
- Result: fixed: added context-overflow error handling row

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components online (LLM, Shell, Google, Web Search, MCP, Database, Project Config)
- No chat loop test needed — processors are internal pipeline; behavioral surface unchanged (no new CLI commands, no output format changes)
- `success_signal`: all four tasks are N/A (internal quality improvements, observable only in long sessions or overflow scenarios)

### Overall: PASS
All 4 tasks verified against spec with file:line evidence. 15/15 done_when grep checks match. 331/331 tests green. One minor doc gap fixed (DESIGN-core-loop.md error matrix). Ship-ready.
