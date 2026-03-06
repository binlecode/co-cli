"""Soul loading and personality discovery for the file-driven personality system.

Personality is assembled from five sources in this order:
- ``souls/{role}/seed.md``                    — identity declaration, trait essence, constraints
- ``.co-cli/knowledge/``                     — character base memories (decay_protected, provenance=planted)
- ``mindsets/{role}/{task_type}.md``          — task-specific behavioral guidance (static, loaded at agent creation)
- ``rules/01..05_*.md``                       — behavioral rules (assembled by assemble_prompt)
- ``souls/{role}/examples.md``               — concrete response patterns (optional, trailing rules)

The folder structure IS the schema. Adding a role requires only files, no Python changes.
"""

from pathlib import Path

from co_cli._frontmatter import parse_frontmatter


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
    return sorted(
        p.name for p in souls_dir.iterdir()
        if p.is_dir() and (p / "seed.md").exists()
    )


VALID_PERSONALITIES: list[str] = _discover_valid_personalities()


def validate_personality_files(role: str) -> list[str]:
    """Return non-blocking warnings for missing soul/mindset files.

    Validation is defensive and never raises — callers can surface warnings at
    startup while continuing with degraded behavior.
    """
    warnings: list[str] = []

    seed_file = _PERSONALITIES_DIR / "souls" / role / "seed.md"
    if not seed_file.exists():
        warnings.append(
            f"Personality '{role}' missing soul seed: souls/{role}/seed.md"
        )

    for task_type in REQUIRED_MINDSET_TASK_TYPES:
        mindset_file = _PERSONALITIES_DIR / "mindsets" / role / f"{task_type}.md"
        if not mindset_file.exists():
            warnings.append(
                "Personality "
                f"'{role}' missing mindset file: mindsets/{role}/{task_type}.md"
            )

    return warnings


def load_soul_seed(role: str) -> str:
    """Load the static soul anchor for a role.

    The seed is the complete static identity anchor: identity declaration,
    distilled trait essence, and hard constraints (Never list). Placed at the
    top of the static system prompt via ``get_agent(personality=…)`` so the
    model's first context is always the soul.

    Behavioral guidance for specific task types is loaded statically via
    ``load_soul_mindsets`` and folded into the soul block at agent creation.

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


def load_soul_examples(role: str) -> str:
    """Load concrete response pattern examples for a role (optional).

    Examples trail the behavioral rules in the static system prompt — they are
    the last identity-level content the model reads before model-specific quirks.
    This placement follows common few-shot practice: show the pattern closest to
    the task so the model pattern-matches from the most recently seen examples.

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        Examples text from ``souls/{role}/examples.md``, or empty string if absent.
    """
    examples_file = _PERSONALITIES_DIR / "souls" / role / "examples.md"
    if not examples_file.exists():
        return ""
    return examples_file.read_text(encoding="utf-8").strip()


def load_soul_critique(role: str) -> str:
    """Load the always-on interpretive critique frame for a role (optional).

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        Critique text from ``souls/{role}/critique.md``, or empty string if absent.
    """
    critique_file = _PERSONALITIES_DIR / "souls" / role / "critique.md"
    if not critique_file.exists():
        return ""
    return critique_file.read_text(encoding="utf-8").strip()


def load_character_memories(role: str, memory_dir: Path) -> str:
    """Load character base memories for the given role from the knowledge store.

    Scans memory_dir for entries tagged with both the role name and "character".
    These are pre-planted, decay-protected entries carrying the felt layer of the
    character — scenes, speech patterns, behavioral observations from source material.

    Args:
        role: Personality role name (e.g., "finch", "jeff").
        memory_dir: Path to .co-cli/knowledge/.

    Returns:
        Formatted memory block (``## Character`` header + prose entries), or empty
        string if the directory is absent or no matching entries are found.
    """
    if not memory_dir.exists():
        return ""

    entries: list[str] = []
    for path in sorted(memory_dir.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)
            tags = fm.get("tags", [])
            if role in tags and "character" in tags:
                text = body.strip()
                if text:
                    entries.append(text)
        except Exception:
            continue

    if not entries:
        return ""

    return "## Character\n\n" + "\n\n".join(entries)


def load_soul_mindsets(role: str) -> str:
    """Load all 6 task-type mindset files for a role into a static block.

    All mindset files for the active role are loaded at agent creation time
    so the model sees complete task-type guidance from Turn 1, regardless of
    how the conversation evolves.

    Skips missing files silently — consistent with existing degraded-but-functional
    policy in ``load_character_memories`` and ``load_soul_examples``.

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        ``## Mindsets`` block with all found task-type files joined, or empty
        string if none found.
    """
    mindsets_dir = _PERSONALITIES_DIR / "mindsets" / role
    parts: list[str] = []
    for task_type in REQUIRED_MINDSET_TASK_TYPES:
        mindset_file = mindsets_dir / f"{task_type}.md"
        if mindset_file.exists():
            content = mindset_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    if not parts:
        return ""
    return "## Mindsets\n\n" + "\n\n".join(parts)
