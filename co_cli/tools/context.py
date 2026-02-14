"""Context-loading tools — personality pieces."""

import logging
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


async def load_personality(
    ctx: RunContext[CoDeps],
    pieces: list[str] | None = None,
) -> dict[str, Any]:
    """Load personality pieces for your current preset.

    Each preset maps to two orthogonal axes:
    - character: WHO you are — identity, philosophy, behavioral patterns
    - style: HOW you communicate — format, length, structure

    When axes conflict, style wins on format (length, structure, emoji),
    character wins on identity (voice, markers, philosophy).

    Call with no pieces to load all available pieces for your preset.
    Call with specific pieces when only certain guidance is relevant
    (e.g. just "style" for format rules, just "character" for voice).

    Args:
        ctx: Agent runtime context.
        pieces: Axis types to load: "character", "style". None loads all.

    Returns:
        dict with display (personality text), preset name, and pieces_loaded list.
    """
    preset_name = ctx.deps.personality
    if not preset_name:
        return {
            "display": "No personality preset configured.",
            "preset": None,
            "pieces_loaded": [],
        }

    from co_cli.prompts.personalities._registry import PRESETS

    if preset_name not in PRESETS:
        return {
            "display": f"Unknown preset: {preset_name}. Available: {', '.join(PRESETS.keys())}",
            "preset": preset_name,
            "pieces_loaded": [],
        }

    preset = PRESETS[preset_name]
    personalities_dir = Path(__file__).parent.parent / "prompts" / "personalities"

    # Two orthogonal axes: character (WHO) and style (HOW)
    available_pieces: dict[str, Path] = {}

    # Character axis — identity, philosophy, behavioral patterns (optional)
    if preset["character"]:
        character_path = personalities_dir / "character" / f"{preset['character']}.md"
        if character_path.exists():
            available_pieces["character"] = character_path

    # Style axis — format, length, structure (required)
    style_path = personalities_dir / "style" / f"{preset['style']}.md"
    if style_path.exists():
        available_pieces["style"] = style_path

    if pieces is None:
        pieces = list(available_pieces.keys())

    # Validate requested axes
    invalid = [p for p in pieces if p not in available_pieces]
    if invalid:
        return {
            "display": (
                f"Unknown axis for preset '{preset_name}': {', '.join(invalid)}. "
                f"Available axes: {', '.join(available_pieces.keys())}"
            ),
            "preset": preset_name,
            "pieces_loaded": [],
        }

    parts: list[str] = []
    loaded: list[str] = []
    for piece_name in pieces:
        path = available_pieces[piece_name]
        content = path.read_text(encoding="utf-8").strip()
        parts.append(content)
        loaded.append(piece_name)

    combined = "\n\n".join(parts)
    return {
        "display": combined,
        "preset": preset_name,
        "pieces_loaded": loaded,
    }

