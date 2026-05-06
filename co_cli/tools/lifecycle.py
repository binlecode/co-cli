"""Cross-cutting tool lifecycle capability: path normalization, telemetry, audit."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai import CallToolsNode, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    NodeResult,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from co_cli.agent._tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN, make_exceeded_payload
from co_cli.deps import CoDeps
from co_cli.tools.categories import PATH_NORMALIZATION_TOOLS

logger = logging.getLogger(__name__)

_TRACER = otel_trace.get_tracer("co-cli.tool_budget")


@dataclass
class CoToolLifecycle(AbstractCapability[CoDeps]):
    """SDK capability for cross-cutting tool concerns.

    Hooks:
    - wrap_tool_execute: per-call cap brake (N times per model turn)
    - after_node_run: per-turn aggregate span after all tool calls complete
    - before_tool_execute: path normalization for file tools
    - after_tool_execute: span enrichment + audit logging
    """

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

        with _TRACER.start_as_current_span("tool_budget.enforce_tool_call_limit") as span:
            span.set_attribute("budget.context_window_tokens", ctx.deps.model_max_ctx)
            span.set_attribute("tool_calls.limit", MAX_TOOL_CALLS_PER_MODEL_TURN)
            span.set_attribute("tool_calls.issued", issued)
            span.set_attribute("tool_calls.allowed", allowed)
            span.set_attribute("tool_calls.rejected", rejected)
            span.set_attribute("tool_calls.limit_exceeded", rejected > 0)

        return result

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
        if call.tool_name in PATH_NORMALIZATION_TOOLS and "path" in args:
            workspace_root = ctx.deps.workspace_root
            args["path"] = str((workspace_root / args["path"]).resolve())
        return args
