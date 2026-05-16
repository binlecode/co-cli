"""Cross-cutting tool lifecycle capability: path normalization, telemetry, audit."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai import CallToolsNode, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    NodeResult,
    RawToolArgs,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ModelResponsePart, ToolCallPart
from pydantic_ai.tools import ToolDefinition

from co_cli.deps import CoDeps, ToolSourceEnum
from co_cli.tools.categories import PATH_NORMALIZATION_TOOLS
from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN, make_exceeded_payload
from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS, spill_with_span

logger = logging.getLogger(__name__)

_CLOSE_FOR: dict[str, str] = {"{": "}", "[": "]"}
_OPEN_FOR: dict[str, str] = {v: k for k, v in _CLOSE_FOR.items()}
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _try_parse(s: str) -> str | None:
    try:
        return json.dumps(json.loads(s, strict=False))
    except json.JSONDecodeError:
        return None


def _balance_brackets(s: str) -> str:
    """Append missing closing brackets by tracking the open-bracket stack."""
    stack: list[str] = []
    in_str = False
    escape_next = False
    for ch in s:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_str:
            escape_next = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in _CLOSE_FOR:
            stack.append(ch)
        elif ch in _OPEN_FOR and stack and stack[-1] == _OPEN_FOR[ch]:
            stack.pop()
    return s + "".join(_CLOSE_FOR[o] for o in reversed(stack))


def _repair_json_args(raw: str) -> str:
    """Apply syntactic repair passes to a malformed tool-call arguments string.

    Purely syntactic — no value inference or schema awareness. Returns a valid
    JSON string on success, or '{}' if all passes fail (so pydantic validation
    can raise ModelRetry rather than crashing the session).
    """
    if not raw or not raw.strip():
        return "{}"
    s = raw.strip()

    if s == "None":
        return "{}"

    # Pass 3: control-char escape — strict=False accepts literal tabs/newlines;
    # re-serialise to produce a spec-compliant string.
    result = _try_parse(s)
    if result is not None:
        return result

    # Pass 4: trailing-comma strip (common in quantized-model output)
    s = _TRAILING_COMMA.sub(r"\1", s)
    result = _try_parse(s)
    if result is not None:
        return result

    # Pass 5: balance unclosed brackets, then re-strip trailing commas that now
    # precede the appended closer.
    s = _TRAILING_COMMA.sub(r"\1", _balance_brackets(s))
    result = _try_parse(s)
    if result is not None:
        return result

    # Pass 6: trim excess trailing closing delimiters (bounded to 50 steps).
    for _ in range(50):
        s = s.rstrip()
        if not s or s[-1] not in ("}", "]"):
            break
        s = s[:-1]
        result = _try_parse(s)
        if result is not None:
            return result

    return "{}"


def _args_dedup_key(args: str | dict[str, Any] | None) -> str:
    """Stable key for ``ToolCallPart.args``; raw strings and parsed dicts both supported."""
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, dict):
        return json.dumps(args, sort_keys=True)
    return ""


def _dedup_tool_call_parts(
    parts: list[ModelResponsePart],
) -> list[ModelResponsePart] | None:
    """Drop later ``ToolCallPart``s with the same ``(tool_name, args)`` as an earlier one; ``None`` if no duplicates."""
    seen: set[tuple[str, str]] = set()
    new_parts: list[ModelResponsePart] = []
    modified = False
    for part in parts:
        if isinstance(part, ToolCallPart):
            key = (part.tool_name, _args_dedup_key(part.args))
            if key in seen:
                modified = True
                continue
            seen.add(key)
        new_parts.append(part)
    return new_parts if modified else None


@dataclass
class CoToolLifecycle(AbstractCapability[CoDeps]):
    """SDK capability for cross-cutting tool concerns.

    Hooks:
    - before_node_run: dedup duplicate tool calls within one ModelResponse
    - wrap_tool_execute: per-call cap brake (N times per model turn)
    - after_node_run: tool-call-limit span (L0). Per-request size enforcement
      lives in ``enforce_request_size`` (history processor at MRN entry), not here.
    - before_tool_validate: syntactic JSON repair for malformed model output
    - before_tool_execute: path normalization for file tools
    - after_tool_execute: span enrichment + audit logging
    """

    _tracer: otel_trace.Tracer = field(
        default_factory=lambda: otel_trace.get_tracer("co-cli.tool_budget")
    )

    async def before_node_run(
        self,
        ctx: RunContext[CoDeps],
        *,
        node: AgentNode[CoDeps],
    ) -> AgentNode[CoDeps]:
        if not isinstance(node, CallToolsNode):
            return node
        deduped = _dedup_tool_call_parts(node.model_response.parts)
        if deduped is None:
            return node
        before = len(node.model_response.parts)
        after = len(deduped)
        dropped = before - after
        with self._tracer.start_as_current_span("tool_budget.dedup_tool_calls") as span:
            span.set_attribute("dedup.parts_before", before)
            span.set_attribute("dedup.parts_after", after)
            span.set_attribute("dedup.dropped", dropped)
        node.model_response = replace(node.model_response, parts=deduped)
        logger.debug("dedup_tool_calls dropped=%d", dropped)
        return node

    async def wrap_tool_execute(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        runtime = ctx.deps.runtime
        # Per-model-turn reset: ctx.run_step increments once per LLM call;
        # all tools from one assistant message share the same run_step.
        if ctx.run_step != runtime.tool_call_limit_run_step:
            runtime.tool_call_limit_run_step = ctx.run_step
            runtime.tool_calls_in_model_turn = 0
        runtime.tool_calls_in_model_turn += 1

        if runtime.tool_calls_in_model_turn > MAX_TOOL_CALLS_PER_MODEL_TURN:
            return json.dumps(make_exceeded_payload(runtime.tool_calls_in_model_turn))
        return await handler(args)

    async def after_node_run(
        self,
        ctx: RunContext[CoDeps],
        *,
        node: AgentNode[CoDeps],
        result: NodeResult[CoDeps],
    ) -> NodeResult[CoDeps]:
        if not isinstance(node, CallToolsNode):
            return result

        issued = ctx.deps.runtime.tool_calls_in_model_turn
        allowed = min(issued, MAX_TOOL_CALLS_PER_MODEL_TURN)
        rejected = max(0, issued - MAX_TOOL_CALLS_PER_MODEL_TURN)

        with self._tracer.start_as_current_span("tool_budget.enforce_tool_call_limit") as span:
            span.set_attribute("budget.context_window_tokens", ctx.deps.model_max_ctx)
            span.set_attribute("tool_calls.limit", MAX_TOOL_CALLS_PER_MODEL_TURN)
            span.set_attribute("tool_calls.issued", issued)
            span.set_attribute("tool_calls.allowed", allowed)
            span.set_attribute("tool_calls.rejected", rejected)
            span.set_attribute("tool_calls.limit_exceeded", rejected > 0)

        return result

    async def before_tool_validate(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
    ) -> RawToolArgs:
        if isinstance(args, str):
            return _repair_json_args(args)
        return args

    async def after_tool_execute(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        span = otel_trace.get_current_span()
        info = ctx.deps.tool_index.get(call.tool_name)

        # MCP results are plain strings that bypass tool_output() — enforce spill gate here.
        if isinstance(result, str) and info and info.source == ToolSourceEnum.MCP:
            threshold = (
                info.spill_threshold_chars
                if info.spill_threshold_chars is not None
                else SPILL_THRESHOLD_CHARS
            )
            result = spill_with_span(
                result,
                tool_name=call.tool_name,
                tool_results_dir=ctx.deps.tool_results_dir,
                threshold_chars=threshold,
            )

        if span.is_recording():
            span.set_attribute("co.tool.result_size", len(str(result)))
            if info:
                span.set_attribute("co.tool.source", info.source.value)
                span.set_attribute("co.tool.requires_approval", info.approval)
        logger.debug("tool_executed tool_name=%s", call.tool_name)
        return result

    async def before_tool_execute(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        span = otel_trace.get_current_span()
        if span.is_recording():
            try:
                args_chars = len(json.dumps(args, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                args_chars = 0
            span.set_attribute("co.tool.args_chars", args_chars)
        if call.tool_name in PATH_NORMALIZATION_TOOLS and "path" in args:
            workspace_dir = ctx.deps.workspace_dir
            args["path"] = str((workspace_dir / args["path"]).resolve())
        return args
