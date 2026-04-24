"""Dynamic instruction implementations called via ``agent.instructions()`` wrappers in ``agent/_instructions.py``.

These functions produce per-turn system-instruction text and are never
appended to ``message_history`` — they drive the ``instructions`` field that
pydantic-ai re-emits each turn.

Functions:
    _recall_prompt_text   — async, returns date + personality memories + recalled knowledge
    _safety_prompt_text   — sync, returns doom-loop / shell-reflection warnings
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import cast

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.deps import CoDeps
from co_cli.memory.state import MemoryRecallState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Last-user helpers
# ---------------------------------------------------------------------------


def _get_last_user_message(messages: list[ModelMessage]) -> str | None:
    """Extract the text of the most recent UserPromptPart from messages."""
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return None


def _count_user_turns(messages: list[ModelMessage]) -> int:
    """Count ModelRequest messages that contain a non-system UserPromptPart."""
    count = 0
    for msg in messages:
        if isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) for p in msg.parts):
            count += 1
    return count


# ---------------------------------------------------------------------------
# _recall_prompt_text — per-turn dynamic instruction (async — memory recall, no LLM)
# ---------------------------------------------------------------------------


async def _recall_prompt_text(ctx: RunContext[CoDeps]) -> str:
    """Per-turn dynamic instruction: date, personality memories, and recalled knowledge."""
    state: MemoryRecallState = ctx.deps.session.memory_recall_state
    user_turn_count = _count_user_turns(ctx.messages)
    user_msg = _get_last_user_message(ctx.messages)

    parts: list[str] = []

    parts.append(f"Today is {date.today().isoformat()}.")

    # Personality memories — re-evaluated every turn (file may be updated between turns).
    if ctx.deps.config.personality:
        from co_cli.prompts.personalities._injector import _load_personality_memories

        personality_content = _load_personality_memories()
        if personality_content:
            parts.append(personality_content)

    # Knowledge recall — only on new user turns.
    if user_msg and user_turn_count > state.last_recall_user_turn:
        from co_cli.tools.knowledge.read import _recall_for_context

        try:
            result = await _recall_for_context(ctx, user_msg, max_results=3)
            state.last_recall_user_turn = user_turn_count
            state.recall_count += 1
            if (result.metadata or {}).get("count", 0) > 0:
                memory_content = cast("str", result.return_value)
                max_chars = ctx.deps.config.memory.injection_max_chars
                if len(memory_content) > max_chars:
                    memory_content = memory_content[:max_chars]
                parts.append(f"Relevant memories:\n{memory_content}")
        except Exception:
            log.debug("_recall_prompt_text: _recall_for_context failed", exc_info=True)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# _safety_prompt_text — per-turn dynamic instruction (doom loop + shell reflection cap)
# ---------------------------------------------------------------------------


def _count_consecutive_same_calls(messages: list[ModelMessage]) -> int:
    """Count the most-recent contiguous streak of identical tool calls.

    Scans in reverse; stops when a different call is seen or after 10 calls.
    """
    consecutive_same: int = 0
    last_hash: str | None = None
    for msg in reversed(messages):
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart):
                continue
            args_str = json.dumps(
                part.args.args_dict()  # type: ignore[union-attr]  # part.args is str|dict|None at type level; hasattr guard is correct at runtime
                if hasattr(part.args, "args_dict")
                else str(part.args),
                sort_keys=True,
            )
            h = hashlib.md5(f"{part.tool_name}:{args_str}".encode()).hexdigest()
            if last_hash is None:
                consecutive_same = 1
                last_hash = h
            elif h == last_hash:
                consecutive_same += 1
            else:
                return consecutive_same
    return consecutive_same


def _is_shell_error_return(part: ToolReturnPart) -> bool:
    """Return True when the tool return represents a shell command error."""
    content = part.content
    if isinstance(content, str):
        c = content.lower()
        # Require "error" at the start or the pydantic-ai ModelRetry prefix.
        # Substring match on the whole output caused false positives on text like
        # "3 tests passed, 0 errors".
        str_is_error = (
            c.startswith("error")
            or c.startswith("shell: command failed")
            or c.startswith("shell: unexpected error")
        )
    else:
        str_is_error = False
    return (isinstance(part.metadata, dict) and bool(part.metadata.get("error"))) or (
        isinstance(content, str) and part.tool_name == "shell" and str_is_error
    )


def _count_consecutive_shell_errors(messages: list[ModelMessage]) -> int:
    """Count the most-recent contiguous streak of shell command errors.

    Scans in reverse; stops at the first non-error return.
    """
    count: int = 0
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if _is_shell_error_return(part) and part.tool_name == "shell":
                count += 1
            else:
                return count
    return count


def _safety_prompt_text(ctx: RunContext[CoDeps]) -> str:
    """Per-turn dynamic instruction: doom loop and shell reflection warnings. Empty string when no condition is active."""
    deps = ctx.deps
    messages = ctx.messages
    doom_threshold = deps.config.doom_loop_threshold
    max_refl = deps.config.max_reflections

    consecutive_same = _count_consecutive_same_calls(messages)
    consecutive_shell_errors = _count_consecutive_shell_errors(messages)

    warnings: list[str] = []

    if consecutive_same >= doom_threshold:
        warnings.append(
            "You are repeating the same tool call. "
            "Try a different approach or explain why you are stuck."
        )
        log.warning("Doom loop detected: %d identical tool calls", consecutive_same)

    if consecutive_shell_errors >= max_refl:
        warnings.append(
            "Shell reflection limit reached. Ask the user for help "
            "or try a fundamentally different approach."
        )
        log.warning("Shell reflection cap: %d consecutive errors", consecutive_shell_errors)

    return "\n\n".join(warnings)
