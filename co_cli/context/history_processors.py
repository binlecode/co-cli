"""Pure history transformers registered via ``Agent(history_processors=[...])``.

Each processor runs before every model request and returns a transformed
message list. None of them mutate ``CoDeps`` or the original ``ModelMessage``
objects — ``_rewrite_tool_returns`` rebuilds only the messages where at least
one part changed.

Registered processors:
    dedup_tool_results          — collapses identical-content tool returns to back-references
    evict_old_tool_results      — content-clears compactable tool results by recency
    spill_largest_tool_results  — force-spills largest unspilled tool returns when full
                                  request exceeds spill_threshold_tokens

Recovery helpers (not registered as processors):
    strip_all_tool_returns      — collapses every tool return to a semantic marker;
                                  used by overflow recovery to cut tokens before retry.
                                  Differs from evict_old_tool_results: no recency cap,
                                  no boundary protection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.context._compaction_boundaries import (
    _find_last_turn_start,
    plan_compaction_boundaries,
)
from co_cli.context._dedup_tool_results import (
    build_dedup_part,
    dedup_key,
    is_dedup_candidate,
)
from co_cli.context._tool_result_markers import is_cleared_marker, semantic_marker
from co_cli.context.summarization import (
    estimate_message_tokens,
    resolve_compaction_budget,
)
from co_cli.context.tokens import CHARS_PER_TOKEN
from co_cli.deps import CoDeps
from co_cli.observability.tracing import current_span
from co_cli.tools.tool_io import (
    PERSISTED_OUTPUT_TAG,
    TOOL_RESULT_PREVIEW_CHARS,
    spill_if_oversized,
)

COMPACTABLE_KEEP_RECENT = 5
"""Keep the N most-recent tool returns per tool name; clear older.

Borrowed from ``fork-claude-code/services/compact/timeBasedMCConfig.ts:33``
(``keepRecent: 5``). Not convergent across peers — codex, hermes, and
opencode do not have per-tool recency retention. Not tuned specifically for
co-cli's tool surface; revisit via ``evals/eval_compaction_quality.py`` if a
retention/fidelity tradeoff becomes measurable.
"""

_CLEARED_PLACEHOLDER = "[tool result cleared]"
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
    # Tail-protected: tool returns at or after boundary always survive.
    for msg in messages[boundary:]:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                durable.add(part.tool_call_id)
    # Pre-tail: mirror truncate's keep-recent logic using tool_call_id strings.
    seen_counts: dict[str, int] = {}
    for part in _iter_tool_returns_reversed(messages[:boundary]):
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

    For each tool return whose content (≥ 200 chars, string) duplicates a
    more recent return of the same tool, replace with a 1-line back-reference
    marker naming the latest ``call_id``. Only the latest occurrence of each
    ``(tool, hash)`` retains full content.

    Protects the last turn via the same ``_find_last_turn_start`` boundary
    M2a uses. Non-string content and content below the dedup floor pass
    through unchanged (same safety envelope as ``evict_old_tool_results``).

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
    readable content string for char/line/outcome heuristics. Already-marked
    string content is returned unchanged so re-running over a previously
    stripped/evicted history does not degrade the size signal (e.g. EVICT
    fires earlier in the same turn, then recovery strip would otherwise
    re-mark ``[file_read] /path (full, 8,432 chars)`` based on the marker
    string itself, losing the original char count).
    """
    content = part.content
    if isinstance(content, str) and is_cleared_marker(content):
        return part
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
    """Content-clear tool results older than the 5 most recent per tool name.

    Replacement content is a per-tool semantic marker (see
    ``semantic_marker``) carrying tool name, key args, and a size/outcome
    signal. Falls back to the static ``_CLEARED_PLACEHOLDER`` when the
    ToolReturnPart carries non-string content.

    Protects the last turn (everything from the last UserPromptPart onward).
    Every tool name competes for its own keep-recent slot — eligibility is
    content-shape only, not tool selectivity.

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
    call_id_to_args = _build_call_id_to_args(messages[:boundary])

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        if id(part) in keep_ids:
            return None
        return _build_cleared_part(part, call_id_to_args)

    return _rewrite_tool_returns(messages, boundary, replacement_for=replacement_for)


def _collect_tool_return_candidates(
    messages: list[ModelMessage],
) -> list[tuple[int, ToolReturnPart]]:
    """Collect every string-content ``ToolReturnPart`` with its message index.

    The message index lets ``spill_largest_tool_results`` exclude returns inside
    the protected tail (index ``>= tail_start``) from the spillable set.
    """
    candidates: list[tuple[int, ToolReturnPart]] = []
    for index, msg in enumerate(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, str):
                candidates.append((index, part))
    return candidates


def _spill_largest_first(
    spillable: list[ToolReturnPart],
    *,
    starting_tokens: int,
    threshold: int,
    tool_results_dir: Path,
) -> tuple[dict[int, ToolReturnPart], int, int, int]:
    """Force-spill candidates largest-first until aggregate fits or candidates exhaust.

    Returns ``(spilled_by_id, aggregate, spilled_count, spill_errors)`` keyed by
    ``id(part)`` so the caller can look up replacements via
    ``_rewrite_tool_returns``.
    """
    spilled_by_id: dict[int, ToolReturnPart] = {}
    chars_freed = 0
    aggregate = starting_tokens
    spilled_count = 0
    spill_errors = 0
    for part in sorted(spillable, key=lambda p: len(p.content), reverse=True):
        aggregate = starting_tokens - chars_freed // CHARS_PER_TOKEN
        if aggregate <= threshold:
            break
        old_content = part.content
        new_content = spill_if_oversized(
            old_content,
            tool_results_dir,
            part.tool_name,
            force=True,
        )
        if new_content == old_content:
            spill_errors += 1
            continue
        spilled_by_id[id(part)] = ToolReturnPart(
            tool_name=part.tool_name,
            content=new_content,
            tool_call_id=part.tool_call_id,
        )
        chars_freed += len(old_content) - len(new_content)
        spilled_count += 1
    aggregate = starting_tokens - chars_freed // CHARS_PER_TOKEN
    return spilled_by_id, aggregate, spilled_count, spill_errors


def spill_largest_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Force-spill the largest unspilled ``ToolReturnPart``s until the request fits.

    Runs after ``dedup_tool_results`` / ``evict_old_tool_results`` (so cheap
    reductions happen first) and before ``proactive_window_processor`` (which
    fast-paths when this processor brought total tokens under
    ``compaction_ratio x budget``). Operates on the full message list -- not
    a single batch -- so it sees whatever pressure the upcoming request will
    actually carry.

    Algorithm:
      1. ``total = static_floor_tokens + estimate_message_tokens`` — the floor-inclusive
         realtime-local count (no provider-reported floor; peer-aligned with hermes/openclaw).
      2. If ``total <= deps.spill_threshold_tokens``, fast-path.
      3. Collect ``ToolReturnPart`` candidates with string content; filter
         spillable (index ``< tail_start`` and content does not start with
         ``PERSISTED_OUTPUT_TAG``). ``tail_start`` comes from the SAME
         ``plan_compaction_boundaries(messages, resolve_compaction_budget(deps),
         cfg.tail_fraction)`` boundary L3 and overflow-recovery use, so the
         freshest tool return — the content the model is about to read — is
         preserved for one round before becoming spill-eligible. When the
         planner returns ``None`` (too few turns to form a tail), every
         candidate is spillable, matching pre-tail-protection behavior.
      4. Sort spillable largest-first by ``len(content)``.
      5. Force-spill via ``spill_if_oversized(..., force=True)`` until aggregate
         falls to or below the threshold or candidates exhaust.

    Skip reasons surfaced via the ``tool_budget.spill_largest_tool_results`` event:
      - ``below_threshold``      -- total under threshold; nothing to do.
      - ``no_candidates``        -- no string ``ToolReturnPart``s present.
      - ``all_spilled``          -- every candidate is already on disk.
      - ``fallback_to_summarize``-- spill exhausted but total still over; let
                                    ``proactive_window_processor`` handle it.
      - empty string             -- spill fired and brought total under threshold.
    """
    deps = ctx.deps
    threshold = deps.spill_threshold_tokens

    local_total = estimate_message_tokens(messages)
    trigger = deps.static_floor_tokens + local_total

    budget = resolve_compaction_budget(deps)
    cfg = deps.config.compaction
    boundary = plan_compaction_boundaries(
        messages,
        budget,
        cfg.tail_fraction,
        static_floor_tokens=deps.static_floor_tokens,
        compaction_ratio=cfg.compaction_ratio,
    )
    tail_start = boundary[1] if boundary else len(messages)

    candidates = _collect_tool_return_candidates(messages)
    spillable = [
        part
        for index, part in candidates
        if index < tail_start
        and not part.content.startswith(PERSISTED_OUTPUT_TAG)
        and len(part.content) > TOOL_RESULT_PREVIEW_CHARS
    ]
    tail_protected_count = sum(1 for index, _ in candidates if index >= tail_start)

    event_attrs: dict[str, Any] = {
        "budget.context_window_tokens": deps.model_max_context_tokens,
        "request.threshold_tokens": threshold,
        "request.tokens_before": trigger,
        "request.local_tokens": local_total,
        "request.static_floor_tokens": deps.static_floor_tokens,
        "request.tail_start": tail_start,
        "request.tail_protected_count": tail_protected_count,
        "request.candidates_count": len(candidates),
        "request.spillable_count": len(spillable),
    }

    def _emit_terminal(
        skip_reason: str,
        *,
        tokens_after: int,
        spilled: int = 0,
        errors: int = 0,
    ) -> None:
        event_attrs.update(
            {
                "request.tokens_after": tokens_after,
                "request.spilled_count": spilled,
                "request.spill_errors": errors,
                "request.spill_fired": spilled > 0,
                "request.skip_reason": skip_reason,
            }
        )
        deps.runtime.current_request_tokens_estimate = tokens_after
        current_span().add_event("tool_budget.spill_largest_tool_results", event_attrs)

    if trigger <= threshold:
        _emit_terminal("below_threshold", tokens_after=trigger)
        return messages
    if not candidates:
        _emit_terminal("no_candidates", tokens_after=trigger)
        return messages
    if not spillable:
        _emit_terminal("all_spilled", tokens_after=trigger)
        return messages

    spilled_by_id, local_after, spilled_count, spill_errors = _spill_largest_first(
        spillable,
        starting_tokens=local_total,
        threshold=threshold,
        tool_results_dir=deps.tool_results_dir,
    )

    effective_after = deps.static_floor_tokens + local_after

    if not spilled_by_id:
        _emit_terminal(
            "fallback_to_summarize" if effective_after > threshold else "all_spilled",
            tokens_after=effective_after,
            errors=spill_errors,
        )
        return messages

    result = _rewrite_tool_returns(
        messages,
        len(messages),
        replacement_for=lambda p: spilled_by_id.get(id(p)),
    )
    _emit_terminal(
        "" if effective_after <= threshold else "fallback_to_summarize",
        tokens_after=effective_after,
        spilled=spilled_count,
        errors=spill_errors,
    )
    return result


def strip_all_tool_returns(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Replace every ToolReturnPart.content with a semantic marker.

    Differs from ``evict_old_tool_results`` in two ways: no recency cap, no
    boundary protection. Every tool return — including writes, approvals,
    and memory ops — is reduced to a one-line marker. Tool-call pairing is
    preserved by construction (only ``.content`` is rewritten, ``tool_name``
    and ``tool_call_id`` survive).

    Used exclusively by overflow recovery, where freeing the entire pre-tail
    region matters more than preserving the protected window. Cheap: pure,
    no LLM, no I/O.
    """
    call_id_to_args = _build_call_id_to_args(messages)

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        return _build_cleared_part(part, call_id_to_args)

    return _rewrite_tool_returns(messages, len(messages), replacement_for=replacement_for)


_ELIDED_IMAGE_PLACEHOLDER = "[image elided]"
"""Replacement for multimodal pixels in non-tail UserPromptParts on replay.

image_view's native path attaches pixels via ToolReturn.content, which pydantic-ai
materializes as a UserPromptPart whose content is a Sequence[UserContent] carrying
BinaryContent. Older turns' pixels are replaced by this text placeholder so base64
does not accumulate across turns.
"""


def _elide_multimodal_content(content: list[Any]) -> tuple[list[Any], bool]:
    """Replace BinaryContent items in a UserPromptPart content sequence with a placeholder.

    Returns ``(new_content, changed)``. Non-pixel items (text, URL references) pass
    through unchanged; only inline pixel payloads (BinaryContent) are elided — those are
    the base64 bloat image_view's native path injects.
    """
    new_content: list[Any] = []
    changed = False
    for item in content:
        if isinstance(item, BinaryContent):
            new_content.append(_ELIDED_IMAGE_PLACEHOLDER)
            changed = True
        else:
            new_content.append(item)
    return new_content, changed


def elide_old_multimodal_prompts(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Strip inline pixels from non-tail UserPromptParts so base64 does not accumulate.

    image_view's native path feeds real pixels to the model via ToolReturn.content,
    which pydantic-ai materializes as a separate UserPromptPart (not a ToolReturnPart —
    the existing processors only cover those). This processor preserves the most recent
    turn's pixels (so the model can still answer about the freshest image) and replaces
    BinaryContent in older UserPromptParts with a text placeholder.

    Protects the last turn via the same ``_find_last_turn_start`` boundary the tool-return
    processors use. Non-multimodal UserPromptParts (string content) and the protected tail
    pass through unchanged. Pure: rebuilds only the messages where a part changed.
    """
    boundary = _find_last_turn_start(messages)
    if not boundary:
        return messages

    result: list[ModelMessage] = []
    for idx, msg in enumerate(messages):
        if idx >= boundary or not isinstance(msg, ModelRequest):
            result.append(msg)
            continue
        new_parts: list = []
        modified = False
        for part in msg.parts:
            if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                new_content, changed = _elide_multimodal_content(list(part.content))
                if changed:
                    new_parts.append(replace(part, content=new_content))
                    modified = True
                    continue
            new_parts.append(part)
        result.append(replace(msg, parts=new_parts) if modified else msg)
    return result
