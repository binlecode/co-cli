"""Compose personality from file-driven soul + traits + behaviors.

Personality is assembled from three file types:
- ``souls/{role}/seed.md`` — identity declaration ("You are X…")
- ``souls/{role}/body.md`` — behavioral detail sections (## Never, ## Voice, etc.)
- ``traits/{role}.md``     — key: value trait wiring (4 traits)
- ``behaviors/{trait}-{value}.md`` — behavioral guidance per trait value

No Python dict, no TypedDict. The folder structure IS the schema.
"""

from pathlib import Path


_PERSONALITIES_DIR = Path(__file__).parent

def _discover_valid_personalities() -> list[str]:
    """Derive valid personality names from traits/ folder listing."""
    traits_dir = _PERSONALITIES_DIR / "traits"
    if not traits_dir.is_dir():
        return []
    return sorted(p.stem for p in traits_dir.glob("*.md"))


VALID_PERSONALITIES: list[str] = _discover_valid_personalities()


def load_soul_seed(role: str) -> str:
    """Load the identity declaration for a role.

    The seed is the most fundamental part of the soul: the "You are X" statement
    that anchors the agent's identity. It belongs at the top of the static system
    prompt so the model's first context is the soul, not a generic label.

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        Identity declaration text from ``souls/{role}/seed.md``.

    Raises:
        FileNotFoundError: If the seed file is missing.
    """
    seed_file = _PERSONALITIES_DIR / "souls" / role / "seed.md"
    if not seed_file.exists():
        raise FileNotFoundError(f"Soul seed file not found: {seed_file}")
    return seed_file.read_text(encoding="utf-8").strip()


def _load_soul_body(role: str) -> str:
    """Load the soul body — the ## sections that provide behavioral detail.

    The seed (identity declaration) lives in the static system prompt via
    ``load_soul_seed()``. This returns only the behavioral detail sections
    (``## Never``, ``## Voice``, etc.) so the per-turn ``## Soul`` block
    does not repeat the seed.

    Returns empty string if ``souls/{role}/body.md`` does not exist.
    """
    body_file = _PERSONALITIES_DIR / "souls" / role / "body.md"
    if not body_file.exists():
        return ""
    return body_file.read_text(encoding="utf-8").strip()


def load_soul(role: str) -> str:
    """Load the full soul — seed + body combined.

    Useful for diagnostics and debug tools that need to display
    the complete soul content in one block.

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        Full soul text: seed paragraph + body sections.

    Raises:
        FileNotFoundError: If the seed file is missing.
    """
    seed = load_soul_seed(role)
    body = _load_soul_body(role)
    if body:
        return seed + "\n\n" + body
    return seed


def load_traits(role: str) -> dict[str, str]:
    """Parse trait wiring from a role's traits file.

    Each line is ``key: value`` (e.g., ``communication: balanced``).
    Blank lines and lines without ``:`` are skipped.

    Args:
        role: Personality role name.

    Returns:
        Dict mapping trait name to value (e.g., {"communication": "balanced"}).

    Raises:
        FileNotFoundError: If the traits file is missing.
    """
    traits_file = _PERSONALITIES_DIR / "traits" / f"{role}.md"
    if not traits_file.exists():
        raise FileNotFoundError(f"Traits file not found: {traits_file}")
    traits: dict[str, str] = {}
    for line in traits_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            traits[key] = value
    return traits


def compose_personality(role: str) -> str:
    """Compose the per-turn personality block from soul body + behavior files.

    The soul seed (identity declaration) is already in the static system prompt
    via ``load_soul_seed()`` — it is not repeated here.

    Assembly:
    1. Load soul body from ``souls/{role}/body.md`` (## Never, ## Voice, etc.)
    2. Load ``traits/{role}.md`` — parse key: value pairs into a fresh dict
    3. For each trait: load ``behaviors/{key}-{value}.md``
    4. Concatenate: soul body + all behavior contents

    Args:
        role: Personality role name.

    Returns:
        ``## Soul`` block (body + behaviors) ready for per-turn injection.
    """
    parts: list[str] = []

    # Soul body only — seed is in the static prompt, not repeated here
    body = _load_soul_body(role)
    if body:
        parts.append(body)

    traits = load_traits(role)

    # Behavior files for each trait
    for trait_name, trait_value in traits.items():
        behavior_file = (
            _PERSONALITIES_DIR / "behaviors" / f"{trait_name}-{trait_value}.md"
        )
        if behavior_file.exists():
            parts.append(behavior_file.read_text(encoding="utf-8").strip())

    return "## Soul\n\n" + "\n\n".join(parts)
