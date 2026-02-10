"""Compose personality from orthogonal aspects (character + style).

Loads markdown aspect files and joins them into a single personality prompt.
"""

from pathlib import Path

from co_cli.prompts.personalities._registry import PRESETS


_ASPECTS_DIR = Path(__file__).parent / "aspects"


def compose_personality(name: str) -> str:
    """Compose a personality prompt from its aspect files.

    Looks up the preset by name, loads the character aspect (if any) and
    the style aspect, then joins them with a double newline.

    Args:
        name: Personality preset name (e.g., "finch", "terse").

    Returns:
        Composed personality prompt text.

    Raises:
        KeyError: If name is not a registered preset.
        FileNotFoundError: If an aspect file is missing.
    """
    preset = PRESETS[name]

    parts: list[str] = []

    # Load character aspect (optional)
    character = preset["character"]
    if character:
        character_file = _ASPECTS_DIR / "character" / f"{character}.md"
        if not character_file.exists():
            raise FileNotFoundError(f"Character aspect not found: {character_file}")
        parts.append(character_file.read_text(encoding="utf-8").strip())

    # Load style aspect (required)
    style = preset["style"]
    style_file = _ASPECTS_DIR / "style" / f"{style}.md"
    if not style_file.exists():
        raise FileNotFoundError(f"Style aspect not found: {style_file}")
    parts.append(style_file.read_text(encoding="utf-8").strip())

    return "\n\n".join(parts)
