"""Hash-based deduplication of identical tool results.

Collapses repeat returns of the same ``(tool_name, content)`` pair — e.g.
five ``file_read`` calls of the same unchanged file — into one full copy
plus back-reference markers for earlier identical returns. Runs before
``truncate_tool_results`` so the kept recent window is shrunk, not only
the older-than-5 range that M2a collapses to per-tool semantic markers.

Dedup is orthogonal to semantic markers: dedup replaces
*identical-to-more-recent* regardless of recency; M2a replaces
*older-than-the-5-most-recent* regardless of content. Running dedup first
prevents identical copies inside the kept window from each carrying full
tokens.
"""

from __future__ import annotations

import hashlib

from pydantic_ai.messages import ToolReturnPart

from co_cli.tools.categories import COMPACTABLE_TOOLS

_DEDUP_MIN_CHARS: int = 200
"""Skip content shorter than this: a back-reference marker is larger than the payload.

Matches the threshold used by peer implementations (hermes-agent) for
identical parity — avoids inflating tiny tool returns and keeps the marker
a net savings in every triggered case.
"""


def _content_hash(content: str) -> str:
    """16-hex-char SHA-256 prefix. 64 bits of entropy — collision-free at session scale.

    Matches the prefix length used by ``persist_if_oversized`` in
    ``co_cli/tools/tool_io.py`` for disk-level content addressing, keeping
    hashing conventions consistent across the codebase.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def is_dedup_candidate(part: ToolReturnPart) -> bool:
    """True when a tool return is eligible for hash-based dedup.

    Gates on ``COMPACTABLE_TOOLS`` membership (same gate M2a uses), string
    content (hashable, non-multimodal), and the ``_DEDUP_MIN_CHARS`` floor.
    """
    return (
        part.tool_name in COMPACTABLE_TOOLS
        and isinstance(part.content, str)
        and len(part.content) >= _DEDUP_MIN_CHARS
    )


def dedup_key(part: ToolReturnPart) -> str:
    """Stable dedup key: ``tool_name:sha256_prefix(content)``.

    Caller must pre-check ``is_dedup_candidate(part)`` — this function
    assumes ``part.content`` is a string.
    """
    return f"{part.tool_name}:{_content_hash(part.content)}"  # type: ignore[arg-type]


def build_dedup_part(part: ToolReturnPart, latest_call_id: str) -> ToolReturnPart:
    """Back-reference replacement for a tool return whose content duplicates a more recent one.

    Preserves the original ``tool_call_id`` so the ToolCallPart ↔ ToolReturnPart
    pairing stays valid. The replacement content names the canonical
    ``latest_call_id`` so the model can trace the live copy if needed.
    """
    return ToolReturnPart(
        tool_name=part.tool_name,
        content=(
            f"[Duplicate tool output — identical to more recent "
            f"{part.tool_name} call (call_id={latest_call_id})]"
        ),
        tool_call_id=part.tool_call_id,
    )
