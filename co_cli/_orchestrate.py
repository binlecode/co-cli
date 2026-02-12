"""Orchestration state machine — extracted from main.py for testability.

Contains FrontendProtocol, TurnResult, run_turn(), and supporting private
functions. The chat loop in main.py delegates all LLM interaction here.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError
from pydantic_ai.messages import (
    FunctionToolCallEvent, FunctionToolResultEvent,
    ModelRequest, ModelResponse, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta,
    PartEndEvent, FinalResultEvent,
    ThinkingPart, ThinkingPartDelta,
    ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from co_cli._approval import _is_safe_command
from co_cli._provider_errors import ProviderErrorAction, classify_provider_error
from co_cli.deps import CoDeps


# ---------------------------------------------------------------------------
# FrontendProtocol — abstraction for display + user interaction
# ---------------------------------------------------------------------------


@runtime_checkable
class FrontendProtocol(Protocol):
    """Display and interaction contract for the orchestration layer.

    Implementations: TerminalFrontend (Rich/prompt-toolkit), RecordingFrontend (tests).
    """

    def on_text_delta(self, accumulated: str) -> None:
        """Incremental Markdown render (called at throttled FPS)."""
        ...

    def on_text_commit(self, final: str) -> None:
        """Final text render + tear down any live display."""
        ...

    def on_thinking_delta(self, accumulated: str) -> None:
        """Thinking panel update (verbose mode only)."""
        ...

    def on_thinking_commit(self, final: str) -> None:
        """Final thinking panel render."""
        ...

    def on_tool_call(self, name: str, args_display: str) -> None:
        """Dim annotation when tool is invoked."""
        ...

    def on_tool_result(self, title: str, content: str | dict[str, Any]) -> None:
        """Panel for tool output."""
        ...

    def on_status(self, message: str) -> None:
        """Status messages (e.g. 'Co is thinking...')."""
        ...

    def on_final_output(self, text: str) -> None:
        """Fallback Markdown render when streaming didn't emit text."""
        ...

    def prompt_approval(self, description: str) -> str:
        """Prompt user for approval. Returns 'y', 'n', or 'a' (yolo)."""
        ...

    def cleanup(self) -> None:
        """Exception/cancellation cleanup — restore terminal state."""
        ...


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
# _stream_events — extracted from _stream_agent_run
# ---------------------------------------------------------------------------


async def _stream_events(agent: Agent, *, user_input: str | None, deps: CoDeps,
                         message_history: list, model_settings: dict,
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
# _handle_approvals — extracted from main.py
# ---------------------------------------------------------------------------


async def _handle_approvals(agent: Agent, deps: CoDeps, result,
                            model_settings: dict, usage_limits: UsageLimits,
                            usage=None, verbose: bool = False,
                            frontend: FrontendProtocol | None = None):
    """Prompt user [y/n/a(yolo)] for each pending tool call, then resume."""
    approvals = DeferredToolResults()

    for call in result.output.approvals:
        args = call.args
        if isinstance(args, str):
            args = json.loads(args)
        args = args or {}
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        desc = f"{call.tool_name}({args_str})"

        if deps.auto_confirm:
            approvals.approvals[call.tool_call_id] = True
            continue

        # Auto-approve safe shell commands only when sandbox provides isolation.
        if call.tool_name == "run_shell_command":
            cmd = args.get("cmd", "")
            if (
                deps.sandbox.isolation_level != "none"
                and _is_safe_command(cmd, deps.shell_safe_commands)
            ):
                approvals.approvals[call.tool_call_id] = True
                continue

        if frontend is not None:
            choice = frontend.prompt_approval(desc)
        else:
            choice = "n"

        if choice == "a":
            deps.auto_confirm = True
            approvals.approvals[call.tool_call_id] = True
        elif choice == "y":
            approvals.approvals[call.tool_call_id] = True
        else:
            approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

    return await _stream_events(
        agent, user_input=None, deps=deps,
        message_history=result.all_messages(),
        model_settings=model_settings, usage_limits=usage_limits,
        usage=usage, verbose=verbose,
        frontend=frontend,
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
    model_settings: dict,
    max_request_limit: int = 25,
    http_retries: int = 2,
    verbose: bool = False,
    frontend: FrontendProtocol,
) -> TurnResult:
    """Execute one LLM turn: streaming, approval chaining, error handling.

    Contains the inner retry loop for HTTP errors and the approval loop
    for deferred tool requests. Delegates all display to the frontend.
    """
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

            # Handle deferred tool approvals (loop: resumed run may trigger more)
            while isinstance(result.output, DeferredToolRequests):
                result, streamed_text = await _handle_approvals(
                    agent, deps, result, model_settings,
                    turn_limits, usage=turn_usage,
                    verbose=verbose, frontend=frontend,
                )
                turn_usage = result.usage()

            message_history = result.all_messages()
            if not streamed_text and isinstance(result.output, str):
                frontend.on_final_output(result.output)

            return TurnResult(
                messages=message_history,
                output=result.output,
                usage=turn_usage,
                interrupted=False,
                streamed_text=streamed_text,
            )

        except ModelHTTPError as e:
            action, msg, delay = classify_provider_error(e)

            if action == ProviderErrorAction.REFLECT and http_retries_left > 0:
                http_retries_left -= 1
                attempt = http_retries - http_retries_left
                frontend.on_status(
                    f"Tool call rejected (HTTP {e.status_code}), "
                    f"reflecting to model... ({attempt}/{http_retries})"
                )
                await asyncio.sleep(delay)
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

            if action == ProviderErrorAction.BACKOFF_RETRY and http_retries_left > 0:
                http_retries_left -= 1
                attempt = http_retries - http_retries_left
                wait = min(delay * (backoff_base ** attempt), 30.0)
                frontend.on_status(
                    f"{msg}, retrying in {wait:.0f}s... ({attempt}/{http_retries})"
                )
                await asyncio.sleep(wait)
                backoff_base *= 1.5
                continue

            # ABORT or retries exhausted
            frontend.on_status(f"Provider error: {msg}")
            msgs = result.all_messages() if result else message_history
            return TurnResult(
                messages=msgs,
                output=None,
                usage=turn_usage,
                interrupted=False,
                streamed_text=streamed_text,
            )

        except ModelAPIError as e:
            # Network/timeout — use backoff path
            if http_retries_left > 0:
                http_retries_left -= 1
                attempt = http_retries - http_retries_left
                _, msg, delay = classify_provider_error(e)
                wait = min(delay * (backoff_base ** attempt), 30.0)
                frontend.on_status(
                    f"{msg}, retrying in {wait:.0f}s... ({attempt}/{http_retries})"
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
            )

        except (KeyboardInterrupt, asyncio.CancelledError):
            msgs = result.all_messages() if result else message_history
            message_history = _patch_dangling_tool_calls(msgs)
            frontend.on_status("Interrupted.")
            return TurnResult(
                messages=message_history,
                output=None,
                usage=turn_usage,
                interrupted=True,
                streamed_text=streamed_text,
            )
