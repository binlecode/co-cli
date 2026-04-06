"""History processors for automatic context governance.

Processors are chained via ``Agent(history_processors=[...])``. They run
before every model request and transform the message list in-place.

Public API (registered on the agent):
    truncate_tool_results        — sync, content-clears compactable tool results by recency
    compact_assistant_responses  — sync, caps large TextPart/ThinkingPart in older ModelResponse
    detect_safety_issues         — sync, doom-loop detection + shell reflection cap
    inject_opening_context       — async, injects recalled memories on each new user turn
    summarize_history_window     — async, summarizes middle messages via inline LLM or static marker
"""

from __future__ import annotations

import hashlib
import json
import logging

from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli._model_factory import ResolvedModel
from co_cli.config import ROLE_SUMMARIZATION
from co_cli.context._summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.context._types import MemoryRecallState, SafetyState
from co_cli.deps import CoDeps

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class TurnGroup:
    """A contiguous group of messages forming one user turn.

    Boundary detection: a new group starts at each ``ModelRequest`` containing
    a ``UserPromptPart``.  Messages before the first such boundary form group 0.
    """

    messages: list[ModelMessage]
    start_index: int


_CompactionBoundaries = tuple[int, int, int]
"""(head_end, tail_start, dropped_count) — None when no valid boundary exists."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn_group(msgs: list[ModelMessage], start: int) -> TurnGroup:
    """Construct a TurnGroup from a contiguous slice of messages."""
    return TurnGroup(messages=list(msgs), start_index=start)


def group_by_turn(messages: list[ModelMessage]) -> list[TurnGroup]:
    """Group messages into turn-sized units at ``UserPromptPart`` boundaries.

    A new group starts at each ``ModelRequest`` that contains a
    ``UserPromptPart`` (not just ``ToolReturnPart``).  Messages before the
    first such boundary form group 0.
    """
    if not messages:
        return []

    groups: list[TurnGroup] = []
    current_msgs: list[ModelMessage] = []
    current_start: int = 0

    for i, msg in enumerate(messages):
        is_boundary = (
            isinstance(msg, ModelRequest)
            and any(isinstance(p, UserPromptPart) for p in msg.parts)
        )
        if is_boundary and current_msgs:
            groups.append(_make_turn_group(current_msgs, current_start))
            current_msgs = []
            current_start = i
        current_msgs.append(msg)

    if current_msgs:
        groups.append(_make_turn_group(current_msgs, current_start))

    return groups


def groups_to_messages(groups: list[TurnGroup]) -> list[ModelMessage]:
    """Flatten turn groups back to a message list."""
    result: list[ModelMessage] = []
    for group in groups:
        result.extend(group.messages)
    return result


def find_first_run_end(messages: list[ModelMessage]) -> int:
    """Return the index (inclusive) of the first ModelResponse with a TextPart or ThinkingPart.

    This anchors the "first run" boundary — everything up to and including
    this message belongs to the initial exchange that establishes session
    context.  If no such message exists, returns 0 (keep nothing pinned).

    ThinkingPart-only responses (extended thinking with no text) are accepted
    as valid anchors — they represent the first substantive model output and
    must not be dropped from the head.

    Design note: if the first ModelResponse is tool-only (no TextPart or
    ThinkingPart), this returns 0, so head_end=1 — only the initial
    ModelRequest is pinned. The first run's tool call/return cycle falls into
    the dropped middle section.
    """
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelResponse):
            if any(isinstance(p, (TextPart, ThinkingPart)) for p in msg.parts):
                return i
    return 0


def _static_marker(dropped_count: int) -> ModelRequest:
    """Build a structurally valid placeholder for dropped messages."""
    return ModelRequest(parts=[
        UserPromptPart(
            content=(
                f"[Earlier conversation trimmed — {dropped_count} messages "
                "removed to stay within context budget]"
            ),
        ),
    ])


# ---------------------------------------------------------------------------
# Shared boundary helper
# ---------------------------------------------------------------------------


def _compute_compaction_boundaries(
    messages: list[ModelMessage],
) -> _CompactionBoundaries | None:
    """Compute head/tail boundary positions for a compaction pass.

    Returns ``(head_end, tail_start, dropped_count)`` or ``None`` when no
    clean boundary exists or there is nothing to drop.
    """
    first_run_end = find_first_run_end(messages)
    head_end = first_run_end + 1
    tail_count = max(4, len(messages) // 2)
    raw_tail_start = max(head_end, len(messages) - tail_count)
    # Snap to the nearest group boundary at or after raw_tail_start
    groups = group_by_turn(messages)
    tail_start = len(messages)
    for group in groups:
        if group.start_index >= raw_tail_start:
            tail_start = group.start_index
            break
    if tail_start >= len(messages) or tail_start <= head_end:
        return None
    return (head_end, tail_start, tail_start - head_end)


# ---------------------------------------------------------------------------
# Processor helpers (shared by #1 and #2)
# ---------------------------------------------------------------------------

OLDER_MSG_MAX_CHARS = 2_500

FILE_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "find_in_files", "list_directory",
})

COMPACTABLE_TOOLS: frozenset[str] = frozenset({
    "read_file", "run_shell_command", "find_in_files",
    "list_directory", "web_search", "web_fetch",
})
COMPACTABLE_KEEP_RECENT = 5
_CLEARED_PLACEHOLDER = "[tool result cleared — older than 5 most recent calls]"


def _find_last_turn_start(messages: list[ModelMessage]) -> int:
    """Return the index of the last ModelRequest containing a UserPromptPart.

    Returns 0 when no such message exists (protect nothing — degenerate case).
    """
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in messages[i].parts):
                return i
    return 0


def _truncate_proportional(text: str, max_chars: int, head_ratio: float = 0.25) -> str:
    """Truncate text keeping head(head_ratio) + tail(1-head_ratio) with a marker between."""
    if len(text) <= max_chars:
        return text
    marker = "\n[...truncated...]\n"
    available = max_chars - len(marker)
    if available <= 0:
        # max_chars too small for marker — hard truncate
        return text[:max_chars]
    head_size = int(available * head_ratio)
    tail_size = available - head_size
    return text[:head_size] + marker + text[-tail_size:]


# ---------------------------------------------------------------------------
# 1. truncate_tool_results (sync — no I/O)
# ---------------------------------------------------------------------------


def truncate_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Content-clear compactable tool results older than the 5 most recent per tool type.

    Protects the last turn (everything from the last UserPromptPart onward).
    Non-compactable tools pass through intact regardless of count.

    Registered as the *first* history processor — cheap in-memory work, no
    LLM call.  ``ctx`` is required by pydantic-ai's history processor
    signature but no config fields are accessed.
    """
    boundary = _find_last_turn_start(messages)
    if boundary == 0:
        return messages

    older = messages[:boundary]

    # Reverse scan: build keep_set of part object ids for the 5 most
    # recent per compactable tool type.
    keep_ids: set[int] = set()
    seen_counts: dict[str, int] = {}

    for msg in reversed(older):
        if not isinstance(msg, ModelRequest):
            continue
        for part in reversed(msg.parts):
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name not in COMPACTABLE_TOOLS:
                continue
            count = seen_counts.get(part.tool_name, 0)
            if count < COMPACTABLE_KEEP_RECENT:
                keep_ids.add(id(part))
            seen_counts[part.tool_name] = count + 1

    # Forward pass: content-clear compactable parts not in keep_ids
    for msg in older:
        if not isinstance(msg, ModelRequest):
            continue
        new_parts: list = []
        msg_modified = False
        for part in msg.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name in COMPACTABLE_TOOLS
                and id(part) not in keep_ids
            ):
                new_parts.append(ToolReturnPart(
                    tool_name=part.tool_name,
                    content=_CLEARED_PLACEHOLDER,
                    tool_call_id=part.tool_call_id,
                ))
                msg_modified = True
            else:
                new_parts.append(part)
        if msg_modified:
            msg.parts = new_parts

    return messages


# ---------------------------------------------------------------------------
# 2. compact_assistant_responses (sync — no I/O)
# ---------------------------------------------------------------------------


def compact_assistant_responses(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Cap large TextPart/ThinkingPart in older ModelResponse messages.

    Protects the last turn (everything from the last UserPromptPart onward).
    Does NOT touch ToolCallPart (args are critical for file path extraction),
    ToolReturnPart, or UserPromptPart.  Mutates in-place — no list rebuild.
    """
    boundary = _find_last_turn_start(messages)
    if boundary == 0:
        return messages

    for msg in messages[:boundary]:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if isinstance(part, (TextPart, ThinkingPart)) and len(part.content) > OLDER_MSG_MAX_CHARS:
                part.content = _truncate_proportional(part.content, OLDER_MSG_MAX_CHARS)

    return messages


# ---------------------------------------------------------------------------
# Context enrichment for summarization
# ---------------------------------------------------------------------------

_CONTEXT_MAX_CHARS = 4_000
_SUMMARY_MARKER_PREFIX = "[Summary of"


def _gather_compaction_context(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    dropped: list[ModelMessage],
) -> str | None:
    """Gather side-channel context for the summarizer from sources that survive truncation.

    Sources:
    1. File working set from ToolCallPart.args (never truncated by processor #1)
    2. Pending session todos from ctx.deps.session
    3. Always-on memories (the summarizer is a separate agent without the main agent's dynamic layers)
    4. Prior-summary text from dropped messages

    Returns None when no context was gathered.
    """
    context_parts: list[str] = []

    # 1. File working set — extracted from ToolCallPart.args (never truncated)
    file_paths: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name in FILE_TOOLS:
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
            todo_lines = [f"- [{t.get('status', 'pending')}] {t.get('content', '?')}" for t in pending[:10]]
            context_parts.append("Active tasks:\n" + "\n".join(todo_lines))

    # 3. Always-on memories — standing context the model always sees
    from co_cli.tools.memory import load_always_on_memories
    memories = load_always_on_memories(ctx.deps.config.memory_dir)
    if memories:
        mem_lines = [m.content[:200] for m in memories[:5]]
        context_parts.append("Standing memories:\n" + "\n".join(mem_lines))

    # 4. Prior-summary text from dropped messages
    for msg in dropped:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    if part.content.startswith(_SUMMARY_MARKER_PREFIX):
                        context_parts.append(f"Prior summary:\n{part.content}")

    if not context_parts:
        return None

    result = "\n\n".join(context_parts)
    return result[:_CONTEXT_MAX_CHARS] if len(result) > _CONTEXT_MAX_CHARS else result


# ---------------------------------------------------------------------------
# Emergency compaction (no LLM — used by overflow recovery)
# ---------------------------------------------------------------------------


def emergency_compact(messages: list[ModelMessage]) -> list[ModelMessage] | None:
    """Static emergency compaction for overflow recovery — no LLM call.

    Keeps first group + last group + static marker between.
    Returns None if ≤2 groups (nothing to compact).
    """
    groups = group_by_turn(messages)
    if len(groups) <= 2:
        return None
    dropped_count = sum(len(g.messages) for g in groups[1:-1])
    return groups_to_messages([groups[0]]) + [_static_marker(dropped_count)] + groups_to_messages([groups[-1]])


# ---------------------------------------------------------------------------
# 5. summarize_history_window (async — LLM call)
# ---------------------------------------------------------------------------


async def summarize_history_window(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Drop middle messages when history exceeds the token budget threshold.

    Triggers when estimated token count exceeds 85% of budget.

    Keeps:
      - **head** — first run's messages (up to first TextPart response)
      - **tail** — last N messages (most relevant recent context)
    Drops:
      - everything in between, replaced by an inline LLM summary when
        possible, else a static marker (circuit-breaker fallback)

    Summarisation runs inline via ``summarize_messages()`` when compaction
    triggers. When ``model_registry`` is absent (sub-agents, tests) or the
    circuit breaker is tripped (3+ consecutive failures), falls back to a
    static marker without attempting an LLM call.

    Registered as the last history processor.
    """
    token_count = latest_response_input_tokens(messages)
    if token_count == 0:
        token_count = estimate_message_tokens(messages)
    budget = resolve_compaction_budget(ctx.deps.config, ctx.deps.model_registry)
    token_threshold = int(budget * 0.85)

    if token_count <= token_threshold:
        return messages

    bounds = _compute_compaction_boundaries(messages)
    if bounds is None:
        return messages

    head_end, tail_start, dropped_count = bounds
    dropped = messages[head_end:tail_start]

    # Inline summarisation — single code path, no pre-computation
    summary_text: str | None = None
    registry = ctx.deps.model_registry

    if registry is None:
        # Configuration absence (sub-agents, tests, minimal bootstrap) —
        # not a transient failure, do not increment compaction_failure_count.
        log.info("Sliding window: model_registry absent, using static marker")
    elif ctx.deps.runtime.compaction_failure_count >= 3:
        log.warning("Sliding window: circuit breaker active (>= 3 consecutive failures), using static marker")
    else:
        from co_cli.display._core import console
        console.print("[dim]Compacting conversation...[/dim]")
        # Context enrichment — gather side-channel context only when the LLM
        # summarizer will actually run. Skipped on static-marker fallback paths
        # (no registry, circuit breaker) to avoid wasted I/O.
        enrichment = _gather_compaction_context(ctx, messages, dropped)
        _none_resolved = ResolvedModel(model=None, settings=None)
        resolved = registry.get(ROLE_SUMMARIZATION, _none_resolved)
        try:
            summary_text = await summarize_messages(
                dropped,
                resolved,
                personality_active=bool(ctx.deps.config.personality),
                context=enrichment,
            )
            ctx.deps.runtime.compaction_failure_count = 0
        except (ModelHTTPError, ModelAPIError) as e:
            log.warning("Inline compaction summarization failed: %s", e)
            ctx.deps.runtime.compaction_failure_count += 1

    if summary_text is not None:
        summary_marker = ModelRequest(parts=[
            UserPromptPart(
                content=(
                    f"[Summary of {dropped_count} earlier messages]\n{summary_text}"
                ),
            ),
        ])
        log.info("Sliding window: summarised %d messages inline", dropped_count)
    else:
        summary_marker = _static_marker(dropped_count)

    return messages[:head_end] + [summary_marker] + messages[tail_start:]


# ---------------------------------------------------------------------------
# 4. inject_opening_context (async — memory recall, no LLM)
# ---------------------------------------------------------------------------


def _get_last_user_message(messages: list[ModelMessage]) -> str | None:
    """Extract the text of the most recent UserPromptPart from messages."""
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return None


def _count_user_turns(messages: list[ModelMessage]) -> int:
    """Count ModelRequest messages that contain a non-system UserPromptPart."""
    count = 0
    for msg in messages:
        if isinstance(msg, ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in msg.parts):
                count += 1
    return count


async def inject_opening_context(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Inject recalled memories on every new user turn.

    Runs before every model request. Recall fires unconditionally on each
    new user turn — no heuristic gate. recall_memory is FTS5/BM25 or grep
    fallback — zero LLM cost in both cases. Returns empty when nothing matches.

    State is stored on ctx.deps.session.memory_recall_state.
    """
    # INTENTIONAL DEVIATION from pydantic-ai's pure-transformer contract:
    # This processor writes to ctx.deps.session.memory_recall_state
    # (last_recall_user_turn, recall_count). Pure transformers should not mutate deps.
    #
    # Why state cannot be local: same reasoning as detect_safety_issues() — fresh
    # call per request, state would not survive across segments.
    #
    # Safety invariant: memory_recall_state is initialised fresh per session in
    # CoSessionState.__post_init__; it does not leak across sessions.
    state: MemoryRecallState = ctx.deps.session.memory_recall_state
    state.model_request_count += 1  # keep for observability

    user_turn_count = _count_user_turns(messages)

    # Find the current user message
    user_msg = _get_last_user_message(messages)
    if not user_msg:
        return messages

    if user_turn_count <= state.last_recall_user_turn:
        return messages

    # Recall memories for the current topic
    from co_cli.tools.memory import recall_memory

    try:
        result = await recall_memory(ctx, user_msg, max_results=3)
        state.recall_count += 1
        state.last_recall_user_turn = user_turn_count
    except Exception:
        log.debug("inject_opening_context: recall_memory failed", exc_info=True)
        return messages

    if (result.metadata or {}).get("count", 0) == 0:
        return messages

    # Inject as a system message at the end of the message list
    memory_content = result.return_value
    max_chars = ctx.deps.config.memory_injection_max_chars
    if len(memory_content) > max_chars:
        memory_content = memory_content[:max_chars]
    injection = ModelRequest(parts=[
        SystemPromptPart(
            content=f"Relevant memories:\n{memory_content}",
        ),
    ])
    return messages + [injection]


# ---------------------------------------------------------------------------
# 3. detect_safety_issues (sync — doom loop + shell reflection cap)
# ---------------------------------------------------------------------------


def detect_safety_issues(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Scan recent tool calls for doom loops and shell error streaks.

    State is stored on ctx.deps.runtime.safety_state (turn-scoped, reset per turn).
    Thresholds from ctx.deps.config.doom_loop_threshold and ctx.deps.config.max_reflections.
    """
    # INTENTIONAL DEVIATION from pydantic-ai's pure-transformer contract:
    # This processor writes to ctx.deps.runtime.safety_state (doom_loop_injected,
    # reflection_injected). Pure transformers should not mutate deps.
    #
    # Why state cannot be local: pydantic-ai constructs a fresh processor call per
    # model request. Local variables would not survive across segments within a
    # single turn (e.g. initial segment + approval-resume segments).
    #
    # Safety invariant: safety_state is reset by reset_for_turn() at each foreground
    # turn entry, so cross-turn state leakage cannot occur.
    state: SafetyState | None = ctx.deps.runtime.safety_state
    if state is None:
        return messages
    if state.doom_loop_injected and state.reflection_injected:
        return messages

    doom_threshold = ctx.deps.config.doom_loop_threshold
    max_refl = ctx.deps.config.max_reflections

    # Scan recent messages in reverse to measure the most-recent contiguous streak
    # for each safety check.
    #
    # Key invariant: once a streak is broken (different call / non-error return),
    # older messages cannot extend it — stop tracking that counter immediately.
    # Without this early-exit, an older differing entry resets the counter and
    # the final value reflects ancient history rather than the most-recent run.
    consecutive_same: int = 0
    last_hash: str | None = None
    doom_streak_done: bool = False  # True after the first hash mismatch

    consecutive_shell_errors: int = 0
    shell_streak_done: bool = False  # True after the first non-shell-error return

    calls_scanned: int = 0

    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    if not doom_streak_done:
                        args_str = json.dumps(
                            part.args.args_dict() if hasattr(part.args, "args_dict") else str(part.args),
                            sort_keys=True,
                        )
                        h = hashlib.md5(
                            f"{part.tool_name}:{args_str}".encode()
                        ).hexdigest()
                        if last_hash is None:
                            consecutive_same = 1
                            last_hash = h
                        elif h == last_hash:
                            consecutive_same += 1
                        else:
                            # Streak broken: consecutive_same holds the most-recent count
                            doom_streak_done = True
                    calls_scanned += 1
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and not shell_streak_done:
                    content = part.content
                    if isinstance(content, str):
                        c = content.lower()
                        # Require "error" at the start of the string, or the pydantic-ai
                        # ModelRetry wrapper prefix ("Shell: command failed / unexpected error").
                        # Substring match on the whole output caused false positives on
                        # informational text like "3 tests passed, 0 errors".
                        str_is_error = (
                            c.startswith("error")
                            or c.startswith("shell: command failed")
                            or c.startswith("shell: unexpected error")
                        )
                    else:
                        str_is_error = False
                    is_error = (
                        (isinstance(part.metadata, dict) and part.metadata.get("error"))
                        or (isinstance(content, str) and part.tool_name == "run_shell_command"
                            and str_is_error)
                    )
                    if is_error and part.tool_name == "run_shell_command":
                        consecutive_shell_errors += 1
                    else:
                        # Streak broken: consecutive_shell_errors holds the most-recent count
                        shell_streak_done = True

        if (doom_streak_done and shell_streak_done) or calls_scanned > 10:
            break

    injections: list[ModelMessage] = []

    # Doom loop detection
    if not state.doom_loop_injected and consecutive_same >= doom_threshold:
        injections.append(ModelRequest(parts=[
            SystemPromptPart(
                content=(
                    "You are repeating the same tool call. "
                    "Try a different approach or explain why you are stuck."
                ),
            ),
        ]))
        state.doom_loop_injected = True
        log.warning("Doom loop detected: %d identical tool calls", consecutive_same)

    # Shell reflection cap
    if not state.reflection_injected and consecutive_shell_errors >= max_refl:
        injections.append(ModelRequest(parts=[
            SystemPromptPart(
                content=(
                    "Shell reflection limit reached. Ask the user for help "
                    "or try a fundamentally different approach."
                ),
            ),
        ]))
        state.reflection_injected = True
        log.warning("Shell reflection cap: %d consecutive errors", consecutive_shell_errors)

    if injections:
        return messages + injections
    return messages
