"""Orchestration state machine — extracted from main.py for testability.

Contains TurnResult, run_turn(), and supporting private functions.
FrontendProtocol lives in co_cli/display.py. The chat loop in main.py
delegates all LLM interaction here.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai import Agent, AgentRunResult, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError, UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent, FunctionToolResultEvent,
    ModelRequest, ModelResponse, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta,
    PartEndEvent, FinalResultEvent,
    ThinkingPart, ThinkingPartDelta,
    ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.usage import UsageLimits
from opentelemetry import trace as otel_trace

# Typed return value from run_turn() to chat loop
TurnOutcome = Literal["continue", "error"]

_TRACER = otel_trace.get_tracer("co-cli.orchestrate")
logger = logging.getLogger(__name__)

from co_cli.display import FrontendProtocol
from co_cli.tools._http_retry import parse_retry_after
from co_cli.tools._tool_approvals import (
    ApprovalSubject,
    decode_tool_args,
    is_auto_approved,
    record_approval_choice,
    resolve_approval_subject,
)
from co_cli.deps import CoDeps


# ---------------------------------------------------------------------------
# TurnResult — returned by run_turn()
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    messages: list = field(default_factory=list)
    output: Any = None
    usage: Any = None
    interrupted: bool = False
    streamed_text: bool = False
    outcome: TurnOutcome = "continue"


# ---------------------------------------------------------------------------
# _TurnState — explicit turn-scoped mutable state for run_turn()
# ---------------------------------------------------------------------------


@dataclass
class _TurnState:
    """Holds all mutable state for one orchestrated turn.

    Collects fields that would otherwise be parallel locals in run_turn(),
    making invariants explicit and mutation paths auditable.
    """
    current_input: str | None
    current_history: list
    tool_approval_decisions: DeferredToolResults | None = None
    latest_result: AgentRunResult | None = None
    latest_streamed_text: bool = False
    latest_usage: Any = None
    retry_budget_remaining: int = 0
    backoff_base: float = 1.0


# ---------------------------------------------------------------------------
# Rendering interval
# ---------------------------------------------------------------------------

_RENDER_INTERVAL = 0.05  # 20 FPS


# ---------------------------------------------------------------------------
# _StreamState — explicit transient streaming state
# ---------------------------------------------------------------------------


@dataclass
class _StreamState:
    text_buffer: str = ""
    last_text_render_at: float = 0.0
    thinking_buffer: str = ""
    last_thinking_render_at: float = 0.0
    thinking_active: bool = False
    streamed_text: bool = False


def _flush_thinking(state: _StreamState, frontend: FrontendProtocol) -> None:
    if state.thinking_buffer:
        frontend.on_thinking_commit(state.thinking_buffer.rstrip())
        state.thinking_buffer = ""
        state.last_thinking_render_at = 0.0
        state.thinking_active = False


def _append_thinking(
    state: _StreamState,
    frontend: FrontendProtocol,
    content: str,
) -> None:
    if not content:
        return
    state.thinking_buffer += content
    now = time.monotonic()
    if now - state.last_thinking_render_at >= _RENDER_INTERVAL:
        state.thinking_active = True
        frontend.on_thinking_delta(state.thinking_buffer.rstrip() or "...")
        state.last_thinking_render_at = now


def _append_text(
    state: _StreamState,
    frontend: FrontendProtocol,
    content: str,
) -> None:
    if not content:
        return
    if state.thinking_active or state.thinking_buffer:
        _flush_thinking(state, frontend)
    state.text_buffer += content
    state.streamed_text = True
    now = time.monotonic()
    if now - state.last_text_render_at >= _RENDER_INTERVAL:
        frontend.on_text_delta(state.text_buffer)
        state.last_text_render_at = now


def _commit_text(state: _StreamState, frontend: FrontendProtocol) -> None:
    if state.text_buffer:
        frontend.on_text_commit(state.text_buffer)
        state.text_buffer = ""
        state.last_text_render_at = 0.0


def _flush_for_tool_output(state: _StreamState, frontend: FrontendProtocol) -> None:
    """Flush thinking/text before inline tool annotations and output panels."""
    if state.thinking_active or state.thinking_buffer:
        _flush_thinking(state, frontend)
    _commit_text(state, frontend)


def _handle_part_start_event(
    event: PartStartEvent,
    state: _StreamState,
    frontend: FrontendProtocol,
    *,
    verbose: bool,
) -> bool:
    """Handle part start event. Returns True if consumed."""
    if isinstance(event.part, ThinkingPart):
        if not verbose:
            return True
        _append_thinking(state, frontend, event.part.content)
        return True
    if isinstance(event.part, TextPart):
        _append_text(state, frontend, event.part.content)
        return True
    return False


def _handle_part_delta_event(
    event: PartDeltaEvent,
    state: _StreamState,
    frontend: FrontendProtocol,
    *,
    verbose: bool,
) -> bool:
    """Handle part delta event. Returns True if consumed."""
    if isinstance(event.delta, ThinkingPartDelta):
        if not verbose:
            return True
        _append_thinking(state, frontend, event.delta.content_delta or "")
        return True
    if isinstance(event.delta, TextPartDelta):
        _append_text(state, frontend, event.delta.content_delta)
        return True
    return False


# ---------------------------------------------------------------------------
# _tool_args_display — per-tool args extraction for on_tool_start
# ---------------------------------------------------------------------------

_TOOL_DISPLAY_ARG: dict[str, str] = {
    "run_shell_command": "cmd",
    "web_search": "query",
    "web_fetch": "url",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "find_in_files": "pattern",
    "list_directory": "path",
    "save_memory": "content",
    "recall_article": "query",
    "search_knowledge": "query",
    "search_memories": "query",
    "search_notes": "query",
    "read_note": "filename",
    "run_coder_subagent": "task",
    "run_research_subagent": "query",
    "run_analysis_subagent": "question",
    "run_thinking_subagent": "problem",
    "start_background_task": "command",
    "check_task_status": "task_id",
}


def _tool_args_display(tool_name: str, part: ToolCallPart) -> str:
    """Isolate per-tool args extraction so FunctionToolCallEvent handler stays uniform."""
    key = _TOOL_DISPLAY_ARG.get(tool_name)
    if not key:
        return ""
    val = part.args_as_dict().get(key, "")
    return str(val)[:120]


# ---------------------------------------------------------------------------
# _run_stream_segment — inner segment loop: runs agent stream, dispatches frontend events
# ---------------------------------------------------------------------------


async def _run_stream_segment(
    agent: Agent,
    *,
    user_input: str | None,
    deps: CoDeps,
    message_history: list,
    model_settings: dict | None = None,
    usage_limits: UsageLimits,
    usage: Any | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
    verbose: bool,
    frontend: FrontendProtocol,
) -> tuple[AgentRunResult, bool]:
    """Consume one streamed segment and return its final result.

    Iterates `agent.run_stream_events(...)` for one contiguous segment — the initial model
    run or an approval-resume after deferred tool calls — and dispatches each SDK event to
    the corresponding frontend callback (text streaming, thinking, tool start/complete).

    `deferred_tool_results` carries tool approval decisions from a prior approval step,
    not actual tool output. Real tool output arrives later as ToolReturnPart events within
    the resumed segment.

    Returns (AgentRunResult, streamed_text) where streamed_text is True when at least one
    PartStart/PartDelta text event was dispatched live. If False, the caller should route
    result.output to frontend.on_final_output() instead.

    Raises RuntimeError if the stream ends without AgentRunResultEvent — callers can treat
    this as a trustworthy primitive without guarding for None.
    """
    result = None
    state = _StreamState()

    try:
        async for event in agent.run_stream_events(
            user_input, deps=deps, message_history=message_history,
            model_settings=model_settings,
            usage_limits=usage_limits,
            usage=usage,
            deferred_tool_results=deferred_tool_results,
        ):
            if isinstance(event, PartStartEvent):
                if _handle_part_start_event(
                    event,
                    state,
                    frontend,
                    verbose=verbose,
                ):
                    continue

            if isinstance(event, PartDeltaEvent):
                if _handle_part_delta_event(
                    event,
                    state,
                    frontend,
                    verbose=verbose,
                ):
                    continue

            # Readiness/meta events are intentionally side-effect free for
            # rendering; text may continue after FinalResultEvent.
            if isinstance(event, (FinalResultEvent, PartEndEvent)):
                continue

            if isinstance(event, FunctionToolCallEvent):
                _flush_for_tool_output(state, frontend)
                tool_id = event.tool_call_id
                name = event.part.tool_name
                args_display = _tool_args_display(name, event.part)
                frontend.on_tool_start(tool_id, name, args_display)
                deps.runtime.tool_progress_callback = (
                    lambda msg, _tid=tool_id: frontend.on_tool_progress(_tid, msg)
                )
                continue

            if isinstance(event, FunctionToolResultEvent):
                _flush_for_tool_output(state, frontend)
                deps.runtime.tool_progress_callback = None
                tool_id = event.tool_call_id
                if not isinstance(event.result, ToolReturnPart):
                    # RetryPromptPart: tool raised ModelRetry or validation failed.
                    # Close the tool surface cleanly — no result to display.
                    frontend.on_tool_complete(tool_id, None)
                    continue
                content = event.result.content
                if isinstance(content, str) and content.strip():
                    frontend.on_tool_complete(tool_id, content)
                elif isinstance(content, dict) and content.get("_kind") == "tool_result":
                    frontend.on_tool_complete(tool_id, content)
                elif isinstance(content, dict):
                    # MCP tools return raw JSON dicts — render as compact key: value summary
                    summary = "; ".join(f"{k}: {str(v)[:60]}" for k, v in list(content.items())[:5])
                    if len(content) > 5:
                        summary += f" (+{len(content) - 5} more)"
                    frontend.on_tool_complete(tool_id, summary[:300] or None)
                else:
                    frontend.on_tool_complete(tool_id, None)
                continue

            if isinstance(event, AgentRunResultEvent):
                result = event.result
                continue

        # Normal completion — final render
        if state.thinking_active or state.thinking_buffer:
            _flush_thinking(state, frontend)
        _commit_text(state, frontend)
    finally:
        frontend.cleanup()

    if result is None:
        raise RuntimeError(
            "_run_stream_segment: stream ended without AgentRunResultEvent — segment contract violated"
        )
    return result, state.streamed_text


# ---------------------------------------------------------------------------
# _collect_deferred_tool_approvals — approval collection without resumption
# ---------------------------------------------------------------------------


async def _collect_deferred_tool_approvals(
    result: AgentRunResult,
    deps: CoDeps,
    frontend: FrontendProtocol | None,
) -> DeferredToolResults:
    """Collect approval decisions for all pending deferred tool requests.

    Returns DeferredToolResults without resuming the stream.
    run_turn() resumes via _run_stream_segment() as a separate step.
    """
    mcp_prefixes = frozenset(
        cfg.prefix or name
        for name, cfg in deps.config.mcp_servers.items()
    )
    approvals = DeferredToolResults()

    for call in result.output.approvals:
        args = decode_tool_args(call.args)
        subject = resolve_approval_subject(call.tool_name, args, mcp_prefixes=mcp_prefixes)

        # Auto-approval — skip prompt if subject already approved this session
        if is_auto_approved(subject, deps):
            approvals.approvals[call.tool_call_id] = True
            continue

        # User prompt
        choice = frontend.prompt_approval(subject.display) if frontend is not None else "n"

        record_approval_choice(
            approvals,
            tool_call_id=call.tool_call_id,
            approved=choice in ("y", "a"),
            subject=subject,
            deps=deps,
            remember=choice == "a" and subject.can_remember,
        )

    return approvals


# ---------------------------------------------------------------------------
# _execute_stream_segment — run one segment and update _TurnState in-place
# ---------------------------------------------------------------------------


async def _execute_stream_segment(
    turn_state: _TurnState,
    agent: Agent,
    deps: CoDeps,
    model_settings: dict | None,
    turn_limits: UsageLimits,
    verbose: bool,
    frontend: FrontendProtocol,
) -> None:
    """Run one stream segment and update turn state in-place.

    Reads turn_state.current_input, current_history, tool_approval_decisions,
    and latest_usage. After the call:
    - latest_result holds the AgentRunResult
    - latest_streamed_text reflects whether text was streamed
    - latest_usage is updated from the result
    - tool_approval_decisions is cleared (consumed)
    """
    turn_state.latest_result, turn_state.latest_streamed_text = await _run_stream_segment(
        agent,
        user_input=turn_state.current_input,
        deps=deps,
        message_history=turn_state.current_history,
        model_settings=model_settings,
        usage_limits=turn_limits,
        usage=turn_state.latest_usage,
        deferred_tool_results=turn_state.tool_approval_decisions,
        verbose=verbose,
        frontend=frontend,
    )
    turn_state.latest_usage = turn_state.latest_result.usage()
    turn_state.tool_approval_decisions = None


# ---------------------------------------------------------------------------
# Exception/transition helpers — each owns one exceptional path in run_turn()
# ---------------------------------------------------------------------------


def _handle_usage_limit_exceeded(
    turn_state: _TurnState,
    frontend: FrontendProtocol,
) -> TurnResult:
    """Emit stop message and return TurnResult directly."""
    frontend.on_status("Turn limit reached. Use /continue to resume.")
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
        outcome="continue",
    )


async def _reflect_http_400(
    turn_state: _TurnState,
    e: ModelHTTPError,
    frontend: FrontendProtocol,
    total_retries: int,
) -> None:
    """Decrement retry budget and append model-reflection prompt for HTTP 400."""
    turn_state.retry_budget_remaining -= 1
    attempt = total_retries - turn_state.retry_budget_remaining
    frontend.on_status(
        f"Tool call rejected (HTTP 400), "
        f"reflecting to model... ({attempt}/{total_retries})"
    )
    await asyncio.sleep(0.5)
    reflection = ModelRequest(parts=[UserPromptPart(
        content=(
            "Your previous tool call was rejected by the "
            f"model provider: {e.body}. Please reformulate "
            "your tool call with valid JSON arguments."
        ),
    )])
    turn_state.current_history = turn_state.current_history + [reflection]
    turn_state.current_input = None


async def _apply_http_backoff(
    turn_state: _TurnState,
    e: ModelHTTPError,
    frontend: FrontendProtocol,
    total_retries: int,
) -> None:
    """Decrement retry budget, sleep with backoff, for HTTP 429/5xx."""
    turn_state.retry_budget_remaining -= 1
    attempt = total_retries - turn_state.retry_budget_remaining
    code = e.status_code
    delay = parse_retry_after(None, e.body) or (3.0 if code == 429 else 2.0)
    wait = min(delay * (turn_state.backoff_base ** attempt), 30.0)
    frontend.on_status(
        f"Provider error (HTTP {code}), retrying in {wait:.0f}s... ({attempt}/{total_retries})"
    )
    await asyncio.sleep(wait)
    turn_state.backoff_base *= 1.5


async def _apply_api_backoff(
    turn_state: _TurnState,
    e: ModelAPIError,
    frontend: FrontendProtocol,
    total_retries: int,
) -> None:
    """Decrement retry budget, sleep with backoff, for network/timeout errors."""
    turn_state.retry_budget_remaining -= 1
    attempt = total_retries - turn_state.retry_budget_remaining
    wait = min(2.0 * (turn_state.backoff_base ** attempt), 30.0)
    frontend.on_status(
        f"Network error: {e}, retrying in {wait:.0f}s... ({attempt}/{total_retries})"
    )
    await asyncio.sleep(wait)
    turn_state.backoff_base *= 1.5


def _build_error_turn_result(turn_state: _TurnState) -> TurnResult:
    """Build a terminal error TurnResult from current turn state."""
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
    abort_marker = ModelRequest(parts=[UserPromptPart(
        content=(
            "The user interrupted the previous turn. Some actions "
            "may be incomplete. Verify current state before continuing."
        ),
    )])
    return TurnResult(
        messages=msgs + [abort_marker],
        output=None,
        usage=turn_state.latest_usage,
        interrupted=True,
        streamed_text=turn_state.latest_streamed_text,
        outcome="continue",
    )


# ---------------------------------------------------------------------------
# run_turn — the main orchestration entry point
# ---------------------------------------------------------------------------


async def run_turn(
    *,
    agent: Agent,
    user_input: str,
    deps: CoDeps,
    message_history: list,
    model_settings: dict | None = None,
    verbose: bool = False,
    frontend: FrontendProtocol,
) -> TurnResult:
    """Execute one LLM turn: streaming, approval chaining, error handling.

    Single public entrypoint for an orchestrated agent turn. Emits the root
    `co.turn` span, displays the thinking status, runs the inner retry loop
    for HTTP errors and the approval loop for deferred tool requests, and
    reads turn policy (request limit, retries) from deps.config.

    Returns TurnResult with outcome field for chat loop pattern-matching:
      "continue" — normal completion, prompt for next input
      "error"    — unrecoverable error, display and prompt
    """
    # Reset turn-scoped safety state (doom loop + shell reflection tracking)
    from co_cli.context._history import SafetyState
    deps.runtime.safety_state = SafetyState()

    # Status before span — matches prior wrapper ordering
    frontend.on_status("Co is thinking...")
    max_request_limit = deps.config.max_request_limit
    http_retries = deps.config.model_http_retries
    turn_limits = UsageLimits(request_limit=max_request_limit)
    turn_state = _TurnState(
        current_input=user_input,
        current_history=message_history,
        retry_budget_remaining=http_retries,
    )

    _span_result: TurnResult | None = None
    with _TRACER.start_as_current_span("co.turn") as span:
        try:
            while True:
                try:
                    await _execute_stream_segment(
                        turn_state, agent, deps, model_settings, turn_limits, verbose, frontend
                    )

                    # Approval flow: collect decisions, update state, resume segment
                    while isinstance(turn_state.latest_result.output, DeferredToolRequests):
                        approvals = await _collect_deferred_tool_approvals(
                            turn_state.latest_result, deps, frontend
                        )
                        turn_state.current_input = None
                        turn_state.current_history = turn_state.latest_result.all_messages()
                        turn_state.tool_approval_decisions = approvals
                        await _execute_stream_segment(
                            turn_state, agent, deps, model_settings, turn_limits, verbose, frontend
                        )

                    turn_state.current_history = turn_state.latest_result.all_messages()
                    if not turn_state.latest_streamed_text and isinstance(turn_state.latest_result.output, str):
                        frontend.on_final_output(turn_state.latest_result.output)

                    # Finish reason detection: warn if response was truncated at token limit.
                    if turn_state.latest_result.response.finish_reason == "length":
                        frontend.on_status(
                            "Response may be truncated (hit output token limit). "
                            "Use /continue to extend."
                        )

                    # Context overflow detection: Ollama truncates silently when
                    # input_tokens > num_ctx. Gemini enforces its own hard limit via HTTP 400.
                    # latest_usage.input_tokens is always int (defaults to 0 when provider reports no usage).
                    if turn_state.latest_usage is not None and deps.config.supports_context_ratio_tracking():
                        ratio = turn_state.latest_usage.input_tokens / deps.config.llm_num_ctx
                        with _TRACER.start_as_current_span("ctx_overflow_check") as ctx_span:
                            ctx_span.set_attribute("ctx.input_tokens", turn_state.latest_usage.input_tokens)
                            ctx_span.set_attribute("ctx.num_ctx", deps.config.llm_num_ctx)
                            ctx_span.set_attribute("ctx.ratio", ratio)
                            if ratio >= deps.config.ctx_overflow_threshold:
                                frontend.on_status(
                                    f"Context limit reached ({turn_state.latest_usage.input_tokens:,} / {deps.config.llm_num_ctx:,} tokens)"
                                    " — Ollama likely truncated the prompt. Use /compact or /new."
                                )
                            elif ratio >= deps.config.ctx_warn_threshold:
                                frontend.on_status(
                                    f"Context {ratio:.0%} full ({turn_state.latest_usage.input_tokens:,} / {deps.config.llm_num_ctx:,} tokens)."
                                    " Consider /compact to free space."
                                )

                    _span_result = TurnResult(
                        messages=turn_state.current_history,
                        output=turn_state.latest_result.output,
                        usage=turn_state.latest_usage,
                        interrupted=False,
                        streamed_text=turn_state.latest_streamed_text,
                        outcome="continue",
                    )
                    return _span_result

                except UsageLimitExceeded:
                    _span_result = _handle_usage_limit_exceeded(turn_state, frontend)
                    return _span_result

                except ModelHTTPError as e:
                    code = e.status_code
                    if code == 400 and turn_state.retry_budget_remaining > 0:
                        await _reflect_http_400(turn_state, e, frontend, http_retries)
                        continue
                    if (code == 429 or code >= 500) and turn_state.retry_budget_remaining > 0:
                        await _apply_http_backoff(turn_state, e, frontend, http_retries)
                        continue
                    # 401/403/404, unknown 4xx, or retries exhausted
                    frontend.on_status(f"Provider error (HTTP {code}): {e.body}")
                    _span_result = _build_error_turn_result(turn_state)
                    return _span_result

                except ModelAPIError as e:
                    if turn_state.retry_budget_remaining > 0:
                        await _apply_api_backoff(turn_state, e, frontend, http_retries)
                        continue
                    frontend.on_status(f"Network error: {e}")
                    _span_result = _build_error_turn_result(turn_state)
                    return _span_result

                except (KeyboardInterrupt, asyncio.CancelledError):
                    _span_result = _build_interrupted_turn_result(turn_state)
                    return _span_result

        finally:
            span.set_attribute("turn.outcome", _span_result.outcome if _span_result else "error")
            span.set_attribute("turn.interrupted", _span_result.interrupted if _span_result else False)
            span.set_attribute("turn.input_tokens",
                (_span_result.usage.input_tokens or 0) if _span_result and _span_result.usage else 0)
            span.set_attribute("turn.output_tokens",
                (_span_result.usage.output_tokens or 0) if _span_result and _span_result.usage else 0)
            deps.runtime.tool_progress_callback = None
