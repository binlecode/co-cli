"""ObservabilityCapability — pydantic-ai capability that emits structured spans.

Replaces ``Agent.instrument_all(InstrumentationSettings(tracer_provider=...))``.
Hooks into agent/model/tool lifecycle and emits one record per close to
``tracing.setup_log``'s spans logger.

**Capability hook ordering invariant** (verified against
``pydantic_ai.capabilities.combined`` — ``before_*`` runs in declaration order,
``after_*`` and ``on_*_error`` run in **reverse** declaration order / LIFO):

    capabilities=[ObservabilityCapability(), CoToolLifecycle()]

This places Observability OUTERMOST:
- ``before_tool_execute``: Observability pushes span first → CoToolLifecycle
  runs inside it.
- ``after_tool_execute``: CoToolLifecycle runs FIRST (so it can attach
  ``co.tool.source`` / ``co.tool.requires_approval`` / ``co.tool.result_size``
  via ``current_span().set_attribute(...)`` while the tool span is still
  active), THEN Observability closes the span.

If this order were reversed, ``CoToolLifecycle.after_tool_execute`` would land
attribute writes on a no-op proxy. Do not reorder without revisiting both
modules.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs
from pydantic_ai.messages import (
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.tools import ToolDefinition

from co_cli.deps import CoDeps
from co_cli.observability.tracing import pop_span, push_span
from co_cli.session.usage import record_usage

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelMessage, ToolCallPart
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.run import AgentRunResult

logger = logging.getLogger(__name__)

_TOOL_RESULT_MAX_CHARS = 16_000


def _agent_name(ctx: RunContext[CoDeps]) -> str:
    if ctx.agent is None:
        return "<unknown>"
    return ctx.agent.name or "<unknown>"


def serialize_messages(messages: list[ModelMessage]) -> str:
    """Serialize message history to a compact JSON string preserving roles + part types.

    Public (importable) surface: the direct-call span in ``co_cli.llm.call`` reuses
    this to populate ``co.model.input`` at parity with the agent-path ``chat`` span.
    Distinct from ``co_cli.context.summarization.serialize_messages`` (which renders
    human-readable redacted text for summarizer prompts, not compact span JSON).
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        kind = getattr(msg, "kind", "request")
        if kind == "request":
            parts_data: list[dict[str, Any]] = []
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content if isinstance(part.content, str) else str(part.content)
                    parts_data.append({"type": "user", "content": content})
                else:
                    parts_data.append(
                        {"type": getattr(part, "part_kind", part.__class__.__name__)}
                    )
            out.append({"role": "request", "parts": parts_data})
        else:
            out.append({"role": "response", "parts": _serialize_response_parts(msg.parts)})
    return json.dumps(out, default=str)


def _serialize_response_parts(parts: list[ModelResponsePart]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            serialized.append({"type": "text", "content": part.content})
        elif isinstance(part, ThinkingPart):
            serialized.append({"type": "thinking", "content": part.content})
        elif isinstance(part, ToolCallPart):
            serialized.append(
                {
                    "type": "tool_call",
                    "tool_name": part.tool_name,
                    "tool_call_id": part.tool_call_id,
                    "args": part.args,
                }
            )
        else:
            serialized.append({"type": getattr(part, "part_kind", part.__class__.__name__)})
    return serialized


def serialize_response(response: ModelResponse) -> str:
    """Serialize a ModelResponse's parts to compact JSON for span ``co.model.output``.

    Public (importable) surface — reused by the direct-call span in ``co_cli.llm.call``.
    """
    return json.dumps(_serialize_response_parts(list(response.parts)), default=str)


def _serialize_tool_args(args: Any) -> str:
    try:
        return json.dumps(args, default=str)
    except (TypeError, ValueError):
        return str(args)


def _truncate(value: Any) -> str:
    text = str(value) if not isinstance(value, str) else value
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        return text[:_TOOL_RESULT_MAX_CHARS] + f"\n... [truncated, total {len(text)} chars]"
    return text


class ObservabilityCapability(AbstractCapability[CoDeps]):
    """Emit structured span records on agent/model/tool lifecycle."""

    async def before_run(self, ctx: RunContext[CoDeps]) -> None:
        meta = ctx.metadata or {}
        agent_name = _agent_name(ctx)
        push_span(
            f"invoke_agent {agent_name}",
            kind="agent",
            attributes={
                "co.agent.role": meta.get("role", agent_name),
                "co.agent.model": getattr(ctx.model, "model_name", str(ctx.model)),
                "co.agent.request_limit": meta.get("request_limit"),
            },
        )

    async def after_run(
        self,
        ctx: RunContext[CoDeps],
        *,
        result: AgentRunResult[Any],
    ) -> AgentRunResult[Any]:
        try:
            usage = result.usage()
            requests_used = getattr(usage, "requests", None)
        except (AttributeError, TypeError):
            requests_used = None
        pop_span(
            attributes={
                "co.agent.requests_used": requests_used,
                "co.agent.final_result": str(result.output),
            },
        )
        return result

    async def on_run_error(
        self,
        ctx: RunContext[CoDeps],
        *,
        error: BaseException,
    ) -> AgentRunResult[Any]:
        pop_span(status="ERROR", status_msg=str(error))
        raise error

    async def before_model_request(
        self,
        ctx: RunContext[CoDeps],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        push_span(
            f"chat {request_context.model.model_name}",
            kind="model",
            attributes={
                "co.model.name": request_context.model.model_name,
                "co.model.input": serialize_messages(list(request_context.messages)),
            },
        )
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[CoDeps],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        usage = response.usage
        record_usage(ctx.deps, usage)
        pop_span(
            attributes={
                "co.model.output": serialize_response(response),
                "co.model.tokens.input": getattr(usage, "input_tokens", 0),
                "co.model.tokens.output": getattr(usage, "output_tokens", 0),
                "co.model.name": response.model_name,
                "co.model.finish_reason": str(response.finish_reason)
                if response.finish_reason
                else None,
            },
        )
        return response

    async def on_model_request_error(
        self,
        ctx: RunContext[CoDeps],
        *,
        request_context: ModelRequestContext,
        error: Exception,
    ) -> ModelResponse:
        pop_span(status="ERROR", status_msg=str(error))
        raise error

    async def before_tool_execute(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        push_span(
            f"tool {call.tool_name}",
            kind="tool",
            attributes={
                "co.tool.name": call.tool_name,
                "co.tool.args": _serialize_tool_args(args),
            },
        )
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
        pop_span(attributes={"co.tool.result": _truncate(result)})
        return result

    async def on_tool_execute_error(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        error: Exception,
    ) -> Any:
        pop_span(status="ERROR", status_msg=str(error))
        raise error


__all__ = ["ObservabilityCapability"]
