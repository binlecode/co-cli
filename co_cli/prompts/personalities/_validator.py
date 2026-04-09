"""Personality discovery and validation for the file-driven personality system.

Discovers valid personalities from the souls/ folder structure and validates
that required soul/mindset files are present.

Callers:
  config.py — uses VALID_PERSONALITIES and validate_personality_files at config-parse time
"""

from pathlib import Path

# Independent copy — this module is importable without _loader
_PERSONALITIES_DIR = Path(__file__).parent

REQUIRED_MINDSET_TASK_TYPES: tuple[str, ...] = (
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
    return sorted(p.name for p in souls_dir.iterdir() if p.is_dir() and (p / "seed.md").exists())


VALID_PERSONALITIES: list[str] = _discover_valid_personalities()


def validate_personality_files(role: str, _personalities_dir: Path | None = None) -> list[str]:
    """Return non-blocking warnings for missing soul/mindset files.

    Validation is defensive and never raises — callers can surface warnings at
    startup while continuing with degraded behavior.

    Args:
        role: Personality role name (e.g., "finch").
        _personalities_dir: Override the personalities directory. Defaults to the package directory.
    """
    personalities_dir = _personalities_dir or _PERSONALITIES_DIR
    warnings: list[str] = []

    seed_file = personalities_dir / "souls" / role / "seed.md"
    if not seed_file.exists():
        warnings.append(f"Personality '{role}' missing soul seed: souls/{role}/seed.md")

    for task_type in REQUIRED_MINDSET_TASK_TYPES:
        mindset_file = personalities_dir / "souls" / role / "mindsets" / f"{task_type}.md"
        if not mindset_file.exists():
            warnings.append(
                f"Personality '{role}' missing mindset file: souls/{role}/mindsets/{task_type}.md"
            )

    return warnings
