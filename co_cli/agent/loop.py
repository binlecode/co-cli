"""Owned (graph-free) turn loop — drives the Phase-1 ``model_turn`` client.

``run_turn_owned`` is the sole orchestrator turn loop;
it drives the per-turn control flow as straight-line code: materialize the user
prompt, then per step run the history processors, assemble instructions, build the request
params from the tool-def schema source, drive ``model_turn`` (rendering deltas), classify the
assembled response, dispatch tool calls through co's owned ``dispatch_tools``, and repeat
until the model emits no tool call (final text) — or a typed terminal condition fires
(tool-cap hard stop, request cap, reasoning overflow, provider error, interrupt).

**Scaffolding tenet (the canonical baseline).** ``_drive_model_request`` is the shared
per-step model-request primitive, used by both this orchestrator loop and the owned subagent
driver ``run_standalone_owned``. The two drivers share the construction scaffolding
(``_build_subagent_toolset`` mirrors the orchestrator toolset), every preflight builder
(history processors, ``assemble_instructions``, ``build_tool_defs``, ``build_request_params``,
``clean_message_history``), ``dispatch_tools``, ``collect_inline_approvals``, ``ToolCapState``,
and this request primitive. They differ ONLY by workflow: instruction assembly, request params
(``output_tools``), termination predicate, rendering, recovery, and approval. The one intended
construction divergence is the subagent's FLAT_EXACT plain ``FunctionToolset`` vs the
orchestrator/VISIBILITY_MODEL filtered routing stack — ``dispatch_tools``/``build_tool_defs``
read ``deps.toolset`` generically and treat both identically. No parameterized mega-loop hosts
the two callers; identical control flow is not the tenet — shared scaffolding is.

A single in-loop catch classifies provider errors (``recovery.classify_provider_error``) and
recovers inline — context overflow strip-then-summarizes and retries once, an HTTP 400
tool-call rejection reflects to the model within a budget, length-truncated answers continue
with a boosted token budget, and transient/timeout/malformed errors surface terminal. Interrupt
appends the abort marker; every step's preflight fills unanswered tool-call ids.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import (
    InstructionPart,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.agent.approval import collect_inline_approvals
from co_cli.agent.dispatch import dispatch_tools
from co_cli.agent.preflight import (
    assemble_instructions,
    build_output_toolset,
    build_request_params,
    build_static_instructions,
    build_tool_defs,
    clean_message_history,
    fill_unanswered_tool_calls,
    run_history_processors,
)
from co_cli.agent.recovery import (
    _HTTP_400_REFLECT_BACKOFF_SECS,
    ErrorAction,
    ErrorClass,
    classify_provider_error,
    length_retry_settings,
)
from co_cli.agent.turn_state import ToolCapState, TurnExit, TurnResult, TurnState
from co_cli.config.llm import resolve_request_limit
from co_cli.config.tuning import TOOL_CAP_HARD_STOP_CONSECUTIVE
from co_cli.context.compaction import recover_overflow_history
from co_cli.display.stream_renderer import StreamRenderer, handle_model_request_event
from co_cli.llm.model_turn import model_turn
from co_cli.observability.tracing import current_span, trace
from co_cli.observability.usage import record_usage

if TYPE_CHECKING:
    from pydantic_ai.messages import BinaryContent, ModelMessage
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.settings import ModelSettings

    from co_cli.deps import CoDeps
    from co_cli.display.core import Frontend

logger = logging.getLogger(__name__)

_REASONING_OVERFLOW_MESSAGE = (
    "Reasoning used the entire output budget before answering — "
    "simplify your request, or raise max_tokens for this model."
)

TOOL_CAP_NO_ANSWER_TEXT = (
    "Stopped after hitting the tool-call cap before producing an answer. "
    "The work so far is in history — re-ask or narrow the request."
)


def _last_assistant_text(messages: list[ModelMessage]) -> str:
    """Return the most recent non-empty assistant text in a message list, or ''.

    Scans backwards for the latest ModelResponse whose TextParts join to non-empty
    text. Used to salvage the model's last visible answer when a cap terminates the
    turn without a usable final output.
    """
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            text = "".join(p.content for p in msg.parts if isinstance(p, TextPart)).strip()
            if text:
                return text
    return ""


def _is_reasoning_overflow(response: ModelResponse) -> bool:
    """True when reasoning consumed the whole output budget before any answer token.

    The typed replacement for the graph's string-matched ``_REASONING_OVERFLOW_SIGNATURE``:
    ``finish_reason == 'length'`` and the response carries no answer content (empty or
    thinking-only). Text-present + ``length`` is a length truncation (continuation retry is
    Phase 4), not a reasoning overflow.
    """
    if response.finish_reason != "length":
        return False
    has_answer = any(isinstance(p, TextPart | ToolCallPart) for p in response.parts)
    return not has_answer


def _final_text(response: ModelResponse) -> str:
    return "".join(p.content for p in response.parts if isinstance(p, TextPart)).strip()


async def _drive_model_request(
    deps: CoDeps,
    request_messages: list[ModelMessage],
    params: ModelRequestParameters,
    settings: ModelSettings | None,
    renderer: StreamRenderer | None,
    stall_window: float,
) -> tuple[ModelResponse, RunUsage]:
    """Drive one streamed model request, rendering deltas, under the stall window.

    Re-arms the model-progress stall timeout on every streamed event (time-since-last-token,
    not an absolute deadline). Returns the assembled (repaired, when Ollama) response and a
    ``RunUsage`` carrying this request's token counts.
    """
    raw_model = deps.model.model
    repair = deps.config.llm.uses_ollama()
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(stall_window) as stall:
        async with model_turn(
            raw_model, request_messages, params, settings, repair=repair
        ) as stream:
            stall.reschedule(loop.time() + stall_window)
            async for event in stream:
                stall.reschedule(loop.time() + stall_window)
                if renderer is not None:
                    handle_model_request_event(event, renderer)
            response = stream.get()
            usage = stream.usage()
    turn_usage = RunUsage()
    turn_usage.incr(usage)
    return response, turn_usage


def _tool_calls(response: ModelResponse) -> list[ToolCallPart]:
    return [p for p in response.parts if isinstance(p, ToolCallPart)]


def _materialize_history(
    message_history: list[ModelMessage],
    user_input: str | list[str | BinaryContent] | None,
) -> list[ModelMessage]:
    """Append the in-flight user prompt to the working history (graph ``agent.iter`` parity)."""
    history = list(message_history)
    if user_input is not None:
        history.append(ModelRequest(parts=[UserPromptPart(content=user_input)]))
    return history


def _instruction_parts_for_step(
    deps: CoDeps,
    static_instructions: str,
    processed: list[ModelMessage],
    requests_completed: int,
) -> list[InstructionPart]:
    return assemble_instructions(
        deps,
        static_instructions=static_instructions,
        messages=processed,
        request_count=requests_completed,
    )


@trace("co.turn", new_trace=True)
async def run_turn_owned(
    *,
    user_input: str | list[str | BinaryContent],
    deps: CoDeps,
    message_history: list[ModelMessage],
    model_settings: ModelSettings | None = None,
    frontend: Frontend,
) -> TurnResult:
    """Execute one orchestrator turn through the owned loop.

    Returns a ``TurnResult`` the chat loop pattern-matches
    on. Provider errors are classified and recovered inline inside the step loop (the single
    classification site, CD-M-2); the turn boundary here owns only interrupt (which can fire
    during ``dispatch_tools``, not just the model request) plus a generic last-resort.
    """
    deps.runtime.reset_for_turn()
    deps.usage_accumulator.reset()
    deps.runtime.status_callback = frontend.on_status
    deps.runtime.frontend = frontend
    frontend.begin_waiting()

    settings = model_settings if model_settings is not None else deps.model.settings
    static_instructions = build_static_instructions(deps)
    state = TurnState(
        history=_materialize_history(message_history, user_input),
        cap_state=ToolCapState(),
    )
    request_limit = resolve_request_limit(deps.config.llm)
    stall_window = deps.config.llm.run_stall_timeout_secs
    renderer = StreamRenderer(frontend, reasoning_display=deps.session.reasoning_display)
    turn_usage = RunUsage()
    span = current_span()

    try:
        result = await _orchestrator_step_loop(
            deps=deps,
            state=state,
            settings=settings,
            static_instructions=static_instructions,
            request_limit=request_limit,
            stall_window=stall_window,
            renderer=renderer,
            frontend=frontend,
            turn_usage=turn_usage,
        )
        return result
    except KeyboardInterrupt:
        # Headless/sync interrupt (no surrounding event loop to cancel the task):
        # absorb directly into the drop->fill interrupted result.
        return _interrupted_result(state, turn_usage)
    except asyncio.CancelledError:
        # Cancellation we do NOT own — a user Esc (REPL) or an outer deadline
        # (eval/test asyncio.timeout). Swallowing it here would defeat outer timeouts
        # (a timed-out turn would masquerade as a silent empty answer). Stash the
        # drop->fill result for the turn caller that owns the cancellation, then
        # re-raise so the timeout propagates and structured concurrency holds.
        deps.runtime.pending_interrupt_result = _interrupted_result(state, turn_usage)
        raise
    except Exception as exc:
        # Last-resort: a truly unexpected exception escaping dispatch_tools. Provider
        # errors are classified and surfaced in-loop (CD-M-2), so they never reach here;
        # dispatch_tools absorbs tool errors as payloads, so this fires only on a bug.
        frontend.on_status(f"Provider error — turn ended: {exc}")
        span.add_event("provider_error", {"error.type": type(exc).__name__})
        return _terminal_result(
            state, turn_usage, outcome="error", exit_reason=TurnExit.PROVIDER_ERROR
        )
    finally:
        renderer.close()
        frontend.cleanup()
        if turn_usage.input_tokens or turn_usage.output_tokens:
            record_usage(deps, turn_usage)
        span.set_attribute(
            "turn.outcome", state.exit_reason.name if state.exit_reason else "error"
        )
        span.set_attribute("turn.model_requests", state.model_requests)
        span.set_attribute("turn.input_tokens", turn_usage.input_tokens)
        span.set_attribute("turn.output_tokens", turn_usage.output_tokens)
        deps.runtime.tool_progress_callback = None
        deps.runtime.frontend = None


async def _orchestrator_step_loop(
    *,
    deps: CoDeps,
    state: TurnState,
    settings: ModelSettings | None,
    static_instructions: str,
    request_limit: int | None,
    stall_window: float,
    renderer: StreamRenderer,
    frontend: Frontend,
    turn_usage: RunUsage,
) -> TurnResult:
    """Run the per-step orchestrator loop until a terminal condition, returning a TurnResult.

    ``settings`` is a mutable loop-local: the length-continuation retry boosts it in place so
    the larger token budget persists across the re-run steps. The provider-error catch around
    ``_drive_model_request`` is the single classification site (CD-M-2) — it both recovers
    (overflow / 400) and surfaces terminal errors with their graph-parity messages.
    """
    span = current_span()
    requests_completed = 0
    while True:
        if request_limit is not None and requests_completed >= request_limit:
            frontend.on_status(
                f"Model-request cap reached ({request_limit} LLM calls) — stopping."
            )
            return _terminal_result(
                state, turn_usage, outcome="error", exit_reason=TurnExit.REQUEST_CAP
            )

        processed = await run_history_processors(state.history, deps)
        processed = fill_unanswered_tool_calls(processed)
        state.history = processed
        instr = _instruction_parts_for_step(
            deps, static_instructions, processed, requests_completed
        )
        tool_defs = await build_tool_defs(deps)
        params = build_request_params(instruction_parts=instr, function_tools=tool_defs)
        request_messages = clean_message_history(processed)

        try:
            response, step_usage = await _drive_model_request(
                deps, request_messages, params, settings, renderer, stall_window
            )
        except (
            ModelHTTPError,
            ModelAPIError,
            httpx.ReadError,
            TimeoutError,
            UnexpectedModelBehavior,
        ) as exc:
            err = classify_provider_error(exc)
            terminal = await _recover_provider_error(
                err, exc, state, deps, frontend, span, turn_usage
            )
            if terminal is not None:
                return terminal
            continue
        requests_completed += 1
        state.model_requests += 1
        turn_usage.incr(step_usage)

        calls = _tool_calls(response)
        if not calls:
            renderer.finish()
            if _is_reasoning_overflow(response):
                state.history = [*state.history, response]
                frontend.on_status(_REASONING_OVERFLOW_MESSAGE)
                state.exit_reason = TurnExit.REASONING_OVERFLOW
                return _terminal_result(
                    state, turn_usage, outcome="error", exit_reason=TurnExit.REASONING_OVERFLOW
                )
            boosted = length_retry_settings(response, settings)
            if boosted is not None:
                # Length-continuation retry: discard the truncated partial (do NOT append it
                # to history) and re-run from the originating user turn with a larger budget.
                # Dropping the partial keeps the prompt from appearing twice and guarantees
                # history ends on a request, never a bare assistant turn.
                settings = boosted
                frontend.on_status(
                    f"Response truncated — retrying with {boosted['max_tokens']:,} output tokens…"
                )
                continue
            state.history = [*state.history, response]
            output = _final_text(response)
            if not renderer.streamed_text and output:
                frontend.on_final_output(output)
            _emit_output_limit_diagnostics(response, deps, frontend, span)
            state.exit_reason = TurnExit.FINAL_TEXT
            return _continue_result(state, turn_usage, output)

        state.history = [*state.history, response]
        resolution = await collect_inline_approvals(calls, deps, frontend)
        parts = await dispatch_tools(
            calls,
            deps,
            cap_state=state.cap_state,
            frontend=frontend,
            denials=resolution.denials,
            approved_ids=resolution.approved_ids,
        )
        state.history = [*state.history, ModelRequest(parts=parts)]

        if state.cap_state.hard_stop:
            frontend.on_status(
                f"Tool-call cap exceeded {TOOL_CAP_HARD_STOP_CONSECUTIVE} consecutive"
                " model requests — stopping."
            )
            salvaged = _last_assistant_text(state.history) or TOOL_CAP_NO_ANSWER_TEXT
            if not renderer.streamed_text:
                frontend.on_final_output(salvaged)
            state.exit_reason = TurnExit.TOOL_CAP
            return _continue_result(state, turn_usage, salvaged)


async def _recover_provider_error(
    err: ErrorClass,
    exc: Exception,
    state: TurnState,
    deps: CoDeps,
    frontend: Frontend,
    span: Any,
    turn_usage: RunUsage,
) -> TurnResult | None:
    """Apply in-loop recovery for a classified provider error (the single catch's body).

    Returns None to retry the step loop (overflow compacted, 400 reflected), or a terminal
    ``TurnResult`` when the error is terminal or recovery/budget is exhausted. Overflow NEVER
    falls through to the 400 path (graph invariant) — the classifier routes them disjointly.
    """
    match err.action:
        case ErrorAction.RECOVER_OVERFLOW:
            if not state.overflow_recovery_attempted:
                state.overflow_recovery_attempted = True
                # recover_overflow_history self-commits (commit_compaction + thrash reset);
                # the loop only assigns its return — no extra bookkeeping (CD-m-4).
                compacted = await recover_overflow_history(deps, state.history)
                if compacted is not None:
                    state.history = compacted
                    frontend.on_status("Context overflow — compacting and retrying...")
                    return None
            frontend.on_status("Context overflow — unrecoverable.")
            span.add_event(*err.span_event)
            return _terminal_result(
                state, turn_usage, outcome="error", exit_reason=err.exit_reason
            )
        case ErrorAction.REFLECT_400:
            if state.tool_reformat_budget > 0:
                state.tool_reformat_budget -= 1
                state.history = [*state.history, _reflection_request(exc)]
                frontend.on_status("Tool call rejected (HTTP 400), reflecting to model...")
                await asyncio.sleep(_HTTP_400_REFLECT_BACKOFF_SECS)
                return None
            frontend.on_status(err.message)
            span.add_event(*err.span_event)
            return _terminal_result(
                state, turn_usage, outcome="error", exit_reason=err.exit_reason
            )
        case _:
            frontend.on_status(err.message)
            span.add_event(*err.span_event)
            return _terminal_result(
                state, turn_usage, outcome="error", exit_reason=err.exit_reason
            )


def _reflection_request(exc: Exception) -> ModelRequest:
    """Build the HTTP 400 tool-call reflection nudge (verbatim graph wording)."""
    body = getattr(exc, "body", exc)
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "Your previous tool call was rejected by the "
                    f"model provider: {body}. Please reformulate "
                    "your tool call with valid JSON arguments."
                )
            )
        ]
    )


def _emit_output_limit_diagnostics(
    response: ModelResponse,
    deps: CoDeps,
    frontend: Frontend,
    span: Any,
) -> None:
    """Emit finish-reason + context-overflow status after a completed turn (graph parity).

    Sources the diagnostics off the final ``ModelResponse`` directly (CD-m-3):
    ``response.finish_reason`` gates the truncation status, and ``response.usage.input_tokens``
    (the live context-window size, NOT the turn-cumulative usage) is the overflow-ratio
    numerator.
    """
    if response.finish_reason == "length":
        frontend.on_status(
            "Response truncated at output token ceiling — use /compact to free context."
        )
    latest_input = response.usage.input_tokens or 0
    if latest_input <= 0:
        return
    ratio = latest_input / deps.model_max_context_tokens
    span.add_event(
        "ctx_overflow_check",
        {
            "ctx.input_tokens": latest_input,
            "ctx.max_context_tokens": deps.model_max_context_tokens,
            "ctx.ratio": ratio,
        },
    )
    if ratio >= 1.0:
        frontend.on_status(
            f"Context limit reached ({latest_input:,} / {deps.model_max_context_tokens:,} tokens)"
            " — prompt may have been truncated. Use /compact or /new."
        )
    elif ratio >= deps.config.compaction.compaction_ratio:
        # Only nudge when proactive compaction has given up (anti-thrash gate active);
        # below that threshold proactive fires on the next request, making it redundant.
        thrash_count = deps.runtime.consecutive_low_yield_proactive_compactions
        if thrash_count >= deps.config.compaction.proactive_thrash_window:
            frontend.on_status(
                f"Context {ratio:.0%} full ({latest_input:,} /"
                f" {deps.model_max_context_tokens:,} tokens)."
                " Auto-compaction paused — try /compact for one more pass or /new for a"
                " fresh session."
            )


def _continue_result(state: TurnState, turn_usage: RunUsage, output: Any) -> TurnResult:
    return TurnResult(
        outcome="continue",
        interrupted=False,
        messages=state.history,
        output=output,
        usage=turn_usage,
        model_requests=state.model_requests,
    )


def _terminal_result(
    state: TurnState, turn_usage: RunUsage, *, outcome: str, exit_reason: TurnExit
) -> TurnResult:
    state.exit_reason = exit_reason
    return TurnResult(
        outcome=outcome,  # type: ignore[arg-type]
        interrupted=False,
        messages=state.history,
        output=None,
        usage=turn_usage,
        model_requests=state.model_requests,
    )


def _interrupted_result(state: TurnState, turn_usage: RunUsage) -> TurnResult:
    """Build the interrupted TurnResult: append the abort marker, retain history.

    Unlike the graph (which drops the last unanswered ModelResponse), the owned path
    **retains** it — the every-step ``fill_unanswered_tool_calls`` net synthesizes the
    missing tool returns on the next turn (the deliberate drop→fill divergence, OQ-6). The
    abort marker (verbatim graph wording) tells the model the previous turn was interrupted.
    """
    state.exit_reason = TurnExit.INTERRUPTED
    abort_marker = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "The user interrupted the previous turn. Some actions "
                    "may be incomplete. Verify current state before continuing."
                )
            )
        ]
    )
    return TurnResult(
        outcome="continue",
        interrupted=True,
        messages=[*state.history, abort_marker],
        output=None,
        usage=turn_usage,
        model_requests=state.model_requests,
    )


# ---------------------------------------------------------------------------
# Owned subagent driver (OQ-4 option b) — structured final_result output.
# ---------------------------------------------------------------------------


def _build_subagent_toolset(spec: Any, deps: CoDeps) -> Any:
    """Build the subagent's tool surface (forked-deps task agent) and route dispatch to it.

    Two modes (``spec.surface_mode``) — the one intended construction divergence; the owned
    ``dispatch_tools`` / ``build_tool_defs`` read ``deps.toolset`` generically and treat both
    identically. FLAT_EXACT (default): a plain ``FunctionToolset`` from ``spec.tool_names``
    (all ``requires_approval=False``) — the closed-surface specialist. VISIBILITY_MODEL:
    assemble the orchestrator's own surface (native + the connected MCP toolsets on
    ``deps.mcp_toolsets``, visibility-filtered) minus a structural blocklist — the open
    general worker, which sees ALWAYS tools, can self-load DEFERRED ones via ``tool_view``,
    and carries each tool's real ``requires_approval``/``sequential`` flags. Either way the
    forked deps carry no ``deps.toolset`` until set here, and the surface is unwrapped — the
    ``tool`` span / cap / spill are applied by ``dispatch_tools``, not a wrapper toolset.
    """
    from co_cli.agent.spec import SurfaceModeEnum

    if spec.surface_mode is SurfaceModeEnum.VISIBILITY_MODEL:
        from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
        from co_cli.agent.delegation import _DELEGATE_AGENT_BLOCKLIST

        native_toolset, _ = build_native_toolset()
        return assemble_routing_toolset(
            native_toolset, deps.mcp_toolsets, name_blocklist=_DELEGATE_AGENT_BLOCKLIST
        )

    from pydantic_ai.toolsets import FunctionToolset

    from co_cli.tools.agent_tool import AGENT_TOOL_ATTR, TOOL_REGISTRY_BY_NAME

    toolset: FunctionToolset[CoDeps] = FunctionToolset()
    for name in spec.tool_names:
        fn = TOOL_REGISTRY_BY_NAME.get(name)
        if fn is None:
            raise ValueError(f"{spec.name}: unknown tool {name!r}")
        info = getattr(fn, AGENT_TOOL_ATTR)
        toolset.add_function(fn, requires_approval=False, sequential=not info.is_concurrent_safe)
    return toolset


def _subagent_instructions(spec: Any, deps: CoDeps) -> str:
    instructions = spec.instructions(deps)
    if spec.include_skill_manifest:
        from co_cli.skills.manifest import render_skill_manifest

        manifest = render_skill_manifest(deps.skill_catalog, deps.skills_dir, deps.user_skills_dir)
        if manifest:
            instructions = f"{manifest}\n\n{instructions}"
    return instructions


async def run_standalone_owned(
    spec: Any,
    deps: CoDeps,
    prompt: str,
    settings: ModelSettings | None = None,
    propagate_approvals: bool = False,
    frontend: Frontend | None = None,
) -> Any:
    """Run a task agent through the owned loop (OQ-4 option b — structured ``final_result``).

    Drives ``model_turn`` with the subagent's tool surface plus the ``final_result`` output
    tool (``allow_text_output=False``), dispatches the subagent's real tool calls (the work),
    and on a ``final_result`` call validates its args into the ``spec.output_type`` instance
    (re-prompting on validation failure, bounded by ``spec.default_budget``). Returns the
    validated model (``run_standalone`` discards it — daemons consume the tool side effects);
    the return lets the parity test assert the structured output.

    ``propagate_approvals`` is the delegated-vs-daemon discriminator (NOT ``frontend is
    None``): when True (the delegated-agent path), each step runs ``collect_inline_approvals``
    before dispatch so an approval-required delegated call surfaces on the parent's ``frontend``
    (marked delegated-origin). A delegated agent with ``frontend is None`` (headless parent)
    still runs the collector, which auto-denies — a write-capable agent never acts unprompted.
    When False (the ``run_standalone`` daemon default) no collector runs and dispatch is
    byte-for-byte identical to today; daemon surfaces carry no approval-required tools anyway.
    ``dispatch_tools`` keeps ``frontend=None`` in both branches — only the collector gets the
    real frontend, so the delegated agent's routine tool activity stays silent (D-3).
    """
    from pydantic import ValidationError as _ValidationError
    from pydantic_ai.messages import RetryPromptPart

    from co_cli.observability.tracing import pop_span, push_span

    if deps.model is None:
        raise ValueError(f"{spec.name}: run_standalone_owned requires deps.model to be set.")

    deps.toolset = _build_subagent_toolset(spec, deps)
    output_defs, processor = build_output_toolset(spec.output_type)
    output_name = output_defs[0].name

    if settings is None:
        settings = deps.model.settings_noreason
    request_limit = spec.default_budget
    stall_window = deps.config.llm.run_stall_timeout_secs
    cap_state = ToolCapState()
    turn_usage = RunUsage()

    push_span(
        f"invoke_agent {spec.name}",
        kind="agent",
        attributes={
            "co.agent.role": spec.name,
            "co.agent.model": getattr(deps.model.model, "model_name", str(deps.model.model)),
            "co.agent.request_limit": request_limit,
        },
    )
    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content=prompt)])]
    result_model: Any = None
    try:
        requests = 0
        while requests < request_limit:
            instructions = _subagent_instructions(spec, deps)
            instr_parts = (
                [InstructionPart(content=instructions, dynamic=False)] if instructions else []
            )
            function_defs = await build_tool_defs(deps)
            params = build_request_params(
                instruction_parts=instr_parts,
                function_tools=function_defs,
                output_tools=output_defs,
                allow_text_output=False,
            )
            request_messages = clean_message_history(history)
            response, step_usage = await _drive_model_request(
                deps, request_messages, params, settings, None, stall_window
            )
            requests += 1
            turn_usage.incr(step_usage)

            calls = _tool_calls(response)
            final = [c for c in calls if c.tool_name == output_name]
            if final:
                history = [*history, response]
                try:
                    result_model = processor.validate(final[0].args)
                    break
                except _ValidationError as exc:
                    history = [
                        *history,
                        ModelRequest(
                            parts=[
                                RetryPromptPart(
                                    content=str(exc),
                                    tool_name=output_name,
                                    tool_call_id=final[0].tool_call_id,
                                )
                            ]
                        ),
                    ]
                    continue
            if not calls:
                history = [
                    *history,
                    response,
                    ModelRequest(
                        parts=[
                            UserPromptPart(
                                content="Call the final_result tool with your structured result."
                            )
                        ]
                    ),
                ]
                continue
            history = [*history, response]
            if propagate_approvals:
                resolution = await collect_inline_approvals(
                    calls, deps, frontend, origin_label="delegated subtask"
                )
                parts = await dispatch_tools(
                    calls,
                    deps,
                    cap_state=cap_state,
                    frontend=None,
                    denials=resolution.denials,
                    approved_ids=resolution.approved_ids,
                )
            else:
                parts = await dispatch_tools(calls, deps, cap_state=cap_state, frontend=None)
            history = [*history, ModelRequest(parts=parts)]
            if cap_state.hard_stop:
                break
    except BaseException as exc:
        pop_span(status="ERROR", status_msg=str(exc))
        raise
    if turn_usage.input_tokens or turn_usage.output_tokens:
        record_usage(deps, turn_usage)
    pop_span(attributes={"co.agent.final_result": str(result_model)})
    return result_model
