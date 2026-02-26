"""Compose personality from file-driven soul + traits + behaviors.

Personality is assembled from three file types:
- ``souls/{role}.md`` — 2-3 sentence voice fingerprint
- ``traits/{role}.md`` — key: value trait wiring (5 traits)
- ``behaviors/{trait}-{value}.md`` — behavioral guidance per trait value

No Python dict, no TypedDict. The folder structure IS the schema.
"""

from pathlib import Path


_PERSONALITIES_DIR = Path(__file__).parent

_ADOPTION_MANDATE = (
    "Adopt this persona fully — it overrides your default "
    "personality and communication patterns.\n"
    "Your personality shapes how you follow the rules below. "
    "It never overrides safety or factual accuracy."
)


def _discover_valid_personalities() -> list[str]:
    """Derive valid personality names from traits/ folder listing."""
    traits_dir = _PERSONALITIES_DIR / "traits"
    if not traits_dir.is_dir():
        return []
    return sorted(p.stem for p in traits_dir.glob("*.md"))


VALID_PERSONALITIES: list[str] = _discover_valid_personalities()


def load_soul(role: str) -> str:
    """Load the voice fingerprint for a role.

    Args:
        role: Personality role name (e.g., "finch", "terse").

    Returns:
        Soul text (2-3 sentences).

    Raises:
        FileNotFoundError: If the soul file is missing.
    """
    soul_file = _PERSONALITIES_DIR / "souls" / f"{role}.md"
    if not soul_file.exists():
        raise FileNotFoundError(f"Soul file not found: {soul_file}")
    return soul_file.read_text(encoding="utf-8").strip()


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


def compose_personality(role: str, depth: str = "normal") -> str:
    """Compose the full personality block from soul + behavior files + mandate.

    Assembly:
    1. Load ``souls/{role}.md`` — identity basis + voice fingerprint + anti-patterns
    2. Load ``traits/{role}.md`` — parse key: value pairs into a fresh dict
    3. Apply ``_DEPTH_OVERRIDES[depth]`` — mutate trait dict before file loading
    4. For each trait (with overrides applied): load ``behaviors/{key}-{value}.md``
    5. Concatenate: soul + all behavior contents + adoption mandate

    Args:
        role: Personality role name.
        depth: User reasoning depth intent — ``"quick"``, ``"normal"``, or ``"deep"``.
            Overrides specific trait values before behavior file selection.
            Defaults to ``"normal"`` (no overrides, role defaults apply).

    Returns:
        Complete ``## Soul`` block ready for system prompt injection.
    """
    from co_cli.prompts._reasoning_depth_override import _DEPTH_OVERRIDES

    parts: list[str] = []

    # Identity basis + voice fingerprint + anti-patterns
    parts.append(load_soul(role))

    # Apply depth overrides before behavior file selection
    traits = load_traits(role)
    traits.update(_DEPTH_OVERRIDES.get(depth, {}))

    # Behavior files for each trait (uses overridden values)
    for trait_name, trait_value in traits.items():
        behavior_file = (
            _PERSONALITIES_DIR / "behaviors" / f"{trait_name}-{trait_value}.md"
        )
        if behavior_file.exists():
            parts.append(behavior_file.read_text(encoding="utf-8").strip())

    # Adoption mandate
    parts.append(_ADOPTION_MANDATE)

    return "## Soul\n\n" + "\n\n".join(parts)
