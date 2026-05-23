"""Decay candidate identification for the memory lifecycle.

Pure selection logic — callers (e.g. the daemon's decay phase in
``co_cli/daemons/dream/_housekeeping.py``) archive the returned items.
Pinned (``decay_protected``) and recently-recalled entries are immune.
See ``docs/specs/dream.md`` for the housekeeping lifecycle model.
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
    1. Exclude: ``decay_protected`` is True (pin)
    2. Include only: ``created_at`` older than ``config.decay_after_days``
    3. Exclude: ``last_recalled_at`` is set and within
       ``config.recall_protection_days`` (recent recall protects from decay)

    Items that never recalled (``last_recalled_at is None``) fall through to
    decay once past ``decay_after_days``. Sort order: oldest ``created_at`` first.
    """
    now = datetime.now(UTC)
    age_cutoff = now - timedelta(days=config.decay_after_days)
    recall_cutoff = now - timedelta(days=config.recall_protection_days)
    items = load_memory_items(memory_dir)

    candidates: list[MemoryItem] = []
    for item in items:
        if item.decay_protected:
            continue

        created_at = _parse_iso8601(item.created_at)
        if created_at is None or created_at >= age_cutoff:
            continue

        if item.last_recalled_at is not None:
            recalled_at = _parse_iso8601(item.last_recalled_at)
            if recalled_at is not None and recalled_at >= recall_cutoff:
                continue

        candidates.append(item)

    candidates.sort(key=lambda art: _parse_iso8601(art.created_at) or age_cutoff)
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
