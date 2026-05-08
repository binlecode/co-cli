"""L2 request-budget hook helper called from ``CoToolLifecycle.after_node_run``.

Operates on a single ``ModelRequest``'s parts: when the aggregate of
non-persisted ``ToolReturnPart``s exceeds
``deps.request_aggregate_threshold_tokens``, force-spill the largest ones until
the request fits. No message-list scan, no boundary search — the hook receives
one upcoming request and rewrites its ``parts``.

Design borrowed from ``hermes-agent/tools/tool_result_storage.py:enforce_turn_budget``
(hermes uses "turn" to mean what co-cli calls a request).
"""

from __future__ import annotations

from opentelemetry import trace as otel_trace
from pydantic_ai.messages import ModelRequestPart, ToolReturnPart

from co_cli.context.tokens import CHARS_PER_TOKEN
from co_cli.deps import CoDeps
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG, spill_if_oversized


def _enforce_request_budget(
    parts: list[ModelRequestPart],
    deps: CoDeps,
    tracer: otel_trace.Tracer,
) -> list[ModelRequestPart] | None:
    """Force-spill ``ToolReturnPart``s in the request until aggregate fits.

    Returns a new parts list (or ``None`` when no rewrite was needed). Writes
    ``deps.runtime.current_request_aggregate_tokens_after_spill`` for OTEL.
    Always emits a ``tool_budget.enforce_request_aggregate`` span.
    """
    threshold = deps.request_aggregate_threshold_tokens

    with tracer.start_as_current_span("tool_budget.enforce_request_aggregate") as span:
        span.set_attribute("budget.context_window_tokens", deps.model_max_ctx)
        span.set_attribute("request_aggregate.threshold_tokens", threshold)

        candidates: list[tuple[int, ToolReturnPart]] = [
            (i, p)
            for i, p in enumerate(parts)
            if isinstance(p, ToolReturnPart) and isinstance(p.content, str)
        ]
        tokens_before = sum(len(p.content) // CHARS_PER_TOKEN for _, p in candidates)
        span.set_attribute("request_aggregate.tokens_before", tokens_before)
        span.set_attribute("request_aggregate.candidates_count", len(candidates))

        if tokens_before <= threshold:
            span.set_attribute("request_aggregate.tokens_after", tokens_before)
            span.set_attribute("request_aggregate.spilled_count", 0)
            span.set_attribute("request_aggregate.spill_fired", False)
            span.set_attribute("request_aggregate.skip_reason", "below_threshold")
            return None

        spillable = [
            (i, p) for i, p in candidates if not p.content.startswith(PERSISTED_OUTPUT_TAG)
        ]
        if not spillable:
            span.set_attribute("request_aggregate.tokens_after", tokens_before)
            span.set_attribute("request_aggregate.spilled_count", 0)
            span.set_attribute("request_aggregate.spill_fired", False)
            span.set_attribute("request_aggregate.skip_reason", "no_candidates_all_spilled")
            return None

        spillable.sort(key=lambda t: len(t[1].content), reverse=True)

        new_parts = list(parts)
        aggregate_tokens = tokens_before
        spilled_count = 0
        for idx, part in spillable:
            if aggregate_tokens <= threshold:
                break
            old_content = part.content
            new_content = spill_if_oversized(
                old_content,
                deps.tool_results_dir,
                part.tool_name,
                force=True,
            )
            if new_content == old_content:
                continue
            new_parts[idx] = ToolReturnPart(
                tool_name=part.tool_name,
                content=new_content,
                tool_call_id=part.tool_call_id,
            )
            aggregate_tokens -= (len(old_content) - len(new_content)) // CHARS_PER_TOKEN
            spilled_count += 1

        deps.runtime.current_request_aggregate_tokens_after_spill = aggregate_tokens
        span.set_attribute("request_aggregate.tokens_after", aggregate_tokens)
        span.set_attribute("request_aggregate.spilled_count", spilled_count)
        span.set_attribute("request_aggregate.spill_fired", True)
        span.set_attribute("request_aggregate.skip_reason", "")
        return new_parts
