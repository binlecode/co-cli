"""Orchestration state machine — turn handling, streaming, and approval flow.

Contains TurnResult, run_turn(), and supporting private functions.
Frontend lives in co_cli/display/_core.py. Stream rendering policy
lives in co_cli/display/_stream_renderer.py. Tool display metadata lives in
co_cli/tools/display.py. The chat loop in main.py delegates all LLM
interaction here.
"""

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolApproved,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

# Local alias — DeferredToolResults carries approval decisions (allow/deny),
# not executed tool output. Actual tool output returns later as ToolReturnPart.
ToolApprovalDecisions = DeferredToolResults
from opentelemetry import trace as otel_trace
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

# Typed return value from run_turn() to chat loop
TurnOutcome = Literal["continue", "error"]

_TRACER = otel_trace.get_tracer("co-cli.orchestrate")
logger = logging.getLogger(__name__)

# Per-segment hang-prevention timeout. Applied to each individual agent.run_stream_events()
# call inside _execute_stream_segment(). Not a behavioral spec — a safety net that prevents
# an unresponsive LLM from hanging a turn indefinitely.
_LLM_SEGMENT_HANG_TIMEOUT_SECS: int = 60

from co_cli.config._core import REASONING_DISPLAY_SUMMARY
from co_cli.context._http_error_classifier import is_context_overflow
from co_cli.context.compaction import maybe_run_pre_turn_hygiene
from co_cli.context.summarization import latest_response_input_tokens
from co_cli.deps import CoDeps
from co_cli.display._core import Frontend, QuestionPrompt
from co_cli.display._stream_renderer import StreamRenderer
from co_cli.tools.approvals import (
    decode_tool_args,
    is_auto_approved,
    record_approval_choice,
    resolve_approval_subject,
)
from co_cli.tools.display import format_for_display, get_tool_start_args_display

type SessionAgent = Agent[CoDeps, str | DeferredToolRequests]
type SessionRunResult = AgentRunResult[str | DeferredToolRequests]

# ---------------------------------------------------------------------------
# TurnResult — returned by run_turn()
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    outcome: TurnOutcome
    interrupted: bool
    messages: list[ModelMessage] = field(default_factory=list)
    # output: pydantic-ai AgentRunResult.output — str | DeferredToolRequests in practice.
    # Kept Any: callers pattern-match on interrupted/outcome, not on output type directly.
    output: Any = None
    # usage: pydantic-ai RunUsage object. Kept Any: callers never inspect fields directly;
    # usage is forwarded opaquely to _merge_turn_usage and span attributes.
    usage: Any = None
    streamed_text: bool = False


# ---------------------------------------------------------------------------
# _TurnState — explicit turn-scoped mutable state for run_turn()
# ---------------------------------------------------------------------------


@dataclass
class _TurnState:
    """Holds all mutable state for one orchestrated turn.

    Collects fields that would otherwise be parallel locals in run_turn(),
    making invariants explicit and mutation paths auditable.

    Phase ownership:
      pre-turn (run_turn init):
        current_input         — user text or None for approval-resume segments
        current_history       — REPL-owned message list at turn entry
        tool_reformat_budget  — app-level budget for 400 tool-call reformulation (not HTTP retry)
      in-turn (per segment, updated by _execute_stream_segment / _run_approval_loop):
        latest_result         — AgentRunResult from the most recent segment
        latest_streamed_text  — whether the last segment streamed visible text
        latest_usage          — usage from the most recent segment (payload for TurnResult)
        tool_approval_decisions — deferred approvals to pass to the next segment resume
      cross-turn (accumulates during the turn):
        outcome               — set on error exit; read by run_turn() return and span
        interrupted           — set on CancelledError; drives _build_interrupted_turn_result
    """

    # pre-turn
    current_input: str | None
    current_history: list[ModelMessage]
    # Tool-call reformulation budget (HTTP 400 only — app logic, not transport retry).
    # Independent of SDK transport retries (429/5xx handled by OpenAI SDK).
    tool_reformat_budget: int = 2
    # Overflow recovery: one-shot flag — emergency compact attempted at most once per turn.
    overflow_recovery_attempted: bool = False
    # in-turn (updated after each segment)
    latest_result: SessionRunResult | None = None
    latest_streamed_text: bool = False
    # latest_usage: pydantic-ai RunUsage object. Kept Any: forwarded opaquely to
    # _merge_turn_usage and span attributes; callers never inspect fields directly.
    latest_usage: Any = None
    tool_approval_decisions: ToolApprovalDecisions | None = None
    # cross-turn outcome flags
    outcome: TurnOutcome = "continue"
    interrupted: bool = False


# ---------------------------------------------------------------------------
# _merge_turn_usage — accumulate segment usage into the authoritative per-turn total
# ---------------------------------------------------------------------------


def _merge_turn_usage(
    deps: CoDeps,
    # usage: pydantic-ai RunUsage object. Kept Any: forwarded opaquely; never inspected here.
    usage: Any | None,
) -> None:
    """Merge one segment's usage into deps.runtime.turn_usage (the authoritative accumulator).

    Called after every _execute_stream_segment() completes. Sub-agent tools call
    their own variant; this one is owned by the foreground orchestrator.
    """
    if usage is None:
        return
    # Deepcopy on first assignment: pydantic-ai mutates the passed-in usage object
    # (including its `details` dict) in place across segments, so aliasing turn_usage
    # to latest_usage would make later incr() calls self-referential and double-count.
    if deps.runtime.turn_usage is None:
        deps.runtime.turn_usage = deepcopy(usage)
    else:
        deps.runtime.turn_usage.incr(usage)


# ---------------------------------------------------------------------------
# _collect_deferred_tool_approvals — approval collection without resumption
# ---------------------------------------------------------------------------


async def _collect_deferred_tool_approvals(
    result: SessionRunResult,
    deps: CoDeps,
    frontend: Frontend | None,
) -> DeferredToolResults:
    """Collect approval decisions for all pending deferred tool requests.

    For each pending call:
      - auto-approved or user approves → approvals.approvals[id] = True
      - user denies                    → approvals.approvals[id] = ToolDenied(...)

    Returns a DeferredToolResults (ToolApprovalDecisions) object consumed by the
    next _execute_stream_segment() call as deferred_tool_results=.

    Important: this payload carries approval decisions only. Actual tool execution
    and ToolReturnPart output happen after the resumed segment completes.
    """
    output = result.output
    if not isinstance(output, DeferredToolRequests):
        raise RuntimeError(
            "_collect_deferred_tool_approvals called without DeferredToolRequests output"
        )

    approvals = DeferredToolResults()

    for call in output.approvals:
        meta = output.metadata.get(call.tool_call_id, {})

        # Clarify path — "question" key is present only on QuestionRequired metadata
        if "question" in meta:
            q_prompt = QuestionPrompt(
                question=meta.get("question", ""),
                options=meta.get("options"),
            )
            answer = frontend.prompt_question(q_prompt) if frontend is not None else ""
            approvals.approvals[call.tool_call_id] = ToolApproved(
                override_args={"user_answer": answer}
            )
            continue

        # Standard approval path
        args = decode_tool_args(call.args)
        subject = resolve_approval_subject(call.tool_name, args)

        # Auto-approval — skip prompt if subject already approved this session
        if is_auto_approved(subject, deps):
            approvals.approvals[call.tool_call_id] = True
            continue

        # User prompt
        choice = frontend.prompt_approval(subject) if frontend is not None else "n"

        approved = choice in ("y", "a")
        record_approval_choice(
            approvals,
            tool_call_id=call.tool_call_id,
            approved=approved,
            subject=subject,
            deps=deps,
            remember=choice == "a" and subject.can_remember,
        )
        if not approved:
            logger.debug(
                "tool_denied tool_name=%s subject_kind=%s subject_value=%s",
                call.tool_name,
                subject.kind,
                subject.value,
            )

    return approvals


# ---------------------------------------------------------------------------
# _execute_stream_segment — run one segment and update _TurnState in-place
# ---------------------------------------------------------------------------


def _handle_tool_call_event(
    event: FunctionToolCallEvent,
    renderer: StreamRenderer,
    deps: CoDeps,
    frontend: Frontend,
) -> None:
    """Handle a FunctionToolCallEvent: flush renderer, start tool UI, install progress."""
    renderer.flush_for_tool_output()
    tool_id = event.tool_call_id
    name = event.part.tool_name
    # In summary mode annotations are suppressed — tool result panel is sufficient feedback
    if deps.session.reasoning_display != REASONING_DISPLAY_SUMMARY:
        frontend.on_tool_start(tool_id, name, get_tool_start_args_display(name, event.part))
    renderer.install_progress(deps, tool_id)


def _handle_stream_event(
    event: object,
    renderer: StreamRenderer,
    deps: CoDeps,
    frontend: Frontend,
) -> SessionRunResult | None:
    """Process one stream event. Returns AgentRunResult when found, else None."""
    if isinstance(event, PartStartEvent):
        if isinstance(event.part, ThinkingPart):
            renderer.append_thinking(event.part.content)
        elif isinstance(event.part, TextPart):
            renderer.append_text(event.part.content)
        return None

    if isinstance(event, PartDeltaEvent):
        if isinstance(event.delta, ThinkingPartDelta):
            renderer.append_thinking(event.delta.content_delta or "")
        elif isinstance(event.delta, TextPartDelta):
            renderer.append_text(event.delta.content_delta)
        return None

    # Readiness/meta events are intentionally side-effect free for
    # rendering; text may continue after FinalResultEvent.
    if isinstance(event, (FinalResultEvent, PartEndEvent)):
        return None

    if isinstance(event, FunctionToolCallEvent):
        _handle_tool_call_event(event, renderer, deps, frontend)
        return None

    if isinstance(event, FunctionToolResultEvent):
        renderer.flush_for_tool_output()
        renderer.clear_progress(deps)
        tool_id = event.tool_call_id
        if not isinstance(event.result, ToolReturnPart):
            # RetryPromptPart: tool raised ModelRetry or validation failed.
            # Close the tool surface cleanly — no result to display.
            frontend.on_tool_complete(tool_id, None)
            return None
        frontend.on_tool_complete(tool_id, format_for_display(event.result.content))
        return None

    if isinstance(event, AgentRunResultEvent):
        return event.result

    return None


async def _execute_stream_segment(
    turn_state: _TurnState,
    agent: SessionAgent,
    deps: CoDeps,
    model_settings: ModelSettings | None,
    frontend: Frontend,
    message_history: list[ModelMessage] | None = None,
) -> None:
    """Run one stream segment and update turn state in-place.

    Uses ``message_history`` when provided (preflight-extended history from
    _run_model_preflight); falls back to turn_state.current_history for
    approval-resume segments. Reads turn_state.current_input,
    tool_approval_decisions, and latest_usage. After the call:
    - latest_result holds the AgentRunResult
    - latest_streamed_text reflects whether text was streamed
    - latest_usage is updated from the result
    - tool_approval_decisions is cleared (consumed)

    Stream rendering policy (buffering, flush, thinking gating) is owned by
    StreamRenderer. Tool display metadata is owned by tool_display.
    """
    result: SessionRunResult | None = None
    renderer = StreamRenderer(frontend, reasoning_display=deps.session.reasoning_display)

    try:
        async with asyncio.timeout(_LLM_SEGMENT_HANG_TIMEOUT_SECS):
            async for event in agent.run_stream_events(
                turn_state.current_input,
                deps=deps,
                message_history=message_history
                if message_history is not None
                else turn_state.current_history,
                model_settings=model_settings,
                usage=turn_state.latest_usage,
                usage_limits=UsageLimits(request_limit=None),
                deferred_tool_results=turn_state.tool_approval_decisions,
                metadata={"session_id": deps.session.session_path.stem[-8:]},
            ):
                event_result = _handle_stream_event(event, renderer, deps, frontend)
                if event_result is not None:
                    result = event_result

            # Normal completion — commit remaining buffers
            renderer.finish()
    finally:
        frontend.cleanup()

    if result is None:
        raise RuntimeError(
            "_execute_stream_segment: stream ended without AgentRunResultEvent — segment contract violated"
        )
    turn_state.latest_result = result
    turn_state.latest_streamed_text = renderer.streamed_text
    turn_state.latest_usage = result.usage()
    turn_state.tool_approval_decisions = None
    _merge_turn_usage(deps, turn_state.latest_usage)


async def _run_approval_loop(
    turn_state: _TurnState,
    agent: SessionAgent,
    deps: CoDeps,
    model_settings: ModelSettings | None,
    frontend: Frontend,
) -> None:
    """Run approval-resume segments until no deferred tool requests remain.

    Each iteration: collect approvals → prepare resume → execute segment.
    Resume segments run on the main agent directly — the SDK skips
    ModelRequestNode entirely on the deferred_tool_results path, so zero
    tokens are sent to the model regardless of which agent runs.
    Exits when latest_result.output is no longer DeferredToolRequests.
    """
    assert turn_state.latest_result is not None

    while True:
        latest_result = turn_state.latest_result
        output = latest_result.output
        if not isinstance(output, DeferredToolRequests):
            break
        deps.runtime.resume_tool_names = frozenset(call.tool_name for call in output.approvals)
        approvals = await _collect_deferred_tool_approvals(latest_result, deps, frontend)
        turn_state.current_input = None
        turn_state.current_history = latest_result.all_messages()
        turn_state.tool_approval_decisions = approvals
        await _execute_stream_segment(turn_state, agent, deps, model_settings, frontend)
    deps.runtime.resume_tool_names = None


def _build_error_turn_result(turn_state: _TurnState) -> TurnResult:
    msgs = (
        turn_state.latest_result.all_messages()
        if turn_state.latest_result
        else turn_state.current_history
    )
    return TurnResult(
        messages=msgs,
        output=None,
        usage=turn_state.latest_usage,
        interrupted=False,
        streamed_text=turn_state.latest_streamed_text,
        outcome="error",
    )


def _build_interrupted_turn_result(turn_state: _TurnState) -> TurnResult:
    """Truncate to last clean ModelResponse, append abort marker, return interrupted TurnResult.

    Drops the last ModelResponse if it contains any unanswered ToolCallPart entries,
    so history ends at a clean point before the interrupted tool call sequence.
    The abort marker carries sufficient context for the next turn.
    """
    msgs = (
        turn_state.latest_result.all_messages()
        if turn_state.latest_result
        else turn_state.current_history
    )
    # Drop last ModelResponse if it has unanswered ToolCallParts
    if (
        msgs
        and isinstance(msgs[-1], ModelResponse)
        and any(isinstance(p, ToolCallPart) for p in msgs[-1].parts)
    ):
        msgs = msgs[:-1]
    # Abort marker — model sees this on the next turn so it knows
    # the previous turn was interrupted and can verify state.
    abort_marker = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "The user interrupted the previous turn. Some actions "
                    "may be incomplete. Verify current state before continuing."
                ),
            )
        ]
    )
    return TurnResult(
        messages=[*msgs, abort_marker],
        output=None,
        usage=turn_state.latest_usage,
        interrupted=True,
        streamed_text=turn_state.latest_streamed_text,
        outcome="continue",
    )


# ---------------------------------------------------------------------------
# _check_output_limits — finish-reason and context overflow diagnostics
# ---------------------------------------------------------------------------


def _check_output_limits(
    turn_state: _TurnState,
    deps: CoDeps,
    frontend: Frontend,
) -> None:
    """Emit finish-reason and context-overflow status warnings after a completed turn.

    Precondition: turn_state.latest_result is non-None (called only on success path).
    """
    latest_result = turn_state.latest_result
    assert latest_result is not None

    if latest_result.response.finish_reason == "length":
        frontend.on_status(
            "Response may be truncated (hit output token limit). Use /continue to extend."
        )
    if deps.runtime.turn_usage is not None and deps.config.llm.supports_context_ratio_tracking():
        effective_ctx = deps.config.llm.effective_num_ctx()
        ratio = deps.runtime.turn_usage.input_tokens / effective_ctx
        with _TRACER.start_as_current_span("ctx_overflow_check") as ctx_span:
            ctx_span.set_attribute("ctx.input_tokens", deps.runtime.turn_usage.input_tokens)
            ctx_span.set_attribute("ctx.num_ctx", effective_ctx)
            ctx_span.set_attribute("ctx.ratio", ratio)
            if ratio >= deps.config.llm.ctx_overflow_threshold:
                frontend.on_status(
                    f"Context limit reached ({deps.runtime.turn_usage.input_tokens:,} / {effective_ctx:,} tokens)"
                    " — Ollama likely truncated the prompt. Use /compact or /new."
                )
            elif ratio >= deps.config.llm.ctx_warn_threshold:
                frontend.on_status(
                    f"Context {ratio:.0%} full ({deps.runtime.turn_usage.input_tokens:,} / {effective_ctx:,} tokens)."
                    " Consider /compact to free space."
                )


def _history_with_pending_user_input(turn_state: _TurnState) -> list[ModelMessage]:
    """Materialize the in-flight user prompt into history for retryable recovery paths."""
    if turn_state.current_input is None:
        return list(turn_state.current_history)
    return [
        *turn_state.current_history,
        ModelRequest(parts=[UserPromptPart(content=turn_state.current_input)]),
    ]


async def _attempt_overflow_recovery(
    recovery_ctx: RunContext[CoDeps],
    recovery_history: list[ModelMessage],
) -> tuple[list[ModelMessage] | None, str]:
    """Normal planner-based compaction, else structural emergency fallback.

    Returns ``(compacted, status_msg)``. ``compacted is None`` signals terminal
    overflow — happens only when ``len(groups) <= 2`` (first-turn structural limit).
    """
    from co_cli.context.compaction import (
        emergency_recover_overflow_history,
        recover_overflow_history,
    )

    compacted = await recover_overflow_history(recovery_ctx, recovery_history)
    if compacted is not None:
        return compacted, "Context overflow — compacting and retrying..."

    compacted = await emergency_recover_overflow_history(recovery_ctx, recovery_history)
    if compacted is not None:
        return compacted, "Context overflow — emergency compaction (first + last turn only)."

    return None, ""


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# run_turn — the main orchestration entry point
# ---------------------------------------------------------------------------


async def run_turn(
    *,
    agent: SessionAgent,
    user_input: str,
    deps: CoDeps,
    message_history: list[ModelMessage],
    model_settings: ModelSettings | None = None,
    frontend: Frontend,
) -> TurnResult:
    """Execute one LLM turn: streaming, approval chaining, error handling.

    Single public entrypoint for an orchestrated agent turn. Emits the root
    `co.turn` span, displays the thinking status, runs the inner retry loop
    for HTTP errors and the approval loop for deferred tool requests, and
    reads turn policy (retries) from deps.config.

    Returns TurnResult with outcome field for chat loop pattern-matching:
      "continue" — normal completion, prompt for next input
      "error"    — unrecoverable error, display and prompt
    """
    deps.runtime.reset_for_turn()
    message_history = await maybe_run_pre_turn_hygiene(
        deps,
        message_history,
        reported_input_tokens=latest_response_input_tokens(message_history),
    )
    # Status before span — matches prior wrapper ordering
    frontend.on_status("Co is thinking...")
    turn_state = _TurnState(
        current_input=user_input,
        current_history=message_history,
    )

    with _TRACER.start_as_current_span("co.turn") as span:
        try:
            while True:
                try:
                    await _execute_stream_segment(
                        turn_state,
                        agent,
                        deps,
                        model_settings,
                        frontend,
                        message_history=turn_state.current_history,
                    )

                    await _run_approval_loop(turn_state, agent, deps, model_settings, frontend)
                    latest_result = turn_state.latest_result
                    assert latest_result is not None
                    turn_state.current_history = latest_result.all_messages()
                    if not turn_state.latest_streamed_text and isinstance(
                        latest_result.output, str
                    ):
                        frontend.on_final_output(latest_result.output)

                    _check_output_limits(turn_state, deps, frontend)

                    return TurnResult(
                        messages=turn_state.current_history,
                        output=latest_result.output,
                        usage=turn_state.latest_usage,
                        interrupted=False,
                        streamed_text=turn_state.latest_streamed_text,
                        outcome="continue",
                    )

                except ModelHTTPError as e:
                    code = e.status_code
                    # Context overflow — must resolve completely (compact+retry OR terminal).
                    # Design invariant: NEVER falls through to the 400 reformulation handler.
                    if is_context_overflow(e):
                        if not turn_state.overflow_recovery_attempted:
                            turn_state.overflow_recovery_attempted = True
                            recovery_ctx = RunContext(
                                deps=deps,
                                model=agent.model,
                                usage=turn_state.latest_usage or RunUsage(),
                            )
                            recovery_history = _history_with_pending_user_input(turn_state)
                            compacted, status_msg = await _attempt_overflow_recovery(
                                recovery_ctx,
                                recovery_history,
                            )
                            if compacted is not None:
                                # Overflow recovery bypasses the proactive gate — reset savings ring.
                                deps.runtime.consecutive_low_yield_proactive_compactions = 0
                                turn_state.current_history = compacted
                                turn_state.current_input = None
                                frontend.on_status(status_msg)
                                continue
                        # Terminal: either second overflow after retry, or both attempts returned None.
                        frontend.on_status("Context overflow — unrecoverable.")
                        turn_state.outcome = "error"
                        return _build_error_turn_result(turn_state)
                    # HTTP 400: malformed tool call — reflect error to model for reformulation.
                    # This is app logic (not transport retry); budget is independent of SDK retries.
                    if code == 400 and turn_state.tool_reformat_budget > 0:
                        turn_state.tool_reformat_budget -= 1
                        frontend.on_status("Tool call rejected (HTTP 400), reflecting to model...")
                        await asyncio.sleep(0.5)
                        turn_state.current_history = [
                            *turn_state.current_history,
                            ModelRequest(
                                parts=[
                                    UserPromptPart(
                                        content=(
                                            "Your previous tool call was rejected by the "
                                            f"model provider: {e.body}. Please reformulate "
                                            "your tool call with valid JSON arguments."
                                        ),
                                    )
                                ]
                            ),
                        ]
                        turn_state.current_input = None
                        continue
                    # All other HTTP errors (429/5xx already retried by SDK, terminal errors)
                    frontend.on_status(f"Provider error (HTTP {code}): {e.body}")
                    turn_state.outcome = "error"
                    span.add_event(
                        "provider_error",
                        {
                            "http.status_code": code,
                            "error.body": str(e.body)[:500],
                        },
                    )
                    return _build_error_turn_result(turn_state)

                except ModelAPIError as e:
                    # Network errors already retried by SDK — terminal after SDK exhaustion
                    frontend.on_status(f"Network error: {e}")
                    turn_state.outcome = "error"
                    return _build_error_turn_result(turn_state)

                except TimeoutError:
                    frontend.on_status(
                        "LLM segment timed out — model did not respond. Try a shorter prompt, or check model health with `co config`."
                    )
                    turn_state.outcome = "error"
                    return _build_error_turn_result(turn_state)

                except UnexpectedModelBehavior as e:
                    frontend.on_status(f"Model returned malformed output: {e}")
                    turn_state.outcome = "error"
                    return _build_error_turn_result(turn_state)

                except (KeyboardInterrupt, asyncio.CancelledError):
                    turn_state.interrupted = True
                    return _build_interrupted_turn_result(turn_state)

        finally:
            span.set_attribute("turn.outcome", turn_state.outcome)
            span.set_attribute("turn.interrupted", turn_state.interrupted)
            span.set_attribute(
                "turn.input_tokens",
                turn_state.latest_usage.input_tokens if turn_state.latest_usage else 0,
            )
            span.set_attribute(
                "turn.output_tokens",
                turn_state.latest_usage.output_tokens if turn_state.latest_usage else 0,
            )
            deps.runtime.tool_progress_callback = (
                None  # belt-and-suspenders; also cleared by reset_for_turn() at next turn entry
            )
