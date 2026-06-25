"""Owned-loop tool dispatch — folds ``_CallSeamToolset`` into straight-line code.

The graph path routed tool calls through ``_CallSeamToolset`` (the outermost wrapper on
``deps.toolset``), which hosts the ``tool`` span, the per-model-request cap, and MCP-result
spill, while the SDK's tool manager validated args and wrapped results upstream. The owned
loop reproduces all of that here:

- ``dispatch_tools`` counts the step's calls through ``ToolCapState`` **before** fan-out
  (pre-fan-out shed, CD-m-3 — execute index ``< cap``, shed the rest with an exceeded
  payload), validates each within-cap call's args via the tool's own validator, dispatches
  over co's existing ``tool_dispatch_sem`` (concurrent calls in parallel, sequential tools
  serialized), applies MCP spill, and emits the ``co.tool.*`` span per call.
- It dispatches on the **unwrapped** routing toolset (``deps.toolset.wrapped``) so the folded
  span/cap/spill are not double-applied by the still-live ``_CallSeamToolset`` seam.
- Approval is resolved upstream by ``collect_inline_approvals`` (``agent/approval.py``),
  which prompts the user before fan-out and passes ``denials`` (denied calls) +
  ``approved_ids`` (the in-body raisers' gate) into ``dispatch_tools``. Subagents register
  tools ``requires_approval=False``, so the collector is a no-op for them.

Phase-2 simplifications (behind the default-off flag, read-only-tool gate): the args-retry
loop is single-shot (a validation/``ModelRetry`` failure returns a ``RetryPromptPart`` the
model reacts to next step, rather than re-driving in place), and a ``ToolReturn``'s secondary
multimodal ``content`` channel (image_view pixels) is not yet spawned as a ``UserPromptPart``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import RetryPromptPart, ToolCallPart, ToolReturn, ToolReturnPart
from pydantic_ai.usage import RunUsage

from co_cli.config.tuning import SPILL_THRESHOLD_CHARS
from co_cli.deps import CoDeps, ToolSourceEnum
from co_cli.fileio.spill import spill_with_span
from co_cli.observability.serialize import serialize_tool_args, truncate_tool_result
from co_cli.observability.tracing import current_span, pop_span, push_span
from co_cli.tools.display import format_for_display, get_tool_start_args_display
from co_cli.tools.tool_call_limit import make_exceeded_payload

if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset
    from pydantic_ai.toolsets.abstract import ToolsetTool

    from co_cli.agent.turn_state import ToolCapState
    from co_cli.display.core import Frontend


def make_run_context(
    deps: CoDeps,
    *,
    tool_name: str | None = None,
    tool_call_id: str = "",
    tool_call_approved: bool = False,
) -> RunContext[CoDeps]:
    """Build the synthetic ``RunContext`` the owned loop passes to toolset get/call.

    The graph supplied a real ``RunContext``; the owned loop owns the loop instead, so it
    constructs a minimal one (deps + raw model + fresh usage). ``model`` may be the raw
    provider model; the visibility filter and co's tools read ``ctx.deps``.

    ``tool_call_approved`` is load-bearing only for the two in-body raisers — shell
    (``execute.py``) and clarify (``user_input.py``) read it to decide whether to execute
    or raise ``ApprovalRequired``/``QuestionRequired``. Catalog ``is_approval_required``
    tools never read it on the owned path (``FunctionToolset.call_tool`` has no approval
    gate), so for them the collector's denial entry is the gate, not this flag.
    """
    raw_model = deps.model.model if deps.model else None
    return RunContext(
        deps=deps,
        model=raw_model,  # type: ignore[arg-type]
        usage=RunUsage(),
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call_approved=tool_call_approved,
    )


def _routing_toolset(deps: CoDeps) -> AbstractToolset[CoDeps]:
    """Return the visibility-filtered routing toolset without the ``_CallSeamToolset`` seam.

    ``deps.toolset`` is ``_CallSeamToolset(filtered(combined([native, *mcp])))``; its
    ``.wrapped`` is the filtered routing stack. Dispatching on the unwrapped stack runs the
    real tool while the folded span/cap/spill below replace the seam's copies.
    """
    return getattr(deps.toolset, "wrapped", deps.toolset)


async def get_visible_tools(
    deps: CoDeps, ctx: RunContext[CoDeps]
) -> dict[str, ToolsetTool[CoDeps]]:
    """Return the per-turn visible tools (native + MCP, DEFERRED/Google/resume filtered).

    Reads through the same filtered routing toolset the graph uses, so the owned loop's
    tool set is identical to the graph's for the same ``deps`` state.
    """
    return await _routing_toolset(deps).get_tools(ctx)


def _to_return_part(call: ToolCallPart, result: Any) -> ToolReturnPart:
    """Wrap a raw tool result into a ``ToolReturnPart`` (graph ``_call_tool`` tail parity)."""
    if isinstance(result, ToolReturn):
        return ToolReturnPart(
            tool_name=call.tool_name,
            tool_call_id=call.tool_call_id,
            content=result.return_value,
            metadata=result.metadata,
        )
    return ToolReturnPart(
        tool_name=call.tool_name,
        tool_call_id=call.tool_call_id,
        content=result,
    )


def _validate_args(call: ToolCallPart, tool: ToolsetTool[CoDeps], ctx: RunContext[CoDeps]) -> dict:
    """Validate a call's args via the tool's own validator (coercion + schema), like the SDK.

    Raises ``ValidationError`` on schema failure and ``ModelRetry`` from a custom
    ``args_validator_func``; the caller turns either into a ``RetryPromptPart``.
    """
    raw = call.args
    if isinstance(raw, str):
        args_dict = tool.args_validator.validate_json(raw or "{}")
    else:
        args_dict = tool.args_validator.validate_python(raw or {})
    if tool.args_validator_func is not None:
        tool.args_validator_func(ctx, **args_dict)
    return args_dict


async def _run_tool_body(
    call: ToolCallPart,
    deps: CoDeps,
    routing: AbstractToolset[CoDeps],
    tool: ToolsetTool[CoDeps],
    info: Any,
    ctx: RunContext[CoDeps],
) -> ToolReturnPart | RetryPromptPart:
    """Validate args, dispatch, apply MCP spill, and wrap the result (no span/frontend).

    A validation or ``ModelRetry`` failure returns a ``RetryPromptPart``; an unexpected
    exception propagates to ``_execute_one`` (which closes the span ERROR).
    """
    name = call.tool_name
    tool_id = call.tool_call_id
    try:
        args_dict = _validate_args(call, tool, ctx)
    except ValidationError as exc:
        return RetryPromptPart(content=str(exc), tool_name=name, tool_call_id=tool_id)
    except ModelRetry as exc:
        return RetryPromptPart(content=exc.message, tool_name=name, tool_call_id=tool_id)

    try:
        async with deps.tool_dispatch_sem:
            result = await routing.call_tool(name, args_dict, ctx, tool)
    except ModelRetry as exc:
        return RetryPromptPart(content=exc.message, tool_name=name, tool_call_id=tool_id)

    if isinstance(result, str) and info and info.source == ToolSourceEnum.MCP:
        threshold = (
            info.spill_threshold_chars
            if info.spill_threshold_chars is not None
            else SPILL_THRESHOLD_CHARS
        )
        result = spill_with_span(
            result,
            tool_name=name,
            tool_results_dir=deps.tool_results_dir,
            threshold_chars=threshold,
        )
    return _to_return_part(call, result)


async def _execute_one(
    call: ToolCallPart,
    deps: CoDeps,
    routing: AbstractToolset[CoDeps],
    tool: ToolsetTool[CoDeps],
    frontend: Frontend | None,
    tool_call_approved: bool = False,
) -> ToolReturnPart | RetryPromptPart:
    """Validate, dispatch, spill, and span one within-cap tool call (folded seam)."""
    name = call.tool_name
    tool_id = call.tool_call_id
    info = deps.tool_catalog.get(name)
    if frontend is not None:
        frontend.on_tool_start(tool_id, name, get_tool_start_args_display(name, call))
        deps.runtime.tool_progress_callback = lambda msg, _tid=tool_id: frontend.on_tool_progress(
            _tid, msg
        )

    ctx = make_run_context(
        deps, tool_name=name, tool_call_id=tool_id, tool_call_approved=tool_call_approved
    )
    push_span(
        f"tool {name}",
        kind="tool",
        attributes={"co.tool.name": name, "co.tool.args": serialize_tool_args(call.args)},
    )
    try:
        args_chars = len(json.dumps(call.args, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        args_chars = 0
    current_span().set_attribute("co.tool.args_chars", args_chars)

    try:
        part = await _run_tool_body(call, deps, routing, tool, info, ctx)
    except Exception as exc:
        pop_span(status="ERROR", status_msg=str(exc))
        if frontend is not None:
            frontend.on_tool_complete(tool_id, None)
            deps.runtime.tool_progress_callback = None
        raise

    span = current_span()
    span.set_attribute("co.tool.result", truncate_tool_result(getattr(part, "content", "")))
    span.set_attribute("co.tool.result_size", len(str(getattr(part, "content", ""))))
    if info:
        span.set_attribute("co.tool.source", info.source.value)
        span.set_attribute("co.tool.requires_approval", info.is_approval_required)
    pop_span()

    if frontend is not None:
        deps.runtime.tool_progress_callback = None
        if isinstance(part, ToolReturnPart):
            frontend.on_tool_complete(tool_id, format_for_display(part.content))
        else:
            frontend.on_tool_complete(tool_id, None)
    return part


async def dispatch_tools(
    tool_calls: list[ToolCallPart],
    deps: CoDeps,
    *,
    cap_state: ToolCapState,
    frontend: Frontend | None = None,
    denials: dict[str, ToolReturnPart] | None = None,
    approved_ids: set[str] | None = None,
) -> list[ToolReturnPart | RetryPromptPart]:
    """Dispatch one model request's tool calls with the pre-fan-out cap, in original order.

    Counts the step's calls through ``cap_state`` and computes the shed boundary before any
    execution: calls at index ``>= cap`` get an exceeded payload; within-cap calls that were
    denied (``denials``) get their denial part; the rest execute (concurrent tools in
    parallel under ``tool_dispatch_sem``, sequential tools serialized). Results preserve the
    input order.

    A call whose ``tool_call_id`` is in ``approved_ids`` runs with
    ``ctx.tool_call_approved=True`` so the in-body raisers (shell, clarify) execute instead
    of raising. Catalog approval-gated tools ignore the flag (see ``make_run_context``).
    """
    denials = denials or {}
    approved_ids = approved_ids or set()
    cap_state.note_calls(len(tool_calls))
    boundary = cap_state.shed_boundary(len(tool_calls))

    results: list[ToolReturnPart | RetryPromptPart | None] = [None] * len(tool_calls)

    for i in range(boundary, len(tool_calls)):
        call = tool_calls[i]
        results[i] = ToolReturnPart(
            tool_name=call.tool_name,
            content=json.dumps(make_exceeded_payload(len(tool_calls))),
            tool_call_id=call.tool_call_id,
        )

    within = [i for i in range(boundary) if tool_calls[i].tool_call_id not in denials]
    for i in range(boundary):
        if tool_calls[i].tool_call_id in denials:
            results[i] = denials[tool_calls[i].tool_call_id]

    ctx = make_run_context(deps)
    tools = await get_visible_tools(deps, ctx)

    async def _run(i: int) -> None:
        call = tool_calls[i]
        tool = tools.get(call.tool_name)
        if tool is None:
            results[i] = RetryPromptPart(
                content=f"Unknown tool {call.tool_name!r}.",
                tool_name=call.tool_name,
                tool_call_id=call.tool_call_id,
            )
            return
        results[i] = await _execute_one(
            call,
            deps,
            _routing_toolset(deps),
            tool,
            frontend,
            tool_call_approved=call.tool_call_id in approved_ids,
        )

    concurrent = [i for i in within if not _is_sequential(tools, tool_calls[i])]
    sequential = [i for i in within if _is_sequential(tools, tool_calls[i])]
    if concurrent:
        await asyncio.gather(*(_run(i) for i in concurrent))
    for i in sequential:
        await _run(i)

    return [r for r in results if r is not None]


def _is_sequential(tools: dict[str, ToolsetTool[CoDeps]], call: ToolCallPart) -> bool:
    """Whether a call's tool must run serially (not concurrent-safe)."""
    tool = tools.get(call.tool_name)
    return bool(tool and getattr(tool.tool_def, "sequential", False))
