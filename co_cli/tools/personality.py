"""Personality helpers — pre-turn classification and system prompt injection.

Personality is delivered via two mechanisms:
- Mechanism 1: pre-turn classification (MindsetDeclaration) — the orchestrator
  calls agent.run(output_type=MindsetDeclaration) once per session before the
  main response. The model picks task type(s); _apply_mindset() loads mindset
  files internally and stores content on deps.
- Mechanism 2: always-on soul critique — loaded from souls/{role}/critique.md
  at session start and injected into every model call via @agent.system_prompt.
"""

import logging
from pathlib import Path
from typing import Annotated, Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from co_cli.tools.memory import _load_memories

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_MINDSETS_DIR = Path(__file__).parent.parent / "prompts" / "personalities" / "mindsets"

MINDSET_TYPES = Literal[
    "technical", "exploration", "debugging", "teaching", "emotional", "memory"
]


class MindsetDeclaration(BaseModel):
    task_types: Annotated[list[MINDSET_TYPES], Field(min_length=1)]


def _apply_mindset(deps: "CoDeps", task_types: list[str]) -> None:
    """Load mindset files for selected task types and store on deps.

    Called by the pre-turn orchestrator phase after MindsetDeclaration is returned.
    Not a tool — system-internal. The model classifies; this loads.
    """
    role = deps.personality
    if not role:
        return
    parts: list[str] = []
    loaded: list[str] = []
    for task_type in task_types:
        strategy_file = _MINDSETS_DIR / role / f"{task_type}.md"
        if strategy_file.exists():
            parts.append(strategy_file.read_text(encoding="utf-8").strip())
            loaded.append(task_type)
    deps.active_mindset_content = "\n\n".join(parts)
    deps.active_mindset_types = loaded
    deps.mindset_loaded = True


def _load_personality_memories() -> str:
    """Load personality-context tagged memories for system prompt injection.

    Scans ``.co-cli/knowledge/`` for entries tagged with
    ``personality-context``. Returns the top 5 (by recency) formatted as
    a ``## Learned Context`` section, or empty string if none found.

    Called by ``add_personality_memories()`` in ``agent.py`` on every turn.
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge"
    personality_memories = _load_memories(
        memory_dir, tags=["personality-context"]
    )
    if not personality_memories:
        return ""

    personality_memories.sort(
        key=lambda m: m.updated or m.created,
        reverse=True,
    )
    personality_memories = personality_memories[:5]
    lines = ["## Learned Context", ""]
    for m in personality_memories:
        lines.append(f"- {m.content}")
    return "\n".join(lines)
