"""Soul loading for the file-driven personality system.

Loads soul seeds, critiques, and mindsets at agent construction time.

Personality is assembled from these sources in this order:
- ``souls/{role}/seed.md``                    — identity declaration, trait essence, constraints
- ``souls/{role}/mindsets/{task_type}.md``     — task-specific behavioral guidance (static)
- ``rules/01..05_*.md``                       — behavioral rules (assembled by build_static_instructions)

Character memories (``souls/{role}/memories/*.md``) are NOT loaded here. They are
surfaced on demand via the canon channel in ``memory_search`` (see
``co_cli/tools/memory/_canon_recall.py``).

Each role is fully self-contained under ``souls/{role}/``. Adding a role requires
only a new directory with the required files — no Python changes.

Callers:
  co_cli.context.assembly — uses load_soul_seed, load_soul_mindsets
                            inside build_static_instructions(), invoked by build_agent()
  co_cli.agent.core       — uses load_soul_critique to append critique after toolset guidance
"""

from pathlib import Path

from co_cli.personality.prompts.validator import REQUIRED_MINDSET_TASK_TYPES

_SOULS_DIR = Path(__file__).parent / "souls"


def load_soul_seed(role: str) -> str:
    """Load the static soul anchor for a role.

    The seed is the complete static identity anchor: identity declaration,
    distilled trait essence, and hard constraints (Never list). Placed at the
    top of the static system prompt via ``build_agent(personality=…)`` so the
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
    seed_file = _SOULS_DIR / role / "seed.md"
    if not seed_file.exists():
        raise FileNotFoundError(f"Soul seed file not found: {seed_file}")
    return seed_file.read_text(encoding="utf-8").strip()


def load_soul_critique(role: str) -> str:
    """Load the always-on interpretive critique frame for a role (optional).

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        Critique text from ``souls/{role}/critique.md``, or empty string if absent.
    """
    critique_file = _SOULS_DIR / role / "critique.md"
    if not critique_file.exists():
        return ""
    return critique_file.read_text(encoding="utf-8").strip()


def load_soul_mindsets(role: str) -> str:
    """Load all 6 task-type mindset files for a role into a static block.

    All mindset files for the active role are loaded at agent creation time
    so the model sees complete task-type guidance from Turn 1, regardless of
    how the conversation evolves.

    Skips missing files silently to degrade gracefully when a mindset file is absent.

    Args:
        role: Personality role name (e.g., "finch", "jeff").

    Returns:
        ``## Mindsets`` block with all found task-type files joined, or empty
        string if none found.
    """
    mindsets_dir = _SOULS_DIR / role / "mindsets"
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
