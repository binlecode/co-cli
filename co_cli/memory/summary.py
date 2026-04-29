"""Session summarization pipeline — FTS5 search → truncate → noreason summarize.

Provides helpers for truncating session transcripts around query matches and
summarizing them with a cheap noreason LLM call. Ported from hermes-agent
session_search_tool.py with adaptations for pydantic-ai message types.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.direct import model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

MAX_SESSION_CHARS = 100_000
_TOOL_OUTPUT_TRUNCATION_THRESHOLD = 500
_TOOL_OUTPUT_HEAD = 250
_TOOL_OUTPUT_TAIL = 250
_SESSION_PROXIMITY_WINDOW_CHARS = 200
_SESSION_SUMMARIZER_MAX_RETRIES = 3

_PROMPT_PATH = Path(__file__).parent / "prompts" / "session_summarizer.md"


def _find_match_positions(text_lower: str, query_lower: str) -> list[int]:
    """Find character positions where the query matches in text (three-tier strategy)."""
    # 1. Full-phrase search
    positions = [m.start() for m in re.finditer(re.escape(query_lower), text_lower)]
    if positions:
        return positions

    # 2. Proximity co-occurrence of all terms (within 200 chars)
    terms = query_lower.split()
    if len(terms) > 1:
        term_pos: dict[str, list[int]] = {
            t: [m.start() for m in re.finditer(re.escape(t), text_lower)] for t in terms
        }
        rarest = min(terms, key=lambda t: len(term_pos.get(t, [])))
        for pos in term_pos.get(rarest, []):
            if all(
                any(abs(p - pos) < _SESSION_PROXIMITY_WINDOW_CHARS for p in term_pos.get(t, []))
                for t in terms
                if t != rarest
            ):
                positions.append(pos)
        if positions:
            return positions

    # 3. Individual term positions (last resort)
    for t in terms:
        for m in re.finditer(re.escape(t), text_lower):
            positions.append(m.start())
    return positions


def _best_window_start(match_positions: list[int], text_len: int, max_chars: int) -> int:
    """Find the window start that covers the most match positions."""
    best_start = 0
    best_count = 0
    for candidate in match_positions:
        ws = max(0, candidate - max_chars // 4)  # bias: 25% before, 75% after
        we = ws + max_chars
        if we > text_len:
            ws = max(0, text_len - max_chars)
        count = sum(1 for p in match_positions if ws <= p < ws + max_chars)
        if count > best_count:
            best_count = count
            best_start = ws
    return best_start


def _truncate_around_matches(
    full_text: str,
    query: str,
    max_chars: int = MAX_SESSION_CHARS,
) -> str:
    """Truncate a conversation transcript to max_chars around query match positions.

    Three-tier match strategy: full phrase → proximity co-occurrence → individual terms.
    Window biases 25% before / 75% after the chosen match anchor.
    """
    if len(full_text) <= max_chars:
        return full_text

    text_lower = full_text.lower()
    match_positions = _find_match_positions(text_lower, query.lower().strip())

    if not match_positions:
        suffix = "\n\n...[later conversation truncated]..." if max_chars < len(full_text) else ""
        return full_text[:max_chars] + suffix

    match_positions.sort()
    start = _best_window_start(match_positions, len(full_text), max_chars)
    end = min(len(full_text), start + max_chars)
    prefix = "...[earlier conversation truncated]...\n\n" if start > 0 else ""
    suffix = "\n\n...[later conversation truncated]..." if end < len(full_text) else ""
    return prefix + full_text[start:end] + suffix


def _render_user_prompt(content: Any) -> str:
    """Extract text from a UserPromptPart content (str or multi-modal list)."""
    if isinstance(content, list):
        return " ".join(
            sub.get("text", "")
            for sub in content
            if isinstance(sub, dict) and sub.get("type") == "text"
        )
    return content or ""


def _render_tool_return(part: ToolReturnPart) -> str:
    """Render ToolReturnPart with head+tail truncation for long outputs."""
    content = part.content or ""
    if len(content) > _TOOL_OUTPUT_TRUNCATION_THRESHOLD:
        content = (
            content[:_TOOL_OUTPUT_HEAD] + "\n...[truncated]...\n" + content[-_TOOL_OUTPUT_TAIL:]
        )
    return f"[TOOL:{part.tool_name}]: {content}"


def _format_conversation(messages: list[ModelMessage]) -> str:
    """Format a pydantic-ai ModelMessage list into a readable transcript for summarization.

    Rendering rules:
    - UserPromptPart → [USER]: content
    - TextPart (assistant) → [ASSISTANT]: content
    - ToolCallPart → [ASSISTANT][Called: tool_name]  (args never inlined)
    - ToolReturnPart → [TOOL:tool_name]: content  (head+tail truncation at 500 chars)
    - SystemPromptPart, RetryPromptPart, ThinkingPart → skipped
    """
    parts: list[str] = []

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    rendered = _render_user_prompt(part.content)
                    if rendered:
                        parts.append(f"[USER]: {rendered}")
                elif isinstance(part, ToolReturnPart):
                    parts.append(_render_tool_return(part))
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    parts.append(f"[ASSISTANT][Called: {part.tool_name}]")
                elif isinstance(part, TextPart) and part.content:
                    parts.append(f"[ASSISTANT]: {part.content}")

    return "\n\n".join(parts)


async def summarize_session_around_query(
    window: str,
    query: str,
    session_meta: dict[str, Any],
    deps: CoDeps,
) -> str | None:
    """Summarize a pre-formatted/truncated session window focused on a search query.

    Expects window to already be formatted via _format_conversation() and
    truncated via _truncate_around_matches(). The query is bookended in the
    prompt (before and after the transcript) so the model sees focus on both ends.

    3-attempt retry with linear backoff on transient errors or empty responses.
    Returns None on unrecoverable failure or after all retries exhausted.
    """
    if deps.model is None:
        logger.warning("No model available for session summarization")
        return None

    session_id = session_meta.get("session_id", "unknown")
    when = session_meta.get("when", "unknown")

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    user_prompt = (
        f"Search topic: {query}\n"
        f"Session ID: {session_id}\n"
        f"Session date: {when}\n\n"
        f"CONVERSATION TRANSCRIPT:\n{window}\n\n"
        f"Summarize this conversation with focus on: {query}"
    )

    max_retries = _SESSION_SUMMARIZER_MAX_RETRIES
    for attempt in range(max_retries):
        try:
            response = await model_request(
                deps.model.model,
                [
                    ModelRequest(parts=[SystemPromptPart(content=system_prompt)]),
                    ModelRequest.user_text_prompt(user_prompt),
                ],
                model_settings=deps.model.settings_noreason,
            )
            content = "".join(p.content for p in response.parts if isinstance(p, TextPart))
            if content and content.strip():
                return content.strip()
            logger.warning(
                "Session summarizer returned empty content (attempt %d/%d)",
                attempt + 1,
                max_retries,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(attempt + 1)
        except RuntimeError:
            logger.warning("No model available for session summarization")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(attempt + 1)
            else:
                logger.warning(
                    "Session summarization failed after %d attempts: %s",
                    max_retries,
                    e,
                    exc_info=True,
                )
                return None

    return None
