"""Pure history transformers registered via ``Agent(history_processors=[...])``.

Each processor runs before every model request and returns a transformed
message list. None of them mutate ``CoDeps`` or the original ``ModelMessage``
objects — ``_rewrite_tool_returns`` rebuilds only the messages where at least
one part changed.

Registered processors:
    dedup_tool_results          — collapses identical-content tool returns to back-references
    evict_old_tool_results      — content-clears compactable tool results by recency
    enforce_request_size        — force-spills largest unspilled tool returns when full
                                  request exceeds spill_threshold_tokens
    sanitize_surrogate_codepoints — replaces lone Unicode surrogates with U+FFFD

Recovery helpers (not registered as processors):
    strip_all_tool_returns      — collapses every tool return to a semantic marker;
                                  used by overflow recovery to cut tokens before retry.
                                  Differs from evict_old_tool_results: no COMPACTABLE_TOOLS
                                  filter, no recency cap, no boundary protection.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.context._compaction_boundaries import _find_last_turn_start
from co_cli.context._dedup_tool_results import (
    build_dedup_part,
    dedup_key,
    is_dedup_candidate,
)
from co_cli.context._tool_result_markers import is_cleared_marker, semantic_marker
from co_cli.context.summarization import estimate_message_tokens, latest_response_input_tokens
from co_cli.context.tokens import CHARS_PER_TOKEN
from co_cli.deps import CoDeps
from co_cli.observability.tracing import current_span
from co_cli.tools.categories import COMPACTABLE_TOOLS
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG, spill_if_oversized

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
    call_id_to_args = _build_call_id_to_args(messages[:boundary])

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        if part.tool_name not in COMPACTABLE_TOOLS or id(part) in keep_ids:
            return None
        return _build_cleared_part(part, call_id_to_args)

    return _rewrite_tool_returns(messages, boundary, replacement_for=replacement_for)


def _collect_tool_return_candidates(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    """Collect every string-content ``ToolReturnPart`` across the message list."""
    candidates: list[ToolReturnPart] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, str):
                candidates.append(part)
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
    aggregate = starting_tokens
    spilled_count = 0
    spill_errors = 0
    for part in sorted(spillable, key=lambda p: len(p.content), reverse=True):
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
        aggregate -= (len(old_content) - len(new_content)) // CHARS_PER_TOKEN
        spilled_count += 1
    return spilled_by_id, aggregate, spilled_count, spill_errors


def enforce_request_size(
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
      1. ``total = max(estimate_message_tokens, latest_response_input_tokens)``.
      2. If ``total <= deps.spill_threshold_tokens``, fast-path.
      3. Collect ``ToolReturnPart`` candidates with string content; filter
         spillable (content does not start with ``PERSISTED_OUTPUT_TAG``).
      4. Sort spillable largest-first by ``len(content)``.
      5. Force-spill via ``spill_if_oversized(..., force=True)`` until aggregate
         falls to or below the threshold or candidates exhaust.

    Skip reasons surfaced via the ``tool_budget.enforce_request_size`` event:
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
    reported_total = latest_response_input_tokens(messages)
    trigger = max(local_total, reported_total)
    candidates = _collect_tool_return_candidates(messages)
    spillable = [p for p in candidates if not p.content.startswith(PERSISTED_OUTPUT_TAG)]

    event_attrs: dict[str, Any] = {
        "budget.context_window_tokens": deps.model_max_ctx,
        "request.threshold_tokens": threshold,
        "request.tokens_before": trigger,
        "request.local_tokens": local_total,
        "request.reported_tokens": reported_total,
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
        current_span().add_event("tool_budget.enforce_request_size", event_attrs)

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

    effective_after = max(local_after, reported_total)

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

    Differs from ``evict_old_tool_results`` in three ways: no
    ``COMPACTABLE_TOOLS`` filter, no recency cap, no boundary protection.
    Every tool return — compactable and non-compactable, including writes,
    approvals, and memory ops — is reduced to a one-line marker. Tool-call
    pairing is preserved by construction (only ``.content`` is rewritten,
    ``tool_name`` and ``tool_call_id`` survive).

    Used exclusively by overflow recovery, where preserving signal in
    non-compactable returns is less valuable than recovering the turn.
    Cheap: pure, no LLM, no I/O.
    """
    call_id_to_args = _build_call_id_to_args(messages)

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        return _build_cleared_part(part, call_id_to_args)

    return _rewrite_tool_returns(messages, len(messages), replacement_for=replacement_for)


# ---------------------------------------------------------------------------
# Surrogate sanitizer — gap 1.3 from RESEARCH-hermes-ollama-stability-gaps.md
# ---------------------------------------------------------------------------

_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _replace_surrogates(text: str) -> str:
    if not _LONE_SURROGATE_RE.search(text):
        return text
    return _LONE_SURROGATE_RE.sub("�", text)


def _sanitize_structure(payload: Any) -> tuple[Any, bool]:
    """Recursively sanitize string leaves in dict/list payloads.

    Returns ``(new_payload, modified)``. Rebuilds dict/list branches only
    where a string actually changed; leaves untouched branches identity-equal
    to the input so downstream change detection stays cheap.
    """
    if isinstance(payload, dict):
        modified = False
        new_dict: dict[Any, Any] = {}
        for key, value in payload.items():
            new_value, changed = _sanitize_structure(value)
            if changed:
                modified = True
            new_dict[key] = new_value
        return (new_dict, True) if modified else (payload, False)
    if isinstance(payload, list):
        modified = False
        new_list: list[Any] = []
        for value in payload:
            new_value, changed = _sanitize_structure(value)
            if changed:
                modified = True
            new_list.append(new_value)
        return (new_list, True) if modified else (payload, False)
    if isinstance(payload, str):
        sanitized = _replace_surrogates(payload)
        return (sanitized, True) if sanitized is not payload else (payload, False)
    return payload, False


def _sanitize_request_parts(msg: ModelRequest) -> ModelRequest:
    new_parts: list = []
    modified = False
    for part in msg.parts:
        if isinstance(
            part, (UserPromptPart, SystemPromptPart, RetryPromptPart, ToolReturnPart)
        ) and isinstance(part.content, str):
            sanitized = _replace_surrogates(part.content)
            if sanitized is not part.content:
                part = replace(part, content=sanitized)
                modified = True
        new_parts.append(part)
    return replace(msg, parts=new_parts) if modified else msg


def _sanitize_response_parts(msg: ModelResponse) -> ModelResponse:
    new_parts: list = []
    modified = False
    for part in msg.parts:
        if isinstance(part, (TextPart, ThinkingPart)):
            if isinstance(part.content, str):
                sanitized = _replace_surrogates(part.content)
                if sanitized is not part.content:
                    part = replace(part, content=sanitized)
                    modified = True
        elif isinstance(part, ToolCallPart):
            if isinstance(part.args, str):
                sanitized = _replace_surrogates(part.args)
                if sanitized is not part.args:
                    part = replace(part, args=sanitized)
                    modified = True
            elif isinstance(part.args, dict):
                new_args, changed = _sanitize_structure(part.args)
                if changed:
                    part = replace(part, args=new_args)
                    modified = True
        new_parts.append(part)
    return replace(msg, parts=new_parts) if modified else msg


def sanitize_surrogate_codepoints_messages(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Replace lone Unicode surrogate code points (U+D800-U+DFFF) with U+FFFD.

    Pure function — shared by the history-processor (proactive walk) and
    ``SurrogateRecoveryModel`` (reactive backstop on ``UnicodeEncodeError``).

    Byte-token reasoning models (Qwen3 quantizations, GLM-5, Kimi K2.5)
    occasionally emit lone surrogates that crash json.dumps() with
    UnicodeEncodeError inside the OpenAI SDK.
    """
    result: list[ModelMessage] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            result.append(_sanitize_request_parts(msg))
        elif isinstance(msg, ModelResponse):
            result.append(_sanitize_response_parts(msg))
        else:
            result.append(msg)
    return result


def sanitize_surrogate_codepoints(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """History-processor wrapper around :func:`sanitize_surrogate_codepoints_messages`.

    Registered last in the history processor chain so it runs on the final
    budget-trimmed message list.
    """
    return sanitize_surrogate_codepoints_messages(messages)
