"""Personality discovery and validation for the file-driven personality system.

Discovers valid personalities from the souls/ folder structure and validates
that required soul/mindset files are present.

Callers:
  co_cli.config.core — uses VALID_PERSONALITIES and validate_personality_files at config-parse time
"""

from pathlib import Path

_SOULS_DIR = Path(__file__).parent / "souls"

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
    if not _SOULS_DIR.is_dir():
        return []
    return sorted(p.name for p in _SOULS_DIR.iterdir() if p.is_dir() and (p / "seed.md").exists())


VALID_PERSONALITIES: list[str] = _discover_valid_personalities()


def validate_personality_files(role: str) -> list[str]:
    """Return non-blocking warnings for missing soul/mindset files.

    Validation is defensive and never raises — callers can surface warnings at
    startup while continuing with degraded behavior.
    """
    warnings: list[str] = []

    seed_file = _SOULS_DIR / role / "seed.md"
    if not seed_file.exists():
        warnings.append(f"Personality '{role}' missing soul seed: souls/{role}/seed.md")

    for task_type in REQUIRED_MINDSET_TASK_TYPES:
        mindset_file = _SOULS_DIR / role / "mindsets" / f"{task_type}.md"
        if not mindset_file.exists():
            warnings.append(
                f"Personality '{role}' missing mindset file: souls/{role}/mindsets/{task_type}.md"
            )

    return warnings
