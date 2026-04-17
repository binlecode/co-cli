"""Decay candidate identification for the knowledge lifecycle.

Pure selection logic — callers (e.g. the dream-cycle decay sweep in TASK-5.6)
archive the returned artifacts. Pinned and decay-protected entries are immune.
See ``docs/specs/cognition.md`` for the lifecycle model.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from co_cli.config._knowledge import KnowledgeSettings
from co_cli.knowledge._artifact import KnowledgeArtifact, load_knowledge_artifacts

logger = logging.getLogger(__name__)


def find_decay_candidates(
    knowledge_dir: Path,
    config: KnowledgeSettings,
) -> list[KnowledgeArtifact]:
    """Return artifacts eligible for automated decay.

    Filters applied in order:
    1. Exclude: ``decay_protected`` is True
    2. Include only: ``created`` older than ``config.decay_after_days``
    3. Include only: ``last_recalled is None`` OR ``last_recalled`` older than
       ``config.decay_after_days``

    Result is sorted by age descending (oldest ``created`` first).
    """
    cutoff = datetime.now(UTC) - timedelta(days=config.decay_after_days)
    artifacts = load_knowledge_artifacts(knowledge_dir)

    candidates: list[KnowledgeArtifact] = []
    for artifact in artifacts:
        if artifact.decay_protected:
            continue

        created_at = _parse_iso8601(artifact.created)
        if created_at is None or created_at >= cutoff:
            continue

        if artifact.last_recalled is not None:
            recalled_at = _parse_iso8601(artifact.last_recalled)
            if recalled_at is not None and recalled_at >= cutoff:
                continue

        candidates.append(artifact)

    candidates.sort(key=lambda art: _parse_iso8601(art.created) or cutoff)
    return candidates


def _parse_iso8601(value: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp string; return None on any failure.

    Accepts the trailing ``Z`` suffix by normalising it to ``+00:00`` before
    delegation to ``datetime.fromisoformat``. Naive timestamps are treated as
    UTC so they compare correctly with an aware cutoff.
    """
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
