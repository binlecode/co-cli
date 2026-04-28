"""Dynamic instruction implementations called via ``agent.instructions()`` wrappers in ``agent/_instructions.py``.

These functions produce per-turn system-instruction text and are never
appended to ``message_history`` — they drive the ``instructions`` field that
pydantic-ai re-emits each turn.

Functions:
    recall_prompt_text   — async, returns today's date (volatile suffix only)
    safety_prompt_text   — sync, returns doom-loop / shell-reflection warnings
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from co_cli.deps import CoDeps

log = logging.getLogger(__name__)


async def recall_prompt_text(ctx: RunContext[CoDeps]) -> str:
    """Per-turn dynamic instruction: today's date.

    Personality memories are injected in the static system prompt via
    ``build_static_instructions()`` for prefix-cache stability. Knowledge
    recall is on-demand via the ``memory_search`` tool.
    """
    return f"Today is {date.today().isoformat()}."


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
            hashed = hashlib.md5(f"{part.tool_name}:{args_str}".encode()).hexdigest()
            if last_hash is None:
                consecutive_same = 1
                last_hash = hashed
            elif hashed == last_hash:
                consecutive_same += 1
            else:
                return consecutive_same
    return consecutive_same


def _is_shell_error_return(part: ToolReturnPart) -> bool:
    """Return True when the tool return represents a shell command error."""
    content = part.content
    if isinstance(content, str):
        lowered = content.lower()
        str_is_error = (
            lowered.startswith("error")
            or lowered.startswith("shell: command failed")
            or lowered.startswith("shell: unexpected error")
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


def safety_prompt_text(ctx: RunContext[CoDeps]) -> str:
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
