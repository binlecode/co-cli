"""Span-payload serialization helpers — compact JSON for span attributes.

Shared by the agent-path ``chat`` span (``SurrogateRecoveryModel``), the
direct-call ``llm_call`` span (``co_cli.llm.call``), and the routing tool
wrapper (``co_cli.agent.toolset``). Kept distinct from
``co_cli.context.summarization.serialize_messages`` (which renders
human-readable redacted text for summarizer prompts, not compact span JSON).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import (
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

_TOOL_RESULT_MAX_CHARS = 16_000


def serialize_messages(messages: list[ModelMessage]) -> str:
    """Serialize message history to a compact JSON string preserving roles + part types."""
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
    """Serialize a ModelResponse's parts to compact JSON for span ``co.model.output``."""
    return json.dumps(_serialize_response_parts(list(response.parts)), default=str)


def serialize_tool_args(args: Any) -> str:
    """Serialize validated tool args for the ``co.tool.args`` span attribute."""
    try:
        return json.dumps(args, default=str)
    except (TypeError, ValueError):
        return str(args)


def truncate_tool_result(value: Any) -> str:
    """Render a tool result for the ``co.tool.result`` span attribute, bounded in length."""
    text = str(value) if not isinstance(value, str) else value
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        return text[:_TOOL_RESULT_MAX_CHARS] + f"\n... [truncated, total {len(text)} chars]"
    return text


__all__ = [
    "serialize_messages",
    "serialize_response",
    "serialize_tool_args",
    "truncate_tool_result",
]
