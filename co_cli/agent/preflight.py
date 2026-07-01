"""Per-step request preflight for the owned (graph-free) turn loop.

Reproduces, as straight-line code, the request-assembly work the pydantic-ai graph
did on co's behalf at each ``ModelRequestNode``:

- ``run_history_processors`` — runs the five (now deps-signature) history processors
  in the canonical order, once per step before the request is built. Its output IS
  persisted to ``TurnState.history`` by the caller (matching the graph at
  ``_agent_graph.py:884``).
- ``clean_message_history`` — a verbatim port of the graph's private
  ``_agent_graph._clean_message_history`` (merge consecutive same-instruction
  ``ModelRequest``s with tool-return/retry parts sorted to the front, merge synthetic
  ``ModelResponse``s, back-fill timestamps). Applied ONLY to the request copy passed to
  the model — NEVER persisted to ``TurnState.history`` (CD-M-1, matching the graph's
  throwaway-copy at ``_agent_graph.py:893``). Ported, not imported: the SDK symbol is
  graph-module-private and removed at Phase 5.
- ``build_static_instructions`` / ``assemble_instructions`` — the static system prompt
  (built once per turn) plus the five per-turn dynamic instructions, emitted as
  ``InstructionPart``s on ``ModelRequestParameters.instruction_parts`` (the model joins
  them, static-first).
- ``build_request_params`` — assembles ``ModelRequestParameters``. The ``function_tools``
  source (native ``FunctionToolset`` schema) is supplied by ``co_cli/agent/dispatch.py``.
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from pydantic.json_schema import GenerateJsonSchema
from pydantic_ai.messages import (
    InstructionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from co_cli.agent._instructions import (
    current_time_prompt,
    deferred_tool_awareness_prompt,
    safety_prompt,
    skill_manifest_prompt,
    wrap_up_prompt,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


async def run_history_processors(history: list[ModelMessage], deps: CoDeps) -> list[ModelMessage]:
    """Run the orchestrator's five history processors in canonical order over ``history``.

    Order: ``elide_old_multimodal_prompts → dedup_tool_results → evict_old_tool_results
    → spill_largest_tool_results → proactive_window_processor`` — the same order the
    graph fired them per ``ModelRequestNode``. The processors take ``deps`` (S6); sync
    ones run inline, async ones (spill, proactive) are awaited. The returned list is the
    request-pressure-reduced history the caller persists to ``TurnState.history``.
    """
    from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC

    messages = history
    for proc in ORCHESTRATOR_SPEC.history_processors:
        result = proc(deps, messages)
        if inspect.isawaitable(result):
            result = await result
        messages = result
    return messages


_INTERRUPTED_TOOL_STUB = "Tool call interrupted; no result."


def fill_unanswered_tool_calls(history: list[ModelMessage]) -> list[ModelMessage]:
    """Insert synthetic tool-return stubs for any unanswered ``ToolCallPart`` (every-step net).

    For any ``ModelResponse`` whose ``ToolCallPart`` ids are not answered by the
    immediately-following message, **insert** a fresh ``ModelRequest`` carrying a
    ``ToolReturnPart`` stub per unanswered id directly after that response — never mutate
    a following message (CD-M-1: the stub must land between the unanswered response and any
    abort marker for ``clean_message_history`` to merge it into a protocol-valid request).

    A no-op under normal intra-turn flow (the loop appends a ``ModelRequest`` after every
    dispatch, so no response is left unanswered within a turn). Load-bearing only on the
    first step of the turn following an interrupt, where ``_interrupted_result`` retains the
    unanswered response (the deliberate drop→fill divergence, milestone OQ-6) and appends the
    abort marker after it. Idempotent: once a stub is inserted, the response is answered, so a
    re-run is a no-op.
    """
    result: list[ModelMessage] = []
    for index, message in enumerate(history):
        result.append(message)
        if not isinstance(message, ModelResponse):
            continue
        call_names = {
            part.tool_call_id: part.tool_name
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if not call_names:
            continue
        following = history[index + 1] if index + 1 < len(history) else None
        answered: set[str] = set()
        if isinstance(following, ModelRequest):
            answered = {
                part.tool_call_id
                for part in following.parts
                if isinstance(part, ToolReturnPart | RetryPromptPart)
            }
        stubs = [
            ToolReturnPart(
                tool_name=call_names[call_id],
                tool_call_id=call_id,
                content=_INTERRUPTED_TOOL_STUB,
            )
            for call_id in call_names
            if call_id not in answered
        ]
        if stubs:
            result.append(ModelRequest(parts=stubs))
    return result


def clean_message_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Merge consecutive messages for clean user/assistant boundaries (graph parity).

    Verbatim port of pydantic-ai's private ``_agent_graph._clean_message_history``: merge
    consecutive ``ModelRequest``s that share instructions (tool-return / retry parts sorted
    to the front of the merged request, timestamps back-filled), and merge consecutive
    synthetic ``ModelResponse``s (those with no provider id/name/model_name). Applied to the
    request copy only — the caller must NOT persist the result to ``TurnState.history``.
    """
    clean_messages: list[ModelMessage] = []
    for message in messages:
        last_message = clean_messages[-1] if clean_messages else None

        if isinstance(message, ModelRequest):
            if (
                last_message is not None
                and isinstance(last_message, ModelRequest)
                and (
                    not last_message.instructions
                    or not message.instructions
                    or last_message.instructions == message.instructions
                )
            ):
                parts = [*last_message.parts, *message.parts]
                parts.sort(
                    key=lambda x: 0 if isinstance(x, ToolReturnPart | RetryPromptPart) else 1
                )
                clean_messages[-1] = ModelRequest(
                    parts=parts,
                    instructions=last_message.instructions or message.instructions,
                    timestamp=message.timestamp or last_message.timestamp,
                )
            else:
                clean_messages.append(message)
        elif isinstance(message, ModelResponse):
            if (
                last_message is not None
                and isinstance(last_message, ModelResponse)
                and last_message.provider_response_id is None
                and last_message.provider_name is None
                and last_message.model_name is None
                and message.provider_response_id is None
                and message.provider_name is None
                and message.model_name is None
            ):
                clean_messages[-1] = replace(
                    last_message, parts=[*last_message.parts, *message.parts]
                )
            else:
                clean_messages.append(message)
    return clean_messages


def build_static_instructions(deps: CoDeps) -> str:
    """Join the orchestrator's static instruction builders into the system prompt.

    Composes the static instruction builders: call each builder in order, drop
    empties, join with double newlines. Built once per turn by the owned driver (stable
    across steps), not once per step — the static block is the cacheable prefix.
    """
    from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC

    parts = [b(deps) for b in ORCHESTRATOR_SPEC.static_instruction_builders]
    return "\n\n".join(p for p in parts if p)


def assemble_instructions(
    deps: CoDeps,
    *,
    static_instructions: str,
    messages: list[ModelMessage],
    request_count: int,
) -> list[InstructionPart]:
    """Build the instruction parts for one step: static prefix + per-turn dynamic parts.

    The static prefix (``dynamic=False``) carries the cached system prompt; the five
    dynamic parts (``dynamic=True``) are evaluated every step in registration
    order (safety → wrap-up → current-time → deferred → skill manifest). Empty dynamic
    outputs are dropped (the model joins the rest with double newlines, static-first).
    ``messages`` feeds the safety warnings; ``request_count`` (completed requests so far)
    feeds the wrap-up nudge.
    """
    parts: list[InstructionPart] = []
    if static_instructions:
        parts.append(InstructionPart(content=static_instructions, dynamic=False))
    dynamic = [
        safety_prompt(deps, messages=messages),
        wrap_up_prompt(deps, request_count=request_count),
        current_time_prompt(deps),
        deferred_tool_awareness_prompt(deps),
        skill_manifest_prompt(deps),
    ]
    parts.extend(InstructionPart(content=text, dynamic=True) for text in dynamic if text)
    return parts


async def build_tool_defs(deps: CoDeps) -> list[ToolDefinition]:
    """Source the per-turn ``function_tools`` defs for the model request.

    Reads the visibility-filtered routing toolset (native + MCP, DEFERRED hidden until
    revealed, Google self-gated, resume-narrowed) — and
    returns each tool's ``ToolDefinition``. The owned loop uses the toolset only as a
    schema source; dispatch is co's own ``dispatch_tools``.
    """
    from co_cli.agent.dispatch import get_visible_tools, make_run_context

    ctx = make_run_context(deps)
    tools = await get_visible_tools(deps, ctx)
    return [tool.tool_def for tool in tools.values()]


OUTPUT_TOOL_NAME = "final_result"
OUTPUT_TOOL_DESCRIPTION = "The final response which ends this conversation"
_MARKDOWN_FENCE = re.compile(r"```(?:\w+)?\n(\{.*?\})\s*(?:\n?```|\Z)", re.DOTALL)


class _ToolJsonSchemaGenerator(GenerateJsonSchema):
    """Pydantic schema generator that drops the largely-useless per-property titles.

    Reproduces the single override pydantic-ai applies for output-tool schemas so co
    owns ``final_result`` schema generation through pydantic's documented
    ``GenerateJsonSchema`` extension point, with no reach into the private
    ``pydantic_ai._output`` module.
    """

    def _named_required_fields_schema(self, named_required_fields: Any) -> Any:
        schema = super()._named_required_fields_schema(named_required_fields)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return schema


class _OutputToolValidator:
    """Turns a ``final_result`` tool call's args into the ``output_type`` instance.

    Owns the model-output validation path: dict args validate directly; string args
    have any surrounding markdown code fence stripped (some models wrap the JSON in a
    ```json fence) before JSON validation. Raises ``pydantic.ValidationError`` on
    mismatch, which the owned loop catches to re-prompt.
    """

    def __init__(self, output_type: type[BaseModel]) -> None:
        self._output_type = output_type

    def validate(self, data: str | dict[str, Any] | None) -> BaseModel:
        if isinstance(data, str):
            if not data.startswith("{"):
                match = _MARKDOWN_FENCE.search(data)
                data = match.group(1) if match else data
            return self._output_type.model_validate_json(data or "{}")
        return self._output_type.model_validate(data or {})


def build_output_toolset(
    output_type: type[BaseModel],
) -> tuple[list[ToolDefinition], _OutputToolValidator]:
    """Build the subagent's ``final_result`` output-tool def + validator (OQ-4 b).

    Returns ``(output_tool_defs, validator)``: the ``ToolDefinition`` list for
    ``ModelRequestParameters.output_tools`` and a validator whose ``.validate(args)``
    turns a ``final_result`` call's args into the ``output_type`` instance.

    co owns this: the ``final_result`` name + description + JSON schema (per-property
    titles stripped via ``_ToolJsonSchemaGenerator``) reproduce what the dream-reviewer
    model was tuned to, generated through public pydantic rather than a reach into the
    private ``pydantic_ai._output`` module.

    The model's docstring becomes the tool description (lifted out of the schema, as
    pydantic-ai does); models without a docstring fall back to the default.
    """
    schema = TypeAdapter(output_type).json_schema(schema_generator=_ToolJsonSchemaGenerator)
    description = schema.pop("description", None) or OUTPUT_TOOL_DESCRIPTION
    tool_def = ToolDefinition(
        name=OUTPUT_TOOL_NAME,
        description=description,
        parameters_json_schema=schema,
        kind="output",
    )
    return [tool_def], _OutputToolValidator(output_type)


def build_request_params(
    *,
    instruction_parts: list[InstructionPart],
    function_tools: list[ToolDefinition] | None = None,
    output_tools: list[ToolDefinition] | None = None,
    allow_text_output: bool = True,
) -> ModelRequestParameters:
    """Assemble ``ModelRequestParameters`` for one owned-loop model request.

    ``function_tools`` is the native tool-def list (sourced via
    ``co_cli/agent/dispatch.py``); ``output_tools`` + ``allow_text_output=False`` drive the
    subagent's structured ``final_result`` path. ``instruction_parts`` are bridged into the
    request by the model's ``_get_instruction_parts`` on the ``direct`` path.

    ``output_mode`` is set to ``'tool'`` whenever ``output_tools`` are present — the model's
    ``prepare_request`` **strips** ``output_tools`` when ``output_mode != 'tool'`` (and only
    fills the mode from the profile default when it is ``'auto'``, not the ``'text'`` default).
    Setting it explicitly is required for the ``final_result`` tool to reach the model.
    """
    has_output_tools = bool(output_tools)
    return ModelRequestParameters(
        function_tools=function_tools or [],
        output_tools=output_tools or [],
        output_mode="tool" if has_output_tools else "text",
        allow_text_output=allow_text_output,
        instruction_parts=instruction_parts,
    )
