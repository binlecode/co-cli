"""Compose personality from two orthogonal axes: character + style.

Character axis: WHO you are — identity, philosophy, behavioral patterns, markers.
Style axis: HOW you communicate — format, length, structure, emoji policy.

Override precedence: style wins on format, character wins on identity.

Loads markdown axis files and joins them into a single personality prompt.
"""

from pathlib import Path

from co_cli.prompts.personalities._registry import PRESETS


_PERSONALITIES_DIR = Path(__file__).parent


def get_soul_seed(name: str) -> str:
    """Return the always-on personality fingerprint for a preset.

    Loads from ``seed/{name}.md``. The soul seed is a compact
    (2-3 sentence) personality summary injected into the system prompt
    on every turn — ensures Co has a consistent voice without needing
    to call ``load_personality()``.

    Args:
        name: Personality preset name (e.g., "finch", "terse").

    Returns:
        Soul seed text.

    Raises:
        KeyError: If name is not a registered preset.
        FileNotFoundError: If the seed file is missing.
    """
    # Validate preset exists
    _ = PRESETS[name]
    seed_file = _PERSONALITIES_DIR / "seed" / f"{name}.md"
    if not seed_file.exists():
        raise FileNotFoundError(f"Soul seed not found: {seed_file}")
    return seed_file.read_text(encoding="utf-8").strip()


def compose_personality(name: str) -> str:
    """Compose a personality prompt from its axis files.

    Looks up the preset by name, loads the character axis (if any) and
    the style axis, then joins them. Character is loaded first (identity
    context), style second (format rules that take precedence on format).

    Args:
        name: Personality preset name (e.g., "finch", "terse").

    Returns:
        Composed personality prompt text.

    Raises:
        KeyError: If name is not a registered preset.
        FileNotFoundError: If an axis file is missing.
    """
    preset = PRESETS[name]

    parts: list[str] = []

    # Load character axis — WHO: identity, philosophy, markers (optional)
    character = preset["character"]
    if character:
        character_file = _PERSONALITIES_DIR / "character" / f"{character}.md"
        if not character_file.exists():
            raise FileNotFoundError(f"Character axis not found: {character_file}")
        parts.append(character_file.read_text(encoding="utf-8").strip())

    # Load style axis — HOW: format, length, structure (required)
    # Style is loaded second; on format conflicts, style wins
    style = preset["style"]
    style_file = _PERSONALITIES_DIR / "style" / f"{style}.md"
    if not style_file.exists():
        raise FileNotFoundError(f"Style axis not found: {style_file}")
    parts.append(style_file.read_text(encoding="utf-8").strip())

    return "\n\n".join(parts)


def compose_style_only(name: str) -> str:
    """Load only the style axis of a personality (skip character).

    Used by tier 2 models where character content is too expensive
    but style guidance (format, length, structure) is still valuable.

    Args:
        name: Personality preset name (e.g., "finch", "terse").

    Returns:
        Style-only personality prompt text.

    Raises:
        KeyError: If name is not a registered preset.
        FileNotFoundError: If the style axis file is missing.
    """
    preset = PRESETS[name]
    style = preset["style"]
    style_file = _PERSONALITIES_DIR / "style" / f"{style}.md"
    if not style_file.exists():
        raise FileNotFoundError(f"Style axis not found: {style_file}")
    return style_file.read_text(encoding="utf-8").strip()
