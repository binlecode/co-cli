"""Memory retention policy — cut-only overflow management.

Applies capacity management for the knowledge store: when total memories
exceed the cap, oldest non-protected entries are deleted until under cap.
No summary memories are created — cut-only is deterministic and reversible.
"""

import logging
from typing import Any

from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


async def enforce_retention(
    deps: CoDeps,
    all_memories: list,
) -> dict[str, Any]:
    """Delete oldest non-protected memories until total is within the cap.

    Triggered when total memories strictly exceed `memory_max_count`.
    Protected entries (decay_protected=True) are never deleted.

    Args:
        deps: CoDeps with memory_max_count scalar.
        all_memories: Already-loaded list of all memories.

    Returns:
        dict with keys: decayed (count deleted), strategy ("cut").
    """
    total_count = len(all_memories)
    excess = total_count - deps.config.memory.max_count

    if excess <= 0:
        return {"decayed": 0, "strategy": "cut"}

    oldest = sorted(
        [m for m in all_memories if not m.decay_protected],
        key=lambda m: m.created,
    )[:excess]

    deleted = 0
    for m in oldest:
        try:
            m.path.unlink()
            logger.info(f"Deleted memory {m.id} during retention cut")
            deleted += 1
        except Exception as e:
            logger.warning(f"Failed to delete memory {m.id} during retention cut: {e}")

    return {"decayed": deleted, "strategy": "cut"}
