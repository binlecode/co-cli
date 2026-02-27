"""Personality helpers — private functions and tools for personality delivery.

Personality is delivered via two mechanisms:
- Static system prompt: the expanded soul seed (identity + Core + Never list)
- On-demand tool: ``load_task_strategy`` loads soul-specific behavioral guidance
  for the identified task type(s) at the start of each new task

This module provides both the system prompt helper and the ``load_task_strategy`` tool.
"""

import logging
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.tools.memory import _load_memories

logger = logging.getLogger(__name__)

_STRATEGIES_DIR = Path(__file__).parent.parent / "prompts" / "personalities" / "strategies"


def _load_personality_memories() -> str:
    """Load personality-context tagged memories for system prompt injection.

    Scans ``.co-cli/knowledge/memories/`` for entries tagged with
    ``personality-context``. Returns the top 5 (by recency) formatted as
    a ``## Learned Context`` section, or empty string if none found.

    Called by ``add_personality_memories()`` in ``agent.py`` on every turn.
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
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


async def load_task_strategy(
    ctx: RunContext[CoDeps],
    task_types: list[str],
) -> dict[str, Any]:
    """Load soul-specific behavioral strategy for the current task type(s).

    Call at the start of a new task to load behavioral guidance shaped by your soul.
    The same task type produces different guidance per personality — finch's teaching
    strategy is preparation-and-understanding focused; jeff's is collaborative-discovery
    focused. Multiple task types can be active simultaneously — pass all that apply.
    Unknown task types are silently skipped.

    Args:
        task_types: One or more task type tokens:
            technical   — implementation, commands, file ops, tool use
            exploration — research, tradeoffs, open-ended investigation
            debugging   — isolate, hypothesize, verify
            teaching    — explain concepts, guide toward understanding
            emotional   — user is frustrated, stuck, or celebrating
            memory      — save, recall, or manage memories and learned context

    Returns:
        display: merged strategy content for the active task types
        loaded: list of task types successfully loaded
        count: number of strategy files loaded
    """
    role = ctx.deps.personality
    if not role:
        return {"display": "", "loaded": [], "count": 0}

    parts: list[str] = []
    loaded: list[str] = []

    for task_type in task_types:
        strategy_file = _STRATEGIES_DIR / role / f"{task_type}.md"
        if strategy_file.exists():
            parts.append(strategy_file.read_text(encoding="utf-8").strip())
            loaded.append(task_type)

    display = "\n\n".join(parts) if parts else ""
    return {"display": display, "loaded": loaded, "count": len(loaded)}
