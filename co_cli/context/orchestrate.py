"""Orchestration state machine — turn handling, streaming, and approval flow.

Contains TurnResult, run_turn(), and supporting private functions.
Frontend lives in co_cli/display/_core.py. Stream rendering policy
lives in co_cli/display/_stream_renderer.py. Tool display metadata lives in
co_cli/tools/display.py. The chat loop in main.py delegates all LLM
interaction here.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
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

logger = logging.getLogger(__name__)

_LLM_SEGMENT_WARN_SECS: int = 90
"""Slow-segment warning threshold."""

_LENGTH_RETRY_CEILING = 16_384
"""Max output tokens ceiling for length-continuation auto-retry. Doublings from 4096 → 8192 → 16384."""

_LENGTH_RETRY_BOOST = 2
"""Multiplier applied to max_tokens on each length-continuation retry."""

assert _LENGTH_RETRY_BOOST > 1, "boost must strictly increase max_tokens for retry to terminate"

from co_cli.config.core import REASONING_DISPLAY_SUMMARY
from co_cli.config.llm import cap_output_tokens
from co_cli.context._timeouts import LLM_SEGMENT_TIMEOUT_SECS
from co_cli.context.compaction import is_context_overflow, recover_overflow_history
from co_cli.deps import CoDeps
from co_cli.display.core import Frontend, QuestionPrompt
from co_cli.display.stream_renderer import StreamRenderer
from co_cli.observability.tracing import current_span, pop_span, push_span, trace
from co_cli.session.usage import record_usage
from co_cli.tools.approvals import (
    decode_tool_args,
    is_auto_approved,
    record_approval_choice,
    resolve_approval_subject,
)
from co_cli.tools.display import format_for_display, get_tool_start_args_display
from co_cli.tools.tool_call_limit import (
    MAX_TOOL_CALLS_PER_MODEL_REQUEST,
    TOOL_CAP_HARD_STOP_CONSECUTIVE,
)

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
    # usage is forwarded opaquely to span attributes.
    usage: Any = None
    streamed_text: bool = False
    # Count of ModelResponses across all segments in this turn.
    # Sourced from the per-segment accumulator on _TurnState; consumed by the
    # post-turn skill-review hook to gate background firing.
    model_requests: int = 0


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
    # span attributes; callers never inspect fields directly.
    latest_usage: Any = None
    tool_approval_decisions: ToolApprovalDecisions | None = None
    # cross-turn outcome flags
    outcome: TurnOutcome = "continue"
    interrupted: bool = False
    # Accumulator across all segments in this turn — counts every ModelResponse,
    # regardless of whether it contains tool calls. Compaction (which replaces
    # current_history) does not reset this; the accumulator is segment-level state.
    model_requests: int = 0
    # Set by _run_approval_loop when consecutive_tool_cap_violations crosses the threshold.
    # Read by run_turn to drive the hard-stop exit.
    tool_cap_hard_stop: bool = False


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

        # Clarify path — "questions" key is present only on QuestionRequired metadata
        if "questions" in meta:
            answers: list[str] = []
            for q in meta["questions"]:
                raw_opts = q.get("options")
                # options may be list[{label, description}] or list[str] depending on model output
                labels = (
                    [o["label"] if isinstance(o, dict) else o for o in raw_opts]
                    if raw_opts
                    else None
                )
                # model may use "label", "text", or "message" instead of "question"
                q_text = (
                    q.get("question") or q.get("label") or q.get("text") or q.get("message", "")
                )
                q_prompt = QuestionPrompt(
                    question=q_text,
                    options=labels,
                    multiple=q.get("multiple", False),
                )
                answer = (await frontend.prompt_question(q_prompt)) if frontend is not None else ""
                answers.append(answer)
            # Stash answers in runtime keyed by tool_call_id and approve with no
            # override_args. override_args REPLACES the whole args dict, which would
            # drop the required `questions` field and fail resume validation; a bare
            # approval preserves the original args so the clarify tool re-runs approved
            # and reads its answers from deps.runtime.clarify_answers.
            deps.runtime.clarify_answers[call.tool_call_id] = answers
            approvals.approvals[call.tool_call_id] = ToolApproved()
            continue

        # Standard approval path
        args = decode_tool_args(call.args)
        subject = resolve_approval_subject(
            call.tool_name, args, deps.tool_index.get(call.tool_name)
        )

        # Auto-approval — skip prompt if subject already approved this session
        if is_auto_approved(subject, deps):
            approvals.approvals[call.tool_call_id] = True
            continue

        # User prompt
        choice = (await frontend.prompt_approval(subject)) if frontend is not None else "n"

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
    deps.runtime.tool_progress_callback = lambda msg, _tid=tool_id: frontend.on_tool_progress(
        _tid, msg
    )


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
        deps.runtime.tool_progress_callback = None
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
    _t0 = time.monotonic()
    agent_name = getattr(agent, "name", None) or "<unknown>"
    push_span(
        f"invoke_agent {agent_name}",
        kind="agent",
        attributes={
            "co.agent.role": "orchestrator",
            "co.agent.model": getattr(deps.model.model, "model_name", str(deps.model.model))
            if deps.model
            else None,
            "co.agent.request_limit": None,
        },
    )
    try:
        try:
            async with asyncio.timeout(LLM_SEGMENT_TIMEOUT_SECS):
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
                    metadata={
                        "session_id": deps.session.session_path.stem[-8:],
                        "role": "orchestrator",
                        "request_limit": None,
                    },
                ):
                    event_result = _handle_stream_event(event, renderer, deps, frontend)
                    if event_result is not None:
                        result = event_result
                renderer.finish()
        finally:
            frontend.cleanup()
    except BaseException as exc:
        pop_span(status="ERROR", status_msg=str(exc))
        raise
    elapsed = time.monotonic() - _t0
    logger.debug("LLM segment elapsed: %.1fs", elapsed)
    if elapsed >= _LLM_SEGMENT_WARN_SECS:
        logger.warning(
            "LLM segment slow: %.1fs (warn threshold %ds)", elapsed, _LLM_SEGMENT_WARN_SECS
        )

    if result is None:
        pop_span(status="ERROR", status_msg="segment ended without AgentRunResultEvent")
        raise RuntimeError(
            "_execute_stream_segment: stream ended without AgentRunResultEvent — segment contract violated"
        )
    turn_state.latest_result = result
    turn_state.model_requests += sum(
        1 for m in result.new_messages() if isinstance(m, ModelResponse)
    )
    turn_state.latest_streamed_text = renderer.streamed_text
    turn_state.latest_usage = result.usage()
    turn_state.tool_approval_decisions = None

    try:
        requests_used = getattr(result.usage(), "requests", None)
    except (AttributeError, TypeError):
        requests_used = None
    pop_span(
        attributes={
            "co.agent.requests_used": requests_used,
            "co.agent.final_result": str(result.output),
        },
    )

    # Segment-boundary cap finalize (idempotent with the in-wrapper transition reset):
    # if the last model request of this segment stayed within the cap, clear the streak
    # so the orchestrate.py hard-stop check sees only genuinely-consecutive violations.
    if deps.runtime.tool_calls_in_model_request <= MAX_TOOL_CALLS_PER_MODEL_REQUEST:
        deps.runtime.consecutive_tool_cap_violations = 0


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
        if deps.runtime.consecutive_tool_cap_violations >= TOOL_CAP_HARD_STOP_CONSECUTIVE:
            turn_state.tool_cap_hard_stop = True
            break
    deps.runtime.resume_tool_names = None


def _check_turn_caps(
    turn_state: _TurnState,
    deps: CoDeps,
    frontend: Frontend,
) -> TurnResult | None:
    """Return an error TurnResult if the hard-stop or model-request cap fired, else None."""
    if turn_state.tool_cap_hard_stop:
        frontend.on_status(
            f"Tool-call cap exceeded {TOOL_CAP_HARD_STOP_CONSECUTIVE} consecutive"
            " model requests — stopping."
        )
        turn_state.outcome = "error"
        return _build_error_turn_result(turn_state)
    cap = deps.config.llm.max_model_requests_per_turn
    if cap > 0 and turn_state.model_requests >= cap:
        frontend.on_status(f"Model-request cap reached ({cap} LLM calls this turn) — stopping.")
        turn_state.outcome = "error"
        return _build_error_turn_result(turn_state)
    return None


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
        model_requests=turn_state.model_requests,
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
        model_requests=turn_state.model_requests,
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
            "Response truncated at output token ceiling — use /compact to free context."
        )
    latest_input = latest_result.response.usage.input_tokens or 0
    if latest_input > 0:
        ratio = latest_input / deps.model_max_ctx
        current_span().add_event(
            "ctx_overflow_check",
            {
                "ctx.input_tokens": latest_input,
                "ctx.max_ctx": deps.model_max_ctx,
                "ctx.ratio": ratio,
            },
        )
        if ratio >= 1.0:
            frontend.on_status(
                f"Context limit reached ({latest_input:,} / {deps.model_max_ctx:,} tokens)"
                " — prompt may have been truncated. Use /compact or /new."
            )
        elif ratio >= deps.config.compaction.compaction_ratio:
            # Only nudge when proactive compaction has given up (anti-thrash gate active).
            # Below that threshold proactive will fire on the next request automatically,
            # making a manual nudge redundant.
            thrash_count = deps.runtime.consecutive_low_yield_proactive_compactions
            if thrash_count >= deps.config.compaction.proactive_thrash_window:
                frontend.on_status(
                    f"Context {ratio:.0%} full ({latest_input:,} / {deps.model_max_ctx:,} tokens)."
                    " Auto-compaction paused — try /compact for one more pass or /new for a fresh session."
                )


def _emit_final_output_if_needed(
    turn_state: _TurnState,
    latest_result: SessionRunResult,
    frontend: Frontend,
) -> None:
    """Emit final output text when the stream did not already render it inline."""
    if not turn_state.latest_streamed_text and isinstance(latest_result.output, str):
        frontend.on_final_output(latest_result.output)


def _length_retry_settings(
    result: SessionRunResult,
    active_settings: ModelSettings | None,
) -> ModelSettings | None:
    """Return boosted ModelSettings if a length-continuation retry should fire, else None.

    Fires when:
      - finish_reason is 'length' (output was truncated)
      - active_settings has a max_tokens value below the ceiling (boost is possible)
      - the response contains at least one TextPart (text-presence gate)

    Text-only gate: a truncated ToolCallPart would carry malformed JSON args into
    retry history, producing an assistant message with an unanswered tool_calls
    entry — the OpenAI/Ollama protocol rejects that. Tool-call truncations fall
    through to _check_output_limits' ceiling status instead of retrying.

    Returns a new settings dict with max_tokens doubled (capped at _LENGTH_RETRY_CEILING),
    or None when the conditions are not met (no retry).
    """
    if result.response.finish_reason != "length":
        return None
    current_max = active_settings.get("max_tokens", 0) if active_settings else 0
    if not current_max or current_max >= _LENGTH_RETRY_CEILING:
        return None
    if not any(isinstance(p, TextPart) for p in result.response.parts):
        return None
    boosted = min(current_max * _LENGTH_RETRY_BOOST, _LENGTH_RETRY_CEILING)
    # cap_output_tokens applies the Ollama lockstep (scalar + extra_body["max_tokens"]
    # mirror) so the boosted output budget actually reaches Ollama, which honors
    # max_tokens only at the request root via extra_body, not OpenAI's max_completion_tokens.
    return cap_output_tokens(active_settings, boosted)


def _history_with_pending_user_input(turn_state: _TurnState) -> list[ModelMessage]:
    """Materialize the in-flight user prompt into history for retryable recovery paths."""
    if turn_state.current_input is None:
        return turn_state.current_history
    return [
        *turn_state.current_history,
        ModelRequest(parts=[UserPromptPart(content=turn_state.current_input)]),
    ]


def _transient_error_message(e: Exception) -> str:
    if isinstance(e, TimeoutError):
        return (
            "LLM call timed out — model did not respond in time."
            " Try a shorter prompt, or ask Co 'what can you do right now?' or run /doctor."
        )
    return f"Network error: {e}"


def _apply_400_reformulation(
    turn_state: _TurnState,
    error: ModelHTTPError,
) -> bool:
    """Append a reformulation reflection to turn_state.current_history and decrement budget.

    Returns True if budget remained and the retry should proceed; False if budget exhausted.
    """
    if turn_state.tool_reformat_budget <= 0:
        return False
    turn_state.tool_reformat_budget -= 1
    turn_state.current_history = [
        *turn_state.current_history,
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "Your previous tool call was rejected by the "
                        f"model provider: {error.body}. Please reformulate "
                        "your tool call with valid JSON arguments."
                    ),
                )
            ]
        ),
    ]
    turn_state.current_input = None
    return True


async def _attempt_overflow_recovery(
    turn_state: "_TurnState",
    agent: "SessionAgent",
    deps: "CoDeps",
    frontend: "Frontend",
) -> bool:
    """Try to compact history after a context-overflow error.

    Returns True if recovery succeeded and the caller should retry the turn,
    False if recovery is impossible.
    """
    if turn_state.overflow_recovery_attempted:
        frontend.on_status("Context overflow — unrecoverable.")
        return False
    turn_state.overflow_recovery_attempted = True
    recovery_ctx = RunContext(
        deps=deps,
        model=agent.model,
        usage=turn_state.latest_usage or RunUsage(),
    )
    recovery_history = _history_with_pending_user_input(turn_state)
    compacted = await recover_overflow_history(recovery_ctx, recovery_history)
    if compacted is None:
        frontend.on_status("Context overflow — unrecoverable.")
        return False
    turn_state.current_history = compacted
    turn_state.current_input = None
    frontend.on_status("Context overflow — compacting and retrying...")
    return True


# ---------------------------------------------------------------------------
# run_turn — the main orchestration entry point
# ---------------------------------------------------------------------------


@trace("co.turn", new_trace=True)
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
    deps.usage_accumulator.reset()
    deps.runtime.status_callback = frontend.on_status
    frontend.on_status("Co is thinking...")
    turn_state = _TurnState(
        current_input=user_input,
        current_history=message_history,
    )

    span = current_span()
    span.set_attribute("co.user_prompt.chars", len(user_input or ""))
    try:
        active_settings: ModelSettings | None = model_settings
        while True:
            try:
                await _execute_stream_segment(
                    turn_state,
                    agent,
                    deps,
                    active_settings,
                    frontend,
                    message_history=turn_state.current_history,
                )

                await _run_approval_loop(turn_state, agent, deps, active_settings, frontend)
                if cap_result := _check_turn_caps(turn_state, deps, frontend):
                    return cap_result
                latest_result = turn_state.latest_result
                assert latest_result is not None
                turn_state.current_history = latest_result.all_messages()
                _emit_final_output_if_needed(turn_state, latest_result, frontend)

                boosted_settings = _length_retry_settings(latest_result, active_settings)
                if boosted_settings is not None:
                    active_settings = boosted_settings
                    # Do NOT set current_input=None here. Keeping the original user prompt
                    # ensures the retry sends a proper user turn instead of a bare continuation
                    # request (conversation ending with an assistant message). qwen3.6 enters
                    # thinking mode on bare continuations regardless of think=False, exhausting
                    # any token budget before producing text.
                    frontend.on_status(
                        f"Response truncated — retrying with {active_settings['max_tokens']:,} output tokens…"
                    )
                    continue

                _check_output_limits(turn_state, deps, frontend)

                return TurnResult(
                    messages=turn_state.current_history,
                    output=latest_result.output,
                    usage=turn_state.latest_usage,
                    interrupted=False,
                    streamed_text=turn_state.latest_streamed_text,
                    outcome="continue",
                    model_requests=turn_state.model_requests,
                )

            except ModelHTTPError as e:
                code = e.status_code
                # Context overflow — must resolve completely (compact+retry OR terminal).
                # Design invariant: NEVER falls through to the 400 reformulation handler.
                if is_context_overflow(e):
                    if await _attempt_overflow_recovery(turn_state, agent, deps, frontend):
                        continue
                    turn_state.outcome = "error"
                    return _build_error_turn_result(turn_state)
                if code == 400 and _apply_400_reformulation(turn_state, e):
                    frontend.on_status("Tool call rejected (HTTP 400), reflecting to model...")
                    await asyncio.sleep(0.5)
                    continue
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

            except (ModelAPIError, httpx.ReadError, TimeoutError) as e:
                frontend.on_status(_transient_error_message(e))
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
        # Record the turn's FINAL cumulative usage exactly once, here at the sole
        # point that catches every return path (success, cap hard-stop, HTTP/API/
        # malformed errors, interrupt). RunUsage is cumulative within a turn — the
        # orchestrator carries prior segments forward via run_stream_events(usage=...),
        # so recording per-segment would double-count; recording latest_usage once
        # does not. Forked subagent/summarizer tokens roll into the same accumulator
        # via their own once-per-run boundaries.
        if turn_state.latest_usage is not None:
            record_usage(deps, turn_state.latest_usage)
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
        span.set_attribute("turn.model_requests", turn_state.model_requests)
        deps.runtime.tool_progress_callback = None
