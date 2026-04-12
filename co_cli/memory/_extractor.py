"""Post-turn memory extractor — general-purpose memory extraction.

Scans the post-turn message history for memory-worthy content across all
4 memory types (user, feedback, project, reference). Returns up to 3
candidates per extraction. Confidence determines whether each candidate
is saved automatically (high) or surfaced for approval (low).
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.display._core import Frontend

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.memory._lifecycle import persist_memory as _persist_memory
from co_cli.memory.prompt_builders import build_extraction_user_prompt

logger = logging.getLogger(__name__)

_MAX_CANDIDATES = 3


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


class MemoryCandidate(BaseModel):
    """A single memory-worthy signal extracted from conversation."""

    name: str = ""
    candidate: str
    tag: Literal["user", "feedback", "project", "reference"]
    confidence: Literal["high", "low"]
    inject: bool = False
    description: str = ""


class ExtractionResult(BaseModel):
    """Structured output from the memory extractor — up to N candidates."""

    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=_MAX_CANDIDATES)


# ---------------------------------------------------------------------------
# Window builder — formats recent turns for LLM context
# ---------------------------------------------------------------------------


def _build_window(messages: list) -> str:
    """Extract recent conversation turns as plain text for the memory extractor.

    Collects User/Co turn pairs from message history, capped at 20 lines
    (covering roughly 10 turns). Broader window for general memory extraction.

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
                    text = part.content if isinstance(part.content, str) else str(part.content)
                    lines.append(f"User: {text}")
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    lines.append(f"Co: {part.content}")

    # Cap at last 20 lines (~10 turns) for broader extraction context
    return "\n".join(lines[-20:])


_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_extractor.md"

_extraction_agent: Agent[None, ExtractionResult] = Agent(
    output_type=ExtractionResult,
    instructions=_PROMPT_PATH.read_text(encoding="utf-8").strip(),
    retries=0,
    output_retries=0,
)


# ---------------------------------------------------------------------------
# Extraction — multi-candidate structured analysis
# ---------------------------------------------------------------------------


async def analyze_for_signals(
    messages: list,
    *,
    deps: "CoDeps",
    existing_manifest: str = "",
) -> ExtractionResult:
    """Run the memory extractor on the conversation window.

    Builds a conversation window from recent messages and runs a lightweight
    Agent with structured output. Returns up to 3 memory candidates.
    Never crashes the main chat loop — exceptions return empty result.

    When existing_manifest is non-empty, it is appended to the user prompt
    so the extractor can avoid redundant candidates.

    Args:
        messages: Full message history after run_turn() completes.
        deps: CoDeps for model access.
        existing_manifest: Pre-built manifest string of existing memories.
            When non-empty, appended to the user prompt.

    Returns:
        ExtractionResult with a list of MemoryCandidate entries.
    """
    window = _build_window(messages)
    if not window.strip():
        return ExtractionResult()

    line_count = len(window.splitlines())
    user_prompt = window + "\n\n" + build_extraction_user_prompt(line_count, existing_manifest)

    try:
        _model = deps.model.model if deps.model else None
        result = await _extraction_agent.run(
            user_prompt, model=_model, model_settings=NOREASON_SETTINGS
        )
        return result.output
    except Exception:
        logger.debug("Memory extractor failed", exc_info=True)
        return ExtractionResult()


async def _process_candidate(
    mem: MemoryCandidate,
    deps: "CoDeps",
    frontend: "Frontend",
    *,
    interactive: bool,
) -> None:
    """Process a single extracted memory candidate.

    Handles tag-building, admission gate, and persistence. All dedup routing
    is delegated to persist_memory (which runs the save agent with proper locking).
    When interactive=True, low-confidence candidates are surfaced for user approval.
    When interactive=False (background), low-confidence candidates are logged and skipped.
    """
    tags = [mem.tag] + (["personality-context"] if mem.inject else [])
    auto_save_allowed = mem.tag in deps.config.memory.auto_save_tags
    _model = deps.model.model if deps.model else None

    if mem.confidence == "high" and auto_save_allowed:
        await _persist_memory(
            deps,
            mem.candidate,
            tags,
            None,
            on_failure="skip",
            model=_model,
            model_settings=NOREASON_SETTINGS,
            type_=mem.tag,
            description=mem.description or None,
            name=mem.name or None,
        )
        frontend.on_status(f"Learned: {mem.candidate[:80]}")
    elif interactive:
        choice = frontend.prompt_approval(f"Worth remembering: {mem.candidate}")
        if choice in ("y", "a"):
            await _persist_memory(
                deps,
                mem.candidate,
                tags,
                None,
                on_failure="add",
                model=_model,
                model_settings=NOREASON_SETTINGS,
                type_=mem.tag,
                description=mem.description or None,
                name=mem.name or None,
            )
    else:
        logger.debug("Deferred (async, low-confidence): %s", mem.candidate[:80])


async def handle_extraction(
    extraction: ExtractionResult,
    deps: "CoDeps",
    frontend: "Frontend",
) -> None:
    """Apply admission policy then persist or prompt for each extracted candidate.

    Admission control: memory_auto_save_tags gates which types auto-save at
    high confidence. Types not in the list require user confirmation regardless
    of confidence. Empty list = all signals require confirmation.
    """
    if not extraction.memories:
        return

    for mem in extraction.memories:
        await _process_candidate(mem, deps, frontend, interactive=True)


# ---------------------------------------------------------------------------
# Fire-and-forget async extraction
# ---------------------------------------------------------------------------

_in_flight: asyncio.Task[None] | None = None


async def _run_extraction_async(
    messages: list,
    deps: "CoDeps",
    frontend: "Frontend",
) -> None:
    """Background extraction: analyze + handle, high-confidence only.

    Low-confidence candidates are logged and skipped — no way to prompt the
    user from a background task. Handles CancelledError for clean shutdown.
    """
    try:
        from co_cli.memory._save import build_memory_manifest
        from co_cli.memory.recall import load_memories

        memories = load_memories(deps.memory_dir, kind="memory")
        memories.sort(key=lambda m: m.updated or m.created, reverse=True)
        existing_manifest = build_memory_manifest(memories)
        extraction = await analyze_for_signals(
            messages, deps=deps, existing_manifest=existing_manifest
        )
        if not extraction.memories:
            return

        for mem in extraction.memories:
            await _process_candidate(mem, deps, frontend, interactive=False)
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
    messages: list,
    deps: "CoDeps",
    frontend: "Frontend",
) -> None:
    """Launch extraction as a background task. Skips if one is already running."""
    global _in_flight
    if _in_flight is not None and not _in_flight.done():
        logger.debug("Extraction already in progress, skipping")
        return

    _in_flight = asyncio.get_running_loop().create_task(
        _run_extraction_async(messages, deps, frontend),
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
