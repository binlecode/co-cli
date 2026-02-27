"""Soul loading and personality discovery for the file-driven personality system.

Personality is assembled from two sources:
- ``souls/{role}/seed.md``                    — identity declaration, trait essence, constraints
- ``strategies/{role}/{task_type}.md``        — task-specific behavioral guidance (tool-loaded on demand)

The folder structure IS the schema. Adding a role requires only files, no Python changes.
"""

from pathlib import Path


_PERSONALITIES_DIR = Path(__file__).parent
REQUIRED_STRATEGY_TASK_TYPES: tuple[str, ...] = (
    "technical",
    "exploration",
    "debugging",
    "teaching",
    "emotional",
    "memory",
)


def _discover_valid_personalities() -> list[str]:
    """Derive valid personality names from souls/ folder listing."""
    souls_dir = _PERSONALITIES_DIR / "souls"
    if not souls_dir.is_dir():
        return []
    return sorted(
        p.name for p in souls_dir.iterdir()
        if p.is_dir() and (p / "seed.md").exists()
    )


VALID_PERSONALITIES: list[str] = _discover_valid_personalities()


def validate_personality_files(role: str) -> list[str]:
    """Return non-blocking warnings for missing soul/strategy files.

    Validation is defensive and never raises — callers can surface warnings at
    startup while continuing with degraded behavior.
    """
    warnings: list[str] = []

    seed_file = _PERSONALITIES_DIR / "souls" / role / "seed.md"
    if not seed_file.exists():
        warnings.append(
            f"Personality '{role}' missing soul seed: souls/{role}/seed.md"
        )

    for task_type in REQUIRED_STRATEGY_TASK_TYPES:
        strategy_file = _PERSONALITIES_DIR / "strategies" / role / f"{task_type}.md"
        if not strategy_file.exists():
            warnings.append(
                "Personality "
                f"'{role}' missing strategy file: strategies/{role}/{task_type}.md"
            )

    return warnings


def load_soul_seed(role: str) -> str:
    """Load the static soul anchor for a role.

    The seed is the complete static identity anchor: identity declaration,
    distilled trait essence, and hard constraints (Never list). Placed at the
    top of the static system prompt via ``get_agent(personality=…)`` so the
    model's first context is always the soul.

    Behavioral guidance for specific task types is loaded on demand via the
    ``load_task_strategy`` tool, not injected here.

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        Full seed text from ``souls/{role}/seed.md``.

    Raises:
        FileNotFoundError: If the seed file is missing.
    """
    seed_file = _PERSONALITIES_DIR / "souls" / role / "seed.md"
    if not seed_file.exists():
        raise FileNotFoundError(f"Soul seed file not found: {seed_file}")
    return seed_file.read_text(encoding="utf-8").strip()
