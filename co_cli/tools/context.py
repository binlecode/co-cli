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
    """Load personality pieces for your current role.

    Your role determines which personality pieces are available.
    Each role has a character piece (identity/voice) and a style piece
    (communication format).

    Available piece types: "character", "style", "role"
    - character: Core identity and behavioral traits
    - style: Communication format (terse, warm, balanced, educational)
    - role: Full role description with examples and patterns

    Call with no pieces to load all available pieces for your role.
    Call with specific pieces when only certain personality guidance
    is relevant (e.g. just "character" for voice, just "style" for format).

    Args:
        ctx: Agent runtime context.
        pieces: Piece types to load. None loads all pieces for the role.

    Returns:
        dict with display (personality text), role name, and pieces_loaded list.
    """
    role_name = ctx.deps.personality
    if not role_name:
        return {
            "display": "No personality role configured.",
            "role": None,
            "pieces_loaded": [],
        }

    from co_cli.prompts.personalities._registry import PRESETS

    if role_name not in PRESETS:
        return {
            "display": f"Unknown role: {role_name}. Available: {', '.join(PRESETS.keys())}",
            "role": role_name,
            "pieces_loaded": [],
        }

    preset = PRESETS[role_name]
    personalities_dir = Path(__file__).parent.parent / "prompts" / "personalities"
    roles_dir = Path(__file__).parent.parent / "prompts" / "personalities" / "roles"

    # Determine which pieces are available for this role
    available_pieces: dict[str, Path] = {}

    # Character piece (optional per preset)
    if preset["character"]:
        character_path = personalities_dir / "character" / f"{preset['character']}.md"
        if character_path.exists():
            available_pieces["character"] = character_path

    # Style piece (required per preset)
    style_path = personalities_dir / "style" / f"{preset['style']}.md"
    if style_path.exists():
        available_pieces["style"] = style_path

    # Role piece (full description if it exists)
    role_path = roles_dir / f"{role_name}.md"
    if role_path.exists():
        available_pieces["role"] = role_path

    if pieces is None:
        pieces = list(available_pieces.keys())

    # Validate requested pieces
    invalid = [p for p in pieces if p not in available_pieces]
    if invalid:
        return {
            "display": (
                f"Unknown piece(s) for role '{role_name}': {', '.join(invalid)}. "
                f"Available: {', '.join(available_pieces.keys())}"
            ),
            "role": role_name,
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
        "role": role_name,
        "pieces_loaded": loaded,
    }

