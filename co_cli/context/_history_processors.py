"""Pure history transformers registered via ``Agent(history_processors=[...])``.

Each processor runs before every model request and returns a transformed
message list. None of them mutate ``CoDeps`` or the original
``ModelMessage`` objects — ``_rewrite_tool_returns`` rebuilds only the
messages where at least one part changed.

Registered processors:
    dedup_tool_results     — collapses identical-content tool returns to back-references
    evict_old_tool_results   — content-clears compactable tool results by recency
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import replace

log = logging.getLogger(__name__)

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from co_cli.context._compaction_boundaries import _find_last_turn_start
from co_cli.context._dedup_tool_results import (
    build_dedup_part,
    dedup_key,
    is_dedup_candidate,
)
from co_cli.context._tool_result_markers import semantic_marker
from co_cli.deps import CoDeps
from co_cli.tools.categories import COMPACTABLE_TOOLS

COMPACTABLE_KEEP_RECENT = 5
"""Keep the N most-recent tool returns per compactable tool type; clear older.

Borrowed from ``fork-claude-code/services/compact/timeBasedMCConfig.ts:33``
(``keepRecent: 5``). Not convergent across peers — codex, hermes, and
opencode do not have per-tool recency retention. Not tuned specifically for
co-cli's tool surface; revisit via ``evals/eval_compaction_quality.py`` if a
retention/fidelity tradeoff becomes measurable.
"""

_CLEARED_PLACEHOLDER = (
    f"[tool result cleared — older than {COMPACTABLE_KEEP_RECENT} most recent calls]"
)
"""Last-resort fallback when ToolReturnPart.content is non-string (multimodal).

Normal path uses ``semantic_marker`` to produce per-tool descriptions that
preserve intent and outcome signal. The static placeholder survives only
for non-string content shapes where a marker cannot be generated.
"""


def _iter_tool_returns_reversed(messages: list[ModelMessage]) -> Iterator[ToolReturnPart]:
    """Yield ToolReturnParts from messages, reverse over both messages and parts."""
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in reversed(msg.parts):
            if isinstance(part, ToolReturnPart):
                yield part


def _rewrite_tool_returns(
    messages: list[ModelMessage],
    boundary: int,
    *,
    replacement_for: Callable[[ToolReturnPart], ToolReturnPart | None],
) -> list[ModelMessage]:
    """Rewrite ToolReturnParts in ``messages[:boundary]`` per the given policy.

    ``replacement_for(part)`` returns a new ToolReturnPart to substitute, or
    ``None`` for pass-through. Messages at or after ``boundary`` and every
    non-ModelRequest message are copied verbatim. A ModelRequest is only
    rebuilt via ``replace(msg, parts=...)`` when at least one part changed —
    otherwise the original message object is preserved.

    Shared by ``evict_old_tool_results`` and ``dedup_tool_results`` so both
    obey the same "boundary-protected, non-mutating" contract by construction.
    """
    result: list[ModelMessage] = []
    for idx, msg in enumerate(messages):
        if idx >= boundary or not isinstance(msg, ModelRequest):
            result.append(msg)
            continue
        new_parts: list = []
        modified = False
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                replacement = replacement_for(part)
                if replacement is not None:
                    new_parts.append(replacement)
                    modified = True
                    continue
            new_parts.append(part)
        result.append(replace(msg, parts=new_parts) if modified else msg)
    return result


def _build_latest_id_by_key(messages: list[ModelMessage]) -> dict[str, str]:
    """For each ``(tool_name, content-hash)`` key, record the ``tool_call_id`` of the latest occurrence.

    Reverse scan over ToolReturnParts eligible for dedup; the first
    observation in reverse order is the latest in forward order. The full
    message list (including the protected tail) is scanned so that tail copies
    are preferred as the back-reference target when they exist.
    """
    latest: dict[str, str] = {}
    for part in _iter_tool_returns_reversed(messages):
        if not is_dedup_candidate(part):
            continue
        key = dedup_key(part)
        if key not in latest:
            latest[key] = part.tool_call_id
    return latest


def _build_durable_call_ids(messages: list[ModelMessage], boundary: int) -> set[str]:
    """Return tool_call_ids that will survive ``evict_old_tool_results``.

    A part is durable if it lives in the protected tail (``messages[boundary:]``)
    or is among the ``COMPACTABLE_KEEP_RECENT`` most recent per tool_name in
    ``messages[:boundary]``. Used by ``dedup_tool_results`` to avoid emitting
    back-references that ``evict_old_tool_results`` will subsequently clear.
    """
    durable: set[str] = set()
    # Tail-protected: compactable returns at or after boundary always survive.
    for msg in messages[boundary:]:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name in COMPACTABLE_TOOLS:
                durable.add(part.tool_call_id)
    # Pre-tail: mirror truncate's keep-recent logic using tool_call_id strings.
    seen_counts: dict[str, int] = {}
    for part in _iter_tool_returns_reversed(messages[:boundary]):
        if part.tool_name not in COMPACTABLE_TOOLS:
            continue
        count = seen_counts.get(part.tool_name, 0)
        if count < COMPACTABLE_KEEP_RECENT:
            durable.add(part.tool_call_id)
        seen_counts[part.tool_name] = count + 1
    return durable


def dedup_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Collapse repeat returns of the same ``(tool_name, content)`` pair to back-references.

    For each compactable tool return whose content (≥ 200 chars, string)
    duplicates a more recent return of the same tool, replace with a 1-line
    back-reference marker naming the latest ``call_id``. Only the latest
    occurrence of each ``(tool, hash)`` retains full content.

    Protects the last turn via the same ``_find_last_turn_start`` boundary
    M2a uses. Non-string content and non-compactable tools pass through
    unchanged (same safety envelope as ``evict_old_tool_results``).

    Back-references are only emitted when the target ``latest_id`` is durable
    (will survive ``evict_old_tool_results``). A non-durable target means the
    back-reference would point to a cleared semantic marker rather than live
    content — those cases pass through unchanged.

    Registered as the **first** history processor — runs before
    ``evict_old_tool_results`` so the kept recent window has already been
    collapsed for identical repeats before recency-based clearing applies.
    """
    boundary = _find_last_turn_start(messages)
    if not boundary:
        return messages

    latest_id_by_key = _build_latest_id_by_key(messages)
    durable_call_ids = _build_durable_call_ids(messages, boundary)

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        if not is_dedup_candidate(part):
            return None
        latest_id = latest_id_by_key.get(dedup_key(part))
        if latest_id is None or latest_id == part.tool_call_id:
            return None
        if latest_id not in durable_call_ids:
            return None
        return build_dedup_part(part, latest_id)

    return _rewrite_tool_returns(messages, boundary, replacement_for=replacement_for)


def _build_keep_ids(older: list[ModelMessage]) -> set[int]:
    """Reverse scan: collect ids of the COMPACTABLE_KEEP_RECENT most recent parts per tool."""
    keep_ids: set[int] = set()
    seen_counts: dict[str, int] = {}
    for part in _iter_tool_returns_reversed(older):
        if part.tool_name not in COMPACTABLE_TOOLS:
            continue
        count = seen_counts.get(part.tool_name, 0)
        if count < COMPACTABLE_KEEP_RECENT:
            keep_ids.add(id(part))
        seen_counts[part.tool_name] = count + 1
    return keep_ids


def _build_call_id_to_args(messages: list[ModelMessage]) -> dict[str, dict]:
    """Index ``ToolCallPart.tool_call_id`` → args dict across all ModelResponses.

    Forward scan — later calls overwrite earlier ones on id collision, which
    does not happen in practice (ids are unique per call). Args are read via
    ``args_as_dict``; malformed args fall back to an empty dict so a single
    corrupt call cannot break the index for the rest of the conversation.
    """
    result: dict[str, dict] = {}
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart):
                continue
            try:
                args = part.args_as_dict()
            except Exception:
                args = {}
            result[part.tool_call_id] = args or {}
    return result


def _build_cleared_part(
    part: ToolReturnPart,
    call_id_to_args: dict[str, dict],
) -> ToolReturnPart:
    """Construct the replacement ToolReturnPart for an older-than-5 compactable return.

    Non-string content falls back to the static placeholder — markers need a
    readable content string for char/line/outcome heuristics.
    """
    content = part.content
    if not isinstance(content, str):
        replacement = _CLEARED_PLACEHOLDER
    else:
        args = call_id_to_args.get(part.tool_call_id, {})
        replacement = semantic_marker(part.tool_name, args, content)
    return ToolReturnPart(
        tool_name=part.tool_name,
        content=replacement,
        tool_call_id=part.tool_call_id,
    )


def evict_old_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Content-clear compactable tool results older than the 5 most recent per tool type.

    Replacement content is a per-tool semantic marker (see
    ``semantic_marker``) carrying tool name, key args, and a size/outcome
    signal. Falls back to the static ``_CLEARED_PLACEHOLDER`` when the
    ToolReturnPart carries non-string content.

    Protects the last turn (everything from the last UserPromptPart onward).
    Non-compactable tools pass through intact regardless of count.

    Registered after ``dedup_tool_results`` so the kept window has already
    been collapsed for identical repeats before recency-based clearing
    runs. Cheap in-memory work, no LLM call. ``ctx`` is required by
    pydantic-ai's history processor signature but no config fields are
    accessed.
    """
    boundary = _find_last_turn_start(messages)
    if not boundary:
        return messages

    keep_ids = _build_keep_ids(messages[:boundary])
    call_id_to_args = _build_call_id_to_args(messages)

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        if part.tool_name not in COMPACTABLE_TOOLS or id(part) in keep_ids:
            return None
        return _build_cleared_part(part, call_id_to_args)

    return _rewrite_tool_returns(messages, boundary, replacement_for=replacement_for)
