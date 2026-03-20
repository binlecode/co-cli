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

from pydantic_ai import Agent, AgentRunResult, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults, FinishReason
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
    decode_tool_args,
    format_tool_call_description,
    is_session_auto_approved,
    record_approval_choice,
)
from co_cli.deps import CoDeps


def _check_skill_grant(tool_name: str, deps: CoDeps) -> bool:
    """Return True if tool_name is granted by the active skill's allowed-tools."""
    if tool_name not in deps.session.skill_tool_grants:
        return False
    # Eligibility gate: skill grants must not bypass protected tools.
    # Condition 1 — registry-level: tool registered with requires_approval=True.
    if deps.session.tool_approvals.get(tool_name, False):
        logger.debug("Skill grant denied: tool=%s is approval-gated", tool_name)
        return False
    # Condition 2 — explicit carve-out: run_shell_command is registered requires_approval=False
    # at the registry level but raises ApprovalRequired internally under REQUIRE_APPROVAL policy.
    # The registry dict alone does not catch it. This guard is defensive — run_shell_command
    # normally never reaches the deferred path (it is requires_approval=False). It exists to
    # prevent a future registration change or unusual code path from silently bypassing shell
    # approval via skill grant.
    if tool_name == "run_shell_command":
        logger.debug("Skill grant denied: run_shell_command requires explicit user approval")
        return False
    logger.debug(
        "Skill grant: tool=%s active_grants=%s",
        tool_name,
        sorted(deps.session.skill_tool_grants),
    )
    return True


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
    tool_preamble_emitted: bool = False


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
# Tool preamble — fallback status when model emits no text before first tool call
# ---------------------------------------------------------------------------

_TOOL_PREAMBLE: dict[str, str] = {
    "recall_memory": "Checking saved context before answering.",
    "web_search": "Looking up current sources.",
    "web_fetch": "Reading that source for details.",
    "run_shell_command": "Running a quick check.",
    "save_memory": "Saving that to memory.",
    "list_memories": "Checking saved context.",
    "search_notes": "Searching notes.",
    "read_note": "Reading that note.",
    "search_drive_files": "Checking Drive.",
    "list_emails": "Checking email.",
    "list_calendar_events": "Checking calendar.",
}


def _tool_preamble_message(tool_name: str) -> str:
    return _TOOL_PREAMBLE.get(tool_name, "Running a quick check before answering.")


# ---------------------------------------------------------------------------
# _stream_events — extracted from _stream_agent_run
# ---------------------------------------------------------------------------


async def _stream_events(agent: Agent, *, user_input: str | None, deps: CoDeps,
                         message_history: list, model_settings: dict | None = None,
                         usage_limits: UsageLimits, usage=None,
                         deferred_tool_results=None, verbose: bool,
                         frontend: FrontendProtocol):
    """Stream agent events, dispatching to frontend callbacks.

    Returns (result, streamed_text).
    """
    pending_cmds: dict[str, str] = {}
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
                tool = event.part.tool_name
                # Fallback: if model emitted no text before the first tool call, inject a
                # user-visible status line so there is no perceived silence.
                if not state.streamed_text and not state.tool_preamble_emitted:
                    frontend.on_status(_tool_preamble_message(tool))
                    state.tool_preamble_emitted = True
                if tool == "run_shell_command":
                    cmd = event.part.args_as_dict().get("cmd", "")
                    pending_cmds[event.tool_call_id] = cmd
                    frontend.on_tool_call(tool, cmd)
                else:
                    frontend.on_tool_call(tool, "")
                continue

            if isinstance(event, FunctionToolResultEvent):
                _flush_for_tool_output(state, frontend)
                if not isinstance(event.result, ToolReturnPart):
                    continue
                content = event.result.content
                if isinstance(content, str) and content.strip():
                    title = pending_cmds.get(event.tool_call_id, event.result.tool_name)
                    frontend.on_tool_result(title, content)
                elif isinstance(content, dict) and "display" in content:
                    frontend.on_tool_result(event.result.tool_name, content)
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
    run_turn() resumes via _stream_events() as a separate step.
    """
    approvals = DeferredToolResults()

    for call in result.output.approvals:
        args = decode_tool_args(call.args)

        # Tier 1: skill grant — auto-approve if in active skill's allowed-tools
        if _check_skill_grant(call.tool_name, deps):
            approvals.approvals[call.tool_call_id] = True
            continue

        # Tier 2: session auto-approval — skip prompt if user previously chose "a"
        if is_session_auto_approved(call.tool_name, deps):
            approvals.approvals[call.tool_call_id] = True
            continue

        # Tier 3: user prompt
        desc = format_tool_call_description(call.tool_name, args)
        choice = frontend.prompt_approval(desc) if frontend is not None else "n"

        record_approval_choice(
            approvals,
            tool_call_id=call.tool_call_id,
            approved=choice in ("y", "a"),
            tool_name=call.tool_name,
            args=args,
            deps=deps,
            remember=choice == "a",
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
    max_request_limit: int = 50,
    http_retries: int = 2,
    verbose: bool = False,
    frontend: FrontendProtocol,
) -> TurnResult:
    """Execute one LLM turn: streaming, approval chaining, error handling.

    Contains the inner retry loop for HTTP errors and the approval loop
    for deferred tool requests. Delegates all display to the frontend.

    Returns TurnResult with outcome field for chat loop pattern-matching:
      "continue" — normal completion, prompt for next input
      "error"    — unrecoverable error, display and prompt
      "compact"  — reserved for compaction triggers
    """
    # Reset turn-scoped safety state (doom loop + shell reflection tracking)
    from co_cli.context._history import SafetyState
    deps.runtime.safety_state = SafetyState()

    result = None
    streamed_text = False
    http_retries_left = http_retries
    current_input: str | None = user_input
    turn_limits = UsageLimits(request_limit=max_request_limit)
    turn_usage = None
    backoff_base = 1.0

    while True:
        try:
            result, streamed_text = await _stream_events(
                agent, user_input=current_input, deps=deps,
                message_history=message_history, model_settings=model_settings,
                usage_limits=turn_limits, usage=turn_usage,
                verbose=verbose, frontend=frontend,
            )
            turn_usage = result.usage()

            # Approval re-entry loop: collect decisions then resume stream separately
            while isinstance(result.output, DeferredToolRequests):
                approvals = await _collect_deferred_tool_approvals(result, deps, frontend)
                result, streamed_text = await _stream_events(
                    agent, user_input=None, deps=deps,
                    message_history=result.all_messages(),
                    model_settings=model_settings, usage_limits=turn_limits,
                    usage=turn_usage, deferred_tool_results=approvals,
                    verbose=verbose, frontend=frontend,
                )
                turn_usage = result.usage()

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
                with _TRACER.start_as_current_span("ctx_overflow_check") as span:
                    span.set_attribute("ctx.input_tokens", turn_usage.input_tokens)
                    span.set_attribute("ctx.num_ctx", deps.config.llm_num_ctx)
                    span.set_attribute("ctx.ratio", ratio)
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

            return TurnResult(
                messages=message_history,
                output=result.output,
                usage=turn_usage,
                interrupted=False,
                streamed_text=streamed_text,
                outcome="continue",
            )

        except UsageLimitExceeded:
            # Grace turn: ask the model to summarize progress
            frontend.on_status(
                f"Turn limit reached ({max_request_limit} requests). "
                "Asking for a progress summary..."
            )
            msgs = result.all_messages() if result else message_history
            msgs = _patch_dangling_tool_calls(msgs)
            grace_msg = ModelRequest(parts=[UserPromptPart(
                content=(
                    "Turn limit reached. Summarize your progress so far "
                    "and what remains to be done. The user can /continue "
                    "to resume with a fresh budget."
                ),
            )])
            try:
                grace_result, grace_streamed = await _stream_events(
                    agent, user_input=None, deps=deps,
                    message_history=msgs + [grace_msg],
                    model_settings=model_settings,
                    usage_limits=UsageLimits(request_limit=1),
                    verbose=verbose, frontend=frontend, model=model,
                )
                message_history = grace_result.all_messages()
                if not grace_streamed and isinstance(grace_result.output, str):
                    frontend.on_final_output(grace_result.output)
                return TurnResult(
                    messages=message_history,
                    output=grace_result.output,
                    usage=turn_usage,
                    interrupted=False,
                    streamed_text=grace_streamed,
                    outcome="continue",
                )
            except Exception:
                # Grace turn itself failed — return what we have
                frontend.on_status(
                    "Turn limit reached. Use /continue to resume."
                )
                return TurnResult(
                    messages=msgs,
                    output=None,
                    usage=turn_usage,
                    interrupted=False,
                    streamed_text=streamed_text,
                    outcome="continue",
                )

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
                message_history = message_history + [reflection]
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
            msgs = result.all_messages() if result else message_history
            return TurnResult(
                messages=msgs,
                output=None,
                usage=turn_usage,
                interrupted=False,
                streamed_text=streamed_text,
                outcome="error",
            )

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
            msgs = result.all_messages() if result else message_history
            return TurnResult(
                messages=msgs,
                output=None,
                usage=turn_usage,
                interrupted=False,
                streamed_text=streamed_text,
                outcome="error",
            )

        except (KeyboardInterrupt, asyncio.CancelledError):
            msgs = result.all_messages() if result else message_history
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
            return TurnResult(
                messages=message_history,
                output=None,
                usage=turn_usage,
                interrupted=True,
                streamed_text=streamed_text,
                outcome="continue",
            )


async def run_turn_with_fallback(
    *,
    agent: Any,
    user_input: str,
    deps: "CoDeps",
    message_history: list,
    verbose: bool,
    frontend: "FrontendProtocol",
) -> "TurnResult":
    """Run a turn using the agent's baked-in model and settings."""
    frontend.on_status("Co is thinking...")
    return await run_turn(
        agent=agent,
        user_input=user_input,
        deps=deps,
        message_history=message_history,
        max_request_limit=deps.config.max_request_limit,
        http_retries=deps.config.model_http_retries,
        verbose=verbose,
        frontend=frontend,
    )
