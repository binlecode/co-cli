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
TurnOutcome = Literal["continue", "stop", "error", "compact"]

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
# _patch_dangling_tool_calls — moved verbatim from main.py
# ---------------------------------------------------------------------------


def _patch_dangling_tool_calls(
    messages: list, error_message: str = "Interrupted by user."
) -> list:
    """Patch message history so all ToolCallParts have matching ToolReturnParts.

    LLM models expect both a tool call and its corresponding return in
    history. Without this patch, the next agent.run() would fail.

    Scans *all* ModelResponse messages (not just the last one) to handle
    interrupts during multi-tool approval loops where earlier responses may
    also have dangling calls.
    """
    if not messages:
        return messages

    # Collect all tool_call_ids that already have a ToolReturnPart
    answered_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for p in msg.parts:
                if isinstance(p, ToolReturnPart) and p.tool_call_id:
                    answered_ids.add(p.tool_call_id)

    # Scan all ModelResponse messages for unanswered ToolCallParts
    dangling: list[ToolCallPart] = []
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for p in msg.parts:
            if isinstance(p, ToolCallPart) and p.tool_call_id not in answered_ids:
                dangling.append(p)

    if not dangling:
        return messages

    return_parts = [
        ToolReturnPart(
            tool_name=tc.tool_name,
            tool_call_id=tc.tool_call_id,
            content=error_message,
        )
        for tc in dangling
    ]
    return messages + [ModelRequest(parts=return_parts)]


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
# _run_stream_turn — per-turn stream driver: runs agent stream, dispatches frontend events
# ---------------------------------------------------------------------------


async def _run_stream_turn(agent: Agent, *, user_input: str | None, deps: CoDeps,
                         message_history: list, model_settings: dict | None = None,
                         usage_limits: UsageLimits, usage=None,
                         deferred_tool_results=None, verbose: bool,
                         frontend: FrontendProtocol):
    """Run the agent stream for one turn segment, dispatching SDK events to frontend callbacks.

    Returns (result, streamed_text).
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
    run_turn() resumes via _run_stream_turn() as a separate step.
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
      "compact"  — reserved for compaction triggers
    """
    # Reset turn-scoped safety state (doom loop + shell reflection tracking)
    from co_cli.context._history import SafetyState
    deps.runtime.safety_state = SafetyState()

    # Status before span — matches prior wrapper ordering
    frontend.on_status("Co is thinking...")
    _span_result: TurnResult | None = None
    with _TRACER.start_as_current_span("co.turn") as span:
        try:
            result = None
            streamed_text = False
            max_request_limit = deps.config.max_request_limit
            http_retries = deps.config.model_http_retries
            http_retries_left = http_retries
            current_input: str | None = user_input
            current_history = message_history
            current_deferred_results = None
            turn_limits = UsageLimits(request_limit=max_request_limit)
            turn_usage = None
            backoff_base = 1.0

            while True:
                try:
                    result, streamed_text = await _run_stream_turn(
                        agent, user_input=current_input, deps=deps,
                        message_history=current_history, model_settings=model_settings,
                        usage_limits=turn_limits, usage=turn_usage,
                        deferred_tool_results=current_deferred_results,
                        verbose=verbose, frontend=frontend,
                    )
                    turn_usage = result.usage()
                    current_deferred_results = None

                    # Approval flow interception loop: collect decisions, then resume the same stream
                    while isinstance(result.output, DeferredToolRequests):
                        approvals = await _collect_deferred_tool_approvals(result, deps, frontend)
                        current_input = None
                        current_history = result.all_messages()
                        current_deferred_results = approvals
                        result, streamed_text = await _run_stream_turn(
                            agent, user_input=None, deps=deps,
                            message_history=current_history,
                            model_settings=model_settings, usage_limits=turn_limits,
                            usage=turn_usage, deferred_tool_results=current_deferred_results,
                            verbose=verbose, frontend=frontend,
                        )
                        turn_usage = result.usage()
                        current_deferred_results = None

                    message_history = result.all_messages()
                    if not streamed_text and isinstance(result.output, str):
                        frontend.on_final_output(result.output)

                    # Finish reason detection: warn if response was truncated at token limit.
                    if result.response.finish_reason == "length":
                        frontend.on_status(
                            "Response may be truncated (hit output token limit). "
                            "Use /continue to extend."
                        )

                    # Context overflow detection: Ollama truncates silently when
                    # input_tokens > num_ctx. Gemini enforces its own hard limit via HTTP 400.
                    # turn_usage.input_tokens is always int (defaults to 0 when provider reports no usage).
                    if turn_usage is not None and deps.config.supports_context_ratio_tracking():
                        ratio = turn_usage.input_tokens / deps.config.llm_num_ctx
                        with _TRACER.start_as_current_span("ctx_overflow_check") as ctx_span:
                            ctx_span.set_attribute("ctx.input_tokens", turn_usage.input_tokens)
                            ctx_span.set_attribute("ctx.num_ctx", deps.config.llm_num_ctx)
                            ctx_span.set_attribute("ctx.ratio", ratio)
                            if ratio >= deps.config.ctx_overflow_threshold:
                                frontend.on_status(
                                    f"Context limit reached ({turn_usage.input_tokens:,} / {deps.config.llm_num_ctx:,} tokens)"
                                    " — Ollama likely truncated the prompt. Use /compact or /new."
                                )
                            elif ratio >= deps.config.ctx_warn_threshold:
                                frontend.on_status(
                                    f"Context {ratio:.0%} full ({turn_usage.input_tokens:,} / {deps.config.llm_num_ctx:,} tokens)."
                                    " Consider /compact to free space."
                                )

                    _span_result = TurnResult(
                        messages=message_history,
                        output=result.output,
                        usage=turn_usage,
                        interrupted=False,
                        streamed_text=streamed_text,
                        outcome="continue",
                    )
                    return _span_result

                except UsageLimitExceeded:
                    # Grace turn: ask the model to summarize progress
                    frontend.on_status(
                        f"Turn limit reached ({max_request_limit} requests). "
                        "Asking for a progress summary..."
                    )
                    msgs = result.all_messages() if result else current_history
                    msgs = _patch_dangling_tool_calls(msgs)
                    grace_msg = ModelRequest(parts=[UserPromptPart(
                        content=(
                            "Turn limit reached. Summarize your progress so far "
                            "and what remains to be done. The user can /continue "
                            "to resume with a fresh budget."
                        ),
                    )])
                    try:
                        grace_result, grace_streamed = await _run_stream_turn(
                            agent, user_input=None, deps=deps,
                            message_history=msgs + [grace_msg],
                            model_settings=model_settings,
                            usage_limits=UsageLimits(request_limit=1),
                            verbose=verbose, frontend=frontend,
                        )
                        message_history = grace_result.all_messages()
                        if not grace_streamed and isinstance(grace_result.output, str):
                            frontend.on_final_output(grace_result.output)
                        _span_result = TurnResult(
                            messages=message_history,
                            output=grace_result.output,
                            usage=turn_usage,
                            interrupted=False,
                            streamed_text=grace_streamed,
                            outcome="continue",
                        )
                        return _span_result
                    except Exception:
                        # Grace turn itself failed — return what we have
                        frontend.on_status(
                            "Turn limit reached. Use /continue to resume."
                        )
                        _span_result = TurnResult(
                            messages=msgs,
                            output=None,
                            usage=turn_usage,
                            interrupted=False,
                            streamed_text=streamed_text,
                            outcome="continue",
                        )
                        return _span_result

                except ModelHTTPError as e:
                    code = e.status_code

                    if code == 400 and http_retries_left > 0:
                        http_retries_left -= 1
                        attempt = http_retries - http_retries_left
                        frontend.on_status(
                            f"Tool call rejected (HTTP {code}), "
                            f"reflecting to model... ({attempt}/{http_retries})"
                        )
                        await asyncio.sleep(0.5)
                        reflection = ModelRequest(parts=[UserPromptPart(
                            content=(
                                "Your previous tool call was rejected by the "
                                f"model provider: {e.body}. Please reformulate "
                                "your tool call with valid JSON arguments."
                            ),
                        )])
                        current_history = current_history + [reflection]
                        current_input = None
                        continue

                    if (code == 429 or code >= 500) and http_retries_left > 0:
                        http_retries_left -= 1
                        attempt = http_retries - http_retries_left
                        delay = parse_retry_after(None, e.body) or (3.0 if code == 429 else 2.0)
                        wait = min(delay * (backoff_base ** attempt), 30.0)
                        frontend.on_status(
                            f"Provider error (HTTP {code}), retrying in {wait:.0f}s... ({attempt}/{http_retries})"
                        )
                        await asyncio.sleep(wait)
                        backoff_base *= 1.5
                        continue

                    # 401/403/404, unknown 4xx, or retries exhausted
                    frontend.on_status(f"Provider error (HTTP {code}): {e.body}")
                    msgs = result.all_messages() if result else current_history
                    _span_result = TurnResult(
                        messages=msgs,
                        output=None,
                        usage=turn_usage,
                        interrupted=False,
                        streamed_text=streamed_text,
                        outcome="error",
                    )
                    return _span_result

                except ModelAPIError as e:
                    # Network/timeout — backoff retry
                    if http_retries_left > 0:
                        http_retries_left -= 1
                        attempt = http_retries - http_retries_left
                        wait = min(2.0 * (backoff_base ** attempt), 30.0)
                        frontend.on_status(
                            f"Network error: {e}, retrying in {wait:.0f}s... ({attempt}/{http_retries})"
                        )
                        await asyncio.sleep(wait)
                        backoff_base *= 1.5
                        continue

                    frontend.on_status(f"Network error: {e}")
                    msgs = result.all_messages() if result else current_history
                    _span_result = TurnResult(
                        messages=msgs,
                        output=None,
                        usage=turn_usage,
                        interrupted=False,
                        streamed_text=streamed_text,
                        outcome="error",
                    )
                    return _span_result

                except (KeyboardInterrupt, asyncio.CancelledError):
                    msgs = result.all_messages() if result else current_history
                    message_history = _patch_dangling_tool_calls(msgs)
                    # Abort marker — model sees this on the next turn so it knows
                    # the previous turn was interrupted and can verify state.
                    abort_marker = ModelRequest(parts=[UserPromptPart(
                        content=(
                            "The user interrupted the previous turn. Some actions "
                            "may be incomplete. Verify current state before continuing."
                        ),
                    )])
                    message_history = message_history + [abort_marker]
                    frontend.on_status("Interrupted.")
                    _span_result = TurnResult(
                        messages=message_history,
                        output=None,
                        usage=turn_usage,
                        interrupted=True,
                        streamed_text=streamed_text,
                        outcome="continue",
                    )
                    return _span_result
        finally:
            span.set_attribute("turn.outcome", _span_result.outcome if _span_result else "error")
            span.set_attribute("turn.interrupted", _span_result.interrupted if _span_result else False)
            span.set_attribute("turn.input_tokens",
                (_span_result.usage.input_tokens or 0) if _span_result and _span_result.usage else 0)
            span.set_attribute("turn.output_tokens",
                (_span_result.usage.output_tokens or 0) if _span_result and _span_result.usage else 0)
            deps.runtime.tool_progress_callback = None
