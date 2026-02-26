"""Auto-triggered signal detector — CC hookify pattern adapted for Co.

Scans the post-turn message history for correction/preference signals using a
two-stage filter: a cheap keyword precheck gates an LLM mini-agent call.
The mini-agent returns a structured SignalResult; confidence determines whether
the memory is saved automatically (high) or surfaced for user approval (low).
"""

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal phrases for keyword precheck
# ---------------------------------------------------------------------------

_CORRECTION_PHRASES = [
    "don't",
    "do not",
    "stop doing",
    "stop using",
    "never",
    "avoid",
    "revert",
    "undo that",
    "not like that",
    "i didn't ask",
    "please don't",
]

_FRUSTRATED_PHRASES = [
    "why did you",
    "that's not what i",
    "that was wrong",
]

_PREFERENCE_PHRASES = [
    "i prefer",
    "please use",
    "always use",
    "use instead",
]

_ALL_PHRASES = _CORRECTION_PHRASES + _FRUSTRATED_PHRASES + _PREFERENCE_PHRASES


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


class SignalResult(BaseModel):
    """Structured output from the signal analyzer mini-agent."""

    found: bool
    candidate: str | None = None
    tag: Literal["correction", "preference"] | None = None
    confidence: Literal["high", "low"] | None = None


# ---------------------------------------------------------------------------
# Precheck — cheap substring scan, no LLM call
# ---------------------------------------------------------------------------


def _keyword_precheck(messages: list) -> bool:
    """Scan the last user message for known signal phrases.

    Reverse-scans message history for the most recent UserPromptPart.
    Returns True if any precheck phrase is found (case-insensitive).
    Cost: zero LLM calls.

    Args:
        messages: Full message history (ModelRequest / ModelResponse list).

    Returns:
        True if a signal phrase was detected in the last user message.
    """
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in reversed(msg.parts):
            if isinstance(part, UserPromptPart):
                text = (
                    part.content
                    if isinstance(part.content, str)
                    else str(part.content)
                )
                text_lower = text.lower()
                return any(phrase in text_lower for phrase in _ALL_PHRASES)
    return False


# ---------------------------------------------------------------------------
# Window builder — formats recent turns for LLM context
# ---------------------------------------------------------------------------


def _build_window(messages: list) -> str:
    """Extract recent conversation turns as plain text for the mini-agent.

    Collects User/Co turn pairs from message history, capped at 10 lines
    (covering roughly 5 turns). This gives enough context for the mini-agent
    to understand the signal without bloating the prompt.

    Args:
        messages: Full message history.

    Returns:
        Formatted string of alternating User/Co lines.
    """
    lines: list[str] = []

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    text = (
                        part.content
                        if isinstance(part.content, str)
                        else str(part.content)
                    )
                    lines.append(f"User: {text}")
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    lines.append(f"Co: {part.content}")

    # Cap at last 10 lines (~5 turns) to keep prompt small
    return "\n".join(lines[-10:])


# ---------------------------------------------------------------------------
# Mini-agent — structured signal analysis
# ---------------------------------------------------------------------------


async def analyze_for_signals(messages: list, model: Any) -> SignalResult:
    """Run the signal analyzer mini-agent on the conversation window.

    Loads the signal_analyzer.md system prompt, builds a conversation window
    from recent messages, and runs a lightweight Agent with structured output.
    Never crashes the main chat loop — exceptions return SignalResult(found=False).

    Args:
        messages: Full message history after run_turn() completes.
        model: pydantic-ai model instance from agent.model (reuses main model).

    Returns:
        SignalResult with found/candidate/tag/confidence fields.
    """
    window = _build_window(messages)
    if not window.strip():
        return SignalResult(found=False)

    try:
        prompt_path = (
            Path(__file__).parent / "prompts" / "agents" / "signal_analyzer.md"
        )
        system_prompt = prompt_path.read_text(encoding="utf-8").strip()

        signal_agent: Agent[None, SignalResult] = Agent(
            model=model,
            output_type=SignalResult,
            system_prompt=system_prompt,
        )

        result = await signal_agent.run(window)
        return result.output
    except Exception:
        logger.debug("Signal analyzer failed", exc_info=True)
        # Never crash the main chat loop
        return SignalResult(found=False)
