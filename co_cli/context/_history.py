"""History processors for automatic context governance.

Processors are chained via ``Agent(history_processors=[...])``. They run
before every model request and transform the message list in-place.

Public API (registered on the agent):
    inject_opening_context  — async, injects recalled memories on each new user turn
    truncate_tool_returns   — sync, truncates large ToolReturnPart.content
    detect_safety_issues    — sync, doom-loop detection + shell reflection cap
    truncate_history_window — async, drops middle messages + inline LLM summary or static marker
"""

from __future__ import annotations

import hashlib
import json
import logging

from dataclasses import dataclass
from typing import Any

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
from co_cli.context._compaction import (
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
class _CompactionBoundaries:
    """Head/tail boundary positions for a compaction pass.

    Produced by ``_compute_compaction_boundaries()`` and consumed by
    ``truncate_history_window()``.

    When ``valid`` is ``False``, no clean boundary could be found and the
    caller must skip compaction.
    """

    head_end: int
    tail_start: int
    dropped_count: int
    valid: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _align_tail_start(messages: list[ModelMessage], tail_start: int) -> int:
    """Walk tail_start forward to a clean user-turn boundary.

    Ensures the tail never starts at a ModelRequest containing ToolReturnPart
    whose matching ToolCallPart was dropped into the middle section.
    Returns len(messages) if no clean boundary exists (caller should skip drop).
    """
    while tail_start < len(messages):
        msg = messages[tail_start]
        if isinstance(msg, ModelRequest) and not any(
            isinstance(p, ToolReturnPart) for p in msg.parts
        ):
            break
        tail_start += 1
    return tail_start


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


def _content_length(content: Any) -> tuple[str, int]:
    """Normalise ToolReturnPart.content to a string and return (text, length).

    ``content`` may be ``str`` or ``dict`` (tool return convention).
    """
    if isinstance(content, str):
        return content, len(content)
    # dict → JSON-serialise for measurement
    text = json.dumps(content, ensure_ascii=False)
    return text, len(text)


# ---------------------------------------------------------------------------
# Shared boundary helper
# ---------------------------------------------------------------------------


def _compute_compaction_boundaries(
    messages: list[ModelMessage],
) -> _CompactionBoundaries:
    """Compute head/tail boundary positions for a compaction pass.

    Runs the full boundary calculation sequence and returns a
    ``_CompactionBoundaries`` with ``valid=False`` if no clean boundary
    exists or there is nothing to drop.
    """
    first_run_end = find_first_run_end(messages)
    head_end = first_run_end + 1
    tail_count = max(4, len(messages) // 2)
    tail_start = max(head_end, len(messages) - tail_count)
    tail_start = _align_tail_start(messages, tail_start)
    if tail_start >= len(messages) or tail_start <= head_end:
        return _CompactionBoundaries(
            head_end=head_end,
            tail_start=tail_start,
            dropped_count=0,
            valid=False,
        )
    return _CompactionBoundaries(
        head_end=head_end,
        tail_start=tail_start,
        dropped_count=tail_start - head_end,
        valid=True,
    )


# ---------------------------------------------------------------------------
# 1. Tool-output trim processor (sync — no I/O)
# ---------------------------------------------------------------------------


def truncate_tool_returns(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Truncate large ``ToolReturnPart.content`` in older messages.

    "Older" means everything except the **last exchange** (the trailing
    ``ModelRequest`` + ``ModelResponse`` pair that represents the current
    turn).  Recent tool output stays intact so the model can reason about
    the latest results.

    Registered as the *first* history processor — cheap string work, no
    LLM call.
    """
    threshold = ctx.deps.config.tool_output_trim_chars
    if threshold <= 0:
        return messages

    # Protect the last 2 messages (current turn).
    safe_tail = 2
    boundary = max(0, len(messages) - safe_tail)

    out: list[ModelMessage] = []
    for i, msg in enumerate(messages):
        if i >= boundary or not isinstance(msg, ModelRequest):
            out.append(msg)
            continue
        new_parts = []
        modified = False
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                if isinstance(part.content, dict):
                    d = dict(part.content)
                    disp = d.get("display", "")
                    if isinstance(disp, str) and len(disp) > threshold:
                        d["display"] = disp[:threshold] + f"\n[…truncated, {len(disp)} chars total]"
                        new_parts.append(ToolReturnPart(
                            tool_name=part.tool_name,
                            content=d,
                            tool_call_id=part.tool_call_id,
                        ))
                        modified = True
                        continue
                else:
                    text, length = _content_length(part.content)
                    if length > threshold:
                        truncated = text[:threshold] + f"\n[…truncated, {length} chars total]"
                        new_parts.append(ToolReturnPart(
                            tool_name=part.tool_name,
                            content=truncated,
                            tool_call_id=part.tool_call_id,
                        ))
                        modified = True
                        continue
            new_parts.append(part)
        if modified:
            out.append(ModelRequest(parts=new_parts))
        else:
            out.append(msg)
    return out


# ---------------------------------------------------------------------------
# 2. Sliding-window processor (async — LLM call)
# ---------------------------------------------------------------------------


async def truncate_history_window(
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
    if not bounds.valid:
        return messages

    head_end = bounds.head_end
    tail_start = bounds.tail_start
    dropped = messages[head_end:tail_start]
    dropped_count = bounds.dropped_count

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
        _none_resolved = ResolvedModel(model=None, settings=None)
        resolved = registry.get(ROLE_SUMMARIZATION, _none_resolved)
        try:
            summary_text = await summarize_messages(
                dropped,
                resolved,
                personality_active=bool(ctx.deps.config.personality),
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
# 3. Opening context injection (async — memory recall, no LLM)
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

    if result.get("count", 0) == 0:
        return messages

    # Inject as a system message at the end of the message list
    memory_content = result["display"]
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
# 4. Safety processor: doom loop detection + shell reflection cap
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
                        (isinstance(content, dict) and content.get("error"))
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
