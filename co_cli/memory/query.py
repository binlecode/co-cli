"""Knowledge domain query helpers: filtering and formatting for artifact display."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from co_cli.memory.artifact import KnowledgeArtifact


def _apply_memory_filters(
    entries: list[KnowledgeArtifact], filters: dict[str, Any]
) -> list[KnowledgeArtifact]:
    """Apply older_than_days filter to a loaded artifact list.

    ``kind`` is applied upstream via ``load_knowledge_artifacts(artifact_kinds=...)``
    and is not re-applied here.
    """
    result = entries
    if "older_than_days" in filters:
        cutoff_days = filters["older_than_days"]
        now = datetime.now(UTC)
        result = [
            m
            for m in result
            if (now - datetime.fromisoformat(m.created.replace("Z", "+00:00"))).days > cutoff_days
        ]
    return result


def _format_memory_row(m: KnowledgeArtifact) -> str:
    id_prefix = m.id[:8]
    created = m.created[:10]
    snippet = m.content[:80]
    return f"{id_prefix}  {created}  [{m.artifact_kind}]  {snippet}"
