"""Decay candidate identification for the memory lifecycle.

Pure selection logic — callers (e.g. the dream-cycle decay sweep)
archive the returned artifacts. Pinned and decay-protected entries are immune.
See ``docs/specs/dream.md`` for the dream lifecycle model.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from co_cli.config.memory import MemorySettings
from co_cli.memory.item import MemoryItem, load_memory_items

logger = logging.getLogger(__name__)


def find_decay_candidates(
    memory_dir: Path,
    config: MemorySettings,
) -> list[MemoryItem]:
    """Return items eligible for automated decay.

    Filters applied in order:
    1. Exclude: ``decay_protected`` is True
    2. Include only: ``created`` older than ``config.decay_after_days``
    3. Include only: ``last_recalled is None`` OR ``last_recalled`` older than
       ``config.decay_after_days``

    Result is sorted by age descending (oldest ``created`` first).
    """
    cutoff = datetime.now(UTC) - timedelta(days=config.decay_after_days)
    items = load_memory_items(memory_dir)

    candidates: list[MemoryItem] = []
    for item in items:
        if item.decay_protected:
            continue

        created_at = _parse_iso8601(item.created_at)
        if created_at is None or created_at >= cutoff:
            continue

        if item.last_recalled_at is not None:
            recalled_at = _parse_iso8601(item.last_recalled_at)
            if recalled_at is not None and recalled_at >= cutoff:
                continue

        candidates.append(item)

    candidates.sort(key=lambda art: _parse_iso8601(art.created_at) or cutoff)
    return candidates


def _parse_iso8601(value: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp string; return None on any failure."""
    if value is None:
        return None
    normalised = value.strip()
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        logger.warning("Unparsable ISO8601 timestamp: %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
