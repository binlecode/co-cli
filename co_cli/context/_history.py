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
from pathlib import Path
from typing import cast

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
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

from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.context.summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.context.tool_categories import COMPACTABLE_TOOLS, FILE_TOOLS
from co_cli.context.types import MemoryRecallState, SafetyState
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
        is_boundary = isinstance(msg, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in msg.parts
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
        if isinstance(msg, ModelResponse) and any(
            isinstance(p, (TextPart, ThinkingPart)) for p in msg.parts
        ):
            return i
    return 0


def _static_marker(dropped_count: int) -> ModelRequest:
    """Build a structurally valid placeholder for dropped messages."""
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    f"This session is being continued from a previous conversation "
                    f"that ran out of context. {dropped_count} earlier messages were "
                    "removed. Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


def _summary_marker(dropped_count: int, summary_text: str) -> ModelRequest:
    """Build a structurally valid summary marker for compacted messages."""
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    f"portion ({dropped_count} messages).\n\n{summary_text}\n\n"
                    "Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


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

COMPACTABLE_KEEP_RECENT = 5
_CLEARED_PLACEHOLDER = "[tool result cleared — older than 5 most recent calls]"


def _find_last_turn_start(messages: list[ModelMessage]) -> int:
    """Return the index of the last ModelRequest containing a UserPromptPart.

    Returns 0 when no such message exists (protect nothing — degenerate case).
    """
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in messages[i].parts
        ):
            return i
    return 0


def _truncate_proportional(text: str, max_chars: int, head_ratio: float = 0.20) -> str:
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


def _build_keep_ids(older: list[ModelMessage]) -> set[int]:
    """Reverse scan: collect ids of the COMPACTABLE_KEEP_RECENT most recent parts per tool."""
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
    return keep_ids


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
    keep_ids = _build_keep_ids(older)

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
                new_parts.append(
                    ToolReturnPart(
                        tool_name=part.tool_name,
                        content=_CLEARED_PLACEHOLDER,
                        tool_call_id=part.tool_call_id,
                    )
                )
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
            if (
                isinstance(part, (TextPart, ThinkingPart))
                and len(part.content) > OLDER_MSG_MAX_CHARS
            ):
                part.content = _truncate_proportional(part.content, OLDER_MSG_MAX_CHARS)

    return messages


# ---------------------------------------------------------------------------
# Context enrichment for summarization
# ---------------------------------------------------------------------------

_CONTEXT_MAX_CHARS = 4_000
_SUMMARY_MARKER_PREFIX = "[Summary of"


def _gather_file_paths(messages: list[ModelMessage]) -> str | None:
    """Extract file working set from ToolCallPart.args (never truncated by processor #1)."""
    file_paths: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name in FILE_TOOLS:
                    args = part.args_as_dict()
                    path = args.get("path") or args.get("file_path")
                    if path:
                        file_paths.add(path)
    return f"Files touched: {', '.join(sorted(file_paths)[:20])}" if file_paths else None


def _gather_session_todos(todos: list) -> str | None:
    """Format pending session todos for compaction context."""
    if not todos:
        return None
    pending = [t for t in todos if t.get("status") not in ("completed", "cancelled")]
    if not pending:
        return None
    todo_lines = [
        f"- [{t.get('status', 'pending')}] {t.get('content', '?')}" for t in pending[:10]
    ]
    return "Active tasks:\n" + "\n".join(todo_lines)


def _gather_always_on_memories(memory_dir: Path) -> str | None:
    """Load always-on memories for compaction context."""
    from co_cli.memory.recall import load_always_on_memories

    memories = load_always_on_memories(memory_dir)
    if not memories:
        return None
    return "Standing memories:\n" + "\n".join(m.content[:200] for m in memories[:5])


def _gather_prior_summaries(dropped: list[ModelMessage]) -> str | None:
    """Extract prior summary text from dropped messages."""
    summaries: list[str] = []
    for msg in dropped:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.startswith(_SUMMARY_MARKER_PREFIX)
                ):
                    summaries.append(f"Prior summary:\n{part.content}")
    return "\n\n".join(summaries) if summaries else None


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
    context_parts = [
        p
        for p in [
            _gather_file_paths(messages),
            _gather_session_todos(ctx.deps.session.session_todos),
            _gather_always_on_memories(ctx.deps.memory_dir),
            _gather_prior_summaries(dropped),
        ]
        if p is not None
    ]
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
    return [
        *groups_to_messages([groups[0]]),
        _static_marker(dropped_count),
        *groups_to_messages([groups[-1]]),
    ]


async def _summarize_dropped_messages(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    dropped: list[ModelMessage],
    *,
    announce: bool,
) -> str | None:
    """Summarize dropped messages when the model and circuit breaker allow it."""
    if not ctx.deps.model:
        log.info("Compaction: model absent, using static marker")
        return None
    if ctx.deps.runtime.compaction_failure_count >= 3:
        log.warning(
            "Compaction: circuit breaker active (>= 3 consecutive failures), using static marker"
        )
        return None

    if announce:
        from co_cli.display._core import console

        console.print("[dim]Compacting conversation...[/dim]")

    enrichment = _gather_compaction_context(ctx, messages, dropped)
    try:
        summary_text = await summarize_messages(
            dropped,
            model=ctx.deps.model.model,
            model_settings=NOREASON_SETTINGS,
            personality_active=bool(ctx.deps.config.personality),
            context=enrichment,
        )
        ctx.deps.runtime.compaction_failure_count = 0
        return summary_text
    except (ModelHTTPError, ModelAPIError) as e:
        log.warning("Compaction summarization failed: %s", e)
        ctx.deps.runtime.compaction_failure_count += 1
        return None


def _preserve_search_tool_breadcrumbs(dropped: list[ModelMessage]) -> list[ModelMessage]:
    """Keep SDK search-tools discovery state across compaction boundaries."""
    return [
        msg
        for msg in dropped
        if isinstance(msg, ModelRequest)
        and any(isinstance(p, ToolReturnPart) and p.tool_name == "search_tools" for p in msg.parts)
    ]


async def recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage] | None:
    """Recover from provider context overflow with LLM summary or static fallback.

    Keeps the first turn group and last turn group, summarizing the middle when
    possible. Returns None when there is no safe middle region to drop.
    """
    groups = group_by_turn(messages)
    if len(groups) <= 2:
        return None

    dropped = groups_to_messages(groups[1:-1])
    dropped_count = len(dropped)
    summary_text = await _summarize_dropped_messages(
        ctx,
        messages,
        dropped,
        announce=False,
    )
    marker = (
        _summary_marker(dropped_count, summary_text)
        if summary_text is not None
        else _static_marker(dropped_count)
    )
    ctx.deps.runtime.history_compaction_applied = True
    return [
        *groups_to_messages([groups[0]]),
        marker,
        *_preserve_search_tool_breadcrumbs(dropped),
        *groups_to_messages([groups[-1]]),
    ]


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
    triggers. When ``deps.model`` is absent (sub-agents, tests) or the
    circuit breaker is tripped (3+ consecutive failures), falls back to a
    static marker without attempting an LLM call.

    Registered as the last history processor.
    """
    token_count = latest_response_input_tokens(messages)
    if token_count == 0:
        token_count = estimate_message_tokens(messages)
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    token_threshold = int(budget * 0.85)

    if token_count <= token_threshold:
        return messages

    bounds = _compute_compaction_boundaries(messages)
    if bounds is None:
        return messages

    head_end, tail_start, dropped_count = bounds
    dropped = messages[head_end:tail_start]

    summary_text = await _summarize_dropped_messages(
        ctx,
        messages,
        dropped,
        announce=True,
    )
    if summary_text is not None:
        summary_marker = _summary_marker(dropped_count, summary_text)
        log.info("Sliding window: summarised %d messages inline", dropped_count)
    else:
        summary_marker = _static_marker(dropped_count)

    ctx.deps.runtime.history_compaction_applied = True
    preserved_discovery = _preserve_search_tool_breadcrumbs(dropped)
    return [*messages[:head_end], summary_marker, *preserved_discovery, *messages[tail_start:]]


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
        if isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) for p in msg.parts):
            count += 1
    return count


async def inject_opening_context(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Inject recalled memories on every new user turn.

    Runs before every model request. Recall fires unconditionally on each
    new user turn — no heuristic gate. _recall_for_context is FTS5/BM25 or grep
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

    user_turn_count = _count_user_turns(messages)

    # Find the current user message
    user_msg = _get_last_user_message(messages)
    if not user_msg:
        return messages

    if user_turn_count <= state.last_recall_user_turn:
        return messages

    # Recall memories for the current topic
    from co_cli.tools.memory import _recall_for_context

    try:
        result = await _recall_for_context(ctx, user_msg, max_results=3)
        state.recall_count += 1
        state.last_recall_user_turn = user_turn_count
    except Exception:
        log.debug("inject_opening_context: _recall_for_context failed", exc_info=True)
        return messages

    if (result.metadata or {}).get("count", 0) == 0:
        return messages

    # Inject as a system message at the end of the message list
    # _recall_for_context always returns a str via tool_output(); cast narrows ToolReturnContent
    memory_content = cast("str", result.return_value)
    max_chars = ctx.deps.config.memory.injection_max_chars
    if len(memory_content) > max_chars:
        memory_content = memory_content[:max_chars]
    injection = ModelRequest(
        parts=[
            SystemPromptPart(
                content=f"Relevant memories:\n{memory_content}",
            ),
        ]
    )
    return [*messages, injection]


# ---------------------------------------------------------------------------
# 3. detect_safety_issues (sync — doom loop + shell reflection cap)
# ---------------------------------------------------------------------------


def _count_consecutive_same_calls(messages: list[ModelMessage]) -> int:
    """Count the most-recent contiguous streak of identical tool calls.

    Scans in reverse; stops when a different call is seen or after 10 calls.
    """
    consecutive_same: int = 0
    last_hash: str | None = None
    for msg in reversed(messages):
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart):
                continue
            args_str = json.dumps(
                part.args.args_dict()  # type: ignore[union-attr]  # part.args is str|dict|None at type level; hasattr guard is correct at runtime
                if hasattr(part.args, "args_dict")
                else str(part.args),
                sort_keys=True,
            )
            h = hashlib.md5(f"{part.tool_name}:{args_str}".encode()).hexdigest()
            if last_hash is None:
                consecutive_same = 1
                last_hash = h
            elif h == last_hash:
                consecutive_same += 1
            else:
                return consecutive_same
    return consecutive_same


def _is_shell_error_return(part: ToolReturnPart) -> bool:
    """Return True when the tool return represents a shell command error."""
    content = part.content
    if isinstance(content, str):
        c = content.lower()
        # Require "error" at the start or the pydantic-ai ModelRetry prefix.
        # Substring match on the whole output caused false positives on text like
        # "3 tests passed, 0 errors".
        str_is_error = (
            c.startswith("error")
            or c.startswith("shell: command failed")
            or c.startswith("shell: unexpected error")
        )
    else:
        str_is_error = False
    return (isinstance(part.metadata, dict) and bool(part.metadata.get("error"))) or (
        isinstance(content, str) and part.tool_name == "run_shell_command" and str_is_error
    )


def _count_consecutive_shell_errors(messages: list[ModelMessage]) -> int:
    """Count the most-recent contiguous streak of shell command errors.

    Scans in reverse; stops at the first non-error return.
    """
    count: int = 0
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if _is_shell_error_return(part) and part.tool_name == "run_shell_command":
                count += 1
            else:
                return count
    return count


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

    consecutive_same = _count_consecutive_same_calls(messages)
    consecutive_shell_errors = _count_consecutive_shell_errors(messages)

    injections: list[ModelMessage] = []

    # Doom loop detection
    if not state.doom_loop_injected and consecutive_same >= doom_threshold:
        injections.append(
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        content=(
                            "You are repeating the same tool call. "
                            "Try a different approach or explain why you are stuck."
                        ),
                    ),
                ]
            )
        )
        state.doom_loop_injected = True
        log.warning("Doom loop detected: %d identical tool calls", consecutive_same)

    # Shell reflection cap
    if not state.reflection_injected and consecutive_shell_errors >= max_refl:
        injections.append(
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        content=(
                            "Shell reflection limit reached. Ask the user for help "
                            "or try a fundamentally different approach."
                        ),
                    ),
                ]
            )
        )
        state.reflection_injected = True
        log.warning("Shell reflection cap: %d consecutive errors", consecutive_shell_errors)

    if injections:
        return messages + injections
    return messages
