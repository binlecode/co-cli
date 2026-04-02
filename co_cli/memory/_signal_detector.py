"""Auto-triggered signal detector — CC hookify pattern adapted for Co.

Scans the post-turn message history for correction/preference signals using
a structured LLM extraction call. SignalResult confidence determines whether
the memory is saved automatically (high) or surfaced for user approval (low).
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from co_cli.deps import CoServices, CoDeps
    from co_cli.display._core import Frontend

from co_cli._model_factory import ResolvedModel
from co_cli.config import ROLE_ANALYSIS, ROLE_SUMMARIZATION
from co_cli.memory._lifecycle import persist_memory as _persist_memory

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


class SignalResult(BaseModel):
    """Structured output from the signal analyzer."""

    found: bool
    candidate: str | None = None
    tag: Literal["correction", "preference"] | None = None
    confidence: Literal["high", "low"] | None = None
    inject: bool = False


# ---------------------------------------------------------------------------
# Window builder — formats recent turns for LLM context
# ---------------------------------------------------------------------------


def _build_window(messages: list) -> str:
    """Extract recent conversation turns as plain text for the signal analyzer.

    Collects User/Co turn pairs from message history, capped at 10 lines
    (covering roughly 5 turns). This gives enough context to understand the
    signal without bloating the prompt.

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


_SIGNAL_PROMPT_PATH = Path(__file__).parent / "prompts" / "signal_analyzer.md"

_signal_agent: Agent[None, SignalResult] = Agent(
    output_type=SignalResult,
    instructions=_SIGNAL_PROMPT_PATH.read_text(encoding="utf-8").strip(),
)


# ---------------------------------------------------------------------------
# Mini-agent — structured signal analysis
# ---------------------------------------------------------------------------


async def analyze_for_signals(
    messages: list,
    *,
    services: "CoServices",
) -> SignalResult:
    """Run the signal analyzer on the conversation window.

    Builds a conversation window from recent messages and runs a lightweight
    Agent with structured output. Never crashes the main chat loop — exceptions
    return SignalResult(found=False).

    Args:
        messages: Full message history after run_turn() completes.
        services: CoServices for registry lookup (ROLE_ANALYSIS model).

    Returns:
        SignalResult with found/candidate/tag/confidence fields.
    """
    window = _build_window(messages)
    if not window.strip():
        return SignalResult(found=False)

    _none_resolved = ResolvedModel(model=None, settings=None)

    try:
        rm = (
            services.model_registry.get(ROLE_ANALYSIS, _none_resolved)
            if services.model_registry else _none_resolved
        )

        result = await _signal_agent.run(window, model=rm.model, model_settings=rm.settings)
        return result.output
    except Exception:
        logger.debug("Signal analyzer failed", exc_info=True)
        # Never crash the main chat loop
        return SignalResult(found=False)


async def handle_signal(
    signal: SignalResult,
    deps: "CoDeps",
    frontend: "Frontend",
) -> None:
    """Apply admission policy then persist or prompt for a detected signal."""
    if not signal.found or not signal.candidate or not signal.tag:
        return
    if signal.tag not in deps.config.memory_auto_save_tags:
        logger.debug(
            "Memory signal suppressed by policy: tag=%s not in memory_auto_save_tags",
            signal.tag,
        )
        return
    _fallback = ResolvedModel(model=None, settings=None)
    _consolidation_resolved = (
        deps.services.model_registry.get(ROLE_SUMMARIZATION, _fallback)
        if deps.services.model_registry else _fallback
    )
    tags = [signal.tag] + (["personality-context"] if signal.inject else [])
    if signal.confidence == "high":
        await _persist_memory(
            deps, signal.candidate, tags, None,
            on_failure="skip", resolved=_consolidation_resolved,
        )
        frontend.on_status(f"Learned: {signal.candidate[:80]}")
    else:
        choice = frontend.prompt_approval(f"Worth remembering: {signal.candidate}")
        if choice in ("y", "a"):
            await _persist_memory(
                deps, signal.candidate, tags, None,
                on_failure="add", resolved=_consolidation_resolved,
            )
