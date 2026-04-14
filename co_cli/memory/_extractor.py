"""Post-turn memory extractor — extracts durable signals from conversation windows.

Scans the post-turn message history (including tool calls and results) for
memory-worthy signals across all 4 types (user, feedback, project, reference).
Calls save_memory for each detected signal. Cursor-based delta prevents
re-scanning already-extracted turns.
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
from co_cli.tools.memory_write import save_memory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Window builder — formats delta turns for LLM context
# ---------------------------------------------------------------------------


def _build_window(messages: list) -> str:
    """Extract conversation turns from a delta slice as plain text.

    Collects User/Co text lines and interleaved tool call/return lines.
    Caps at max 10 text lines and max 10 tool lines, then merges back
    in original turn order.

    Args:
        messages: Delta message slice (history[cursor:]).

    Returns:
        Formatted string of interleaved User/Co and tool lines.
    """
    # Each entry: (original_index, kind, line_text)
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
                    # Skip Read-tool output (line-number prefix pattern)
                    if content.startswith("1\u2192 "):
                        continue
                    # Skip very long content with no sentence boundary in the first 200 chars
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

    text_entries = [(orig_idx, line) for orig_idx, kind, line in tagged if kind == "text"]
    tool_entries = [(orig_idx, line) for orig_idx, kind, line in tagged if kind == "tool"]

    # Apply independent caps: last 10 of each, then merge in original order
    merged = text_entries[-10:] + tool_entries[-10:]
    merged.sort(key=lambda entry: entry[0])

    return "\n".join(line for _, line in merged)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_extractor.md"

# Module-level memory extractor agent — tool-calling pattern.
# No model at init; model passed at .run() time (same as prior _extraction_agent pattern).
_memory_extractor_agent: Agent["CoDeps", str] = Agent(
    instructions=_PROMPT_PATH.read_text(encoding="utf-8").strip(),
    tools=[save_memory],
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
    """Background extraction: run _memory_extractor_agent on the delta window.

    Advances deps.session.last_extracted_message_idx on success only.
    Handles CancelledError for clean shutdown.
    Never crashes the main chat loop.
    """
    from opentelemetry import trace as otel_trace

    tracer = otel_trace.get_tracer("co.memory")
    try:
        window = _build_window(delta)
        if not window.strip():
            deps.session.last_extracted_message_idx = cursor_start + len(delta)
            return
        _model = deps.model.model if deps.model else None
        with tracer.start_as_current_span("co.memory.extraction"):
            await _memory_extractor_agent.run(
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
