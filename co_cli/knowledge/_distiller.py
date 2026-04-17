"""Post-turn knowledge extractor — writes durable signals into the knowledge layer.

Scans the post-turn message history (including tool calls and results) for
reusable artifacts across four categories — preferences, feedback, rules,
references — and calls ``save_knowledge`` for each. Cursor-based delta
prevents re-scanning already-extracted turns.
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.display._core import Frontend

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.config._llm import NOREASON_SETTINGS
from co_cli.tools.knowledge import save_knowledge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Window builder — formats delta turns for LLM context
# ---------------------------------------------------------------------------


def _tag_messages(messages: list) -> list[tuple[int, str, str]]:
    """Tag each extractable part in ``messages`` as ``(idx, kind, line)``.

    Walks ``ModelRequest`` and ``ModelResponse`` entries and flattens their
    parts into a tagged stream. ``idx`` preserves original turn order so
    callers can impose independent caps on ``text`` vs. ``tool`` entries and
    merge them back in order. Skips Read-tool output and oversized non-prose
    tool results. Shared by the per-turn extractor and the dream-cycle miner.
    """
    tagged: list[tuple[int, str, str]] = []
    idx = 0

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    text = part.content if isinstance(part.content, str) else str(part.content)
                    tagged.append((idx, "text", f"User: {text}"))
                    idx += 1
                elif isinstance(part, ToolReturnPart):
                    content = part.content if isinstance(part.content, str) else str(part.content)
                    if content.startswith("1\u2192 "):
                        continue
                    if len(content) > 1000 and not any(
                        ch in content[:200] for ch in (".", "!", "?")
                    ):
                        continue
                    tagged.append(
                        (idx, "tool", f"Tool result ({part.tool_name}): {content[:300]}")
                    )
                    idx += 1
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    tagged.append((idx, "text", f"Co: {part.content}"))
                    idx += 1
                elif isinstance(part, ToolCallPart):
                    args_str = str(part.args)[:200]
                    tagged.append((idx, "tool", f"Tool({part.tool_name}): {args_str}"))
                    idx += 1

    return tagged


def build_transcript_window(messages: list, *, max_text: int = 10, max_tool: int = 10) -> str:
    """Extract conversation turns from a message list as plain text.

    Shared by the per-turn extractor and the dream-cycle miner.
    Collects User/Co text lines and interleaved tool call/return lines.
    Caps at max ``max_text`` text lines and max ``max_tool`` tool lines,
    then merges back in original turn order.

    Args:
        messages: Message list (delta slice or full transcript).
        max_text: Maximum number of text lines to include (default 10).
        max_tool: Maximum number of tool lines to include (default 10).

    Returns:
        Formatted string of interleaved User/Co and tool lines.
    """
    tagged = _tag_messages(messages)

    text_entries = [(orig_idx, line) for orig_idx, kind, line in tagged if kind == "text"]
    tool_entries = [(orig_idx, line) for orig_idx, kind, line in tagged if kind == "tool"]

    merged = text_entries[-max_text:] + tool_entries[-max_tool:]
    merged.sort(key=lambda entry: entry[0])

    return "\n".join(line for _, line in merged)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "knowledge_extractor.md"

# Module-level knowledge extractor agent — tool-calling pattern.
# No model at init; model passed at .run() time (same as prior _extraction_agent pattern).
_knowledge_extractor_agent: Agent["CoDeps", str] = Agent(
    instructions=_PROMPT_PATH.read_text(encoding="utf-8").strip(),
    tools=[save_knowledge],
)


# ---------------------------------------------------------------------------
# Fire-and-forget async extraction
# ---------------------------------------------------------------------------

_in_flight: asyncio.Task[None] | None = None


async def _run_extraction_async(
    delta: list,
    deps: "CoDeps",
    frontend: "Frontend",
    *,
    cursor_start: int,
) -> None:
    """Background extraction: run _knowledge_extractor_agent on the delta window.

    Advances deps.session.last_extracted_message_idx on success only.
    Handles CancelledError for clean shutdown.
    Never crashes the main chat loop.
    """
    from opentelemetry import trace as otel_trace

    tracer = otel_trace.get_tracer("co.memory")
    try:
        window = build_transcript_window(delta)
        if not window.strip():
            deps.session.last_extracted_message_idx = cursor_start + len(delta)
            return
        _model = deps.model.model if deps.model else None
        with tracer.start_as_current_span("co.memory.extraction"):
            await _knowledge_extractor_agent.run(
                window, deps=deps, model=_model, model_settings=NOREASON_SETTINGS
            )
        deps.session.last_extracted_message_idx = cursor_start + len(delta)
    except asyncio.CancelledError:
        logger.debug("Background memory extraction cancelled")
    except Exception:
        logger.debug("Background memory extraction failed", exc_info=True)


def _on_extraction_done(task: asyncio.Task[None]) -> None:
    """Callback to clear _in_flight and suppress unhandled exception warnings."""
    global _in_flight
    _in_flight = None
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            logger.debug("Extraction task exception: %s", exc)


def fire_and_forget_extraction(
    delta: list,
    deps: "CoDeps",
    frontend: "Frontend",
    *,
    cursor_start: int,
) -> None:
    """Launch extraction as a background task. Skips if one is already running."""
    global _in_flight
    if _in_flight is not None and not _in_flight.done():
        logger.debug("Extraction already in progress, skipping")
        return

    _in_flight = asyncio.get_running_loop().create_task(
        _run_extraction_async(delta, deps, frontend, cursor_start=cursor_start),
        name="memory_extraction",
    )
    _in_flight.add_done_callback(_on_extraction_done)


async def drain_pending_extraction(timeout_ms: int = 10_000) -> None:
    """Await the in-flight extraction task with a timeout. Cancel on timeout."""
    global _in_flight
    task = _in_flight
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_ms / 1000)
    except TimeoutError:
        logger.debug("Drain timeout — cancelling extraction")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except Exception:
        logger.debug("Drain failed", exc_info=True)
    finally:
        _in_flight = None
