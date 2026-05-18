"""Memory service layer — pure CRUD functions, no RunContext or agent dependencies.

Provides save_memory_item and mutate_memory_item as the canonical write path for all
memory mutations. Tool wrappers acquire resource locks before calling these
functions, then call reindex explicitly after a successful write.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from co_cli.fileio.atomic import atomic_write_text
from co_cli.memory.chunker import chunk_text
from co_cli.memory.frontmatter import (
    memory_item_to_frontmatter,
    parse_frontmatter,
    render_frontmatter,
    render_memory_item_file,
)
from co_cli.memory.item import (
    MemoryItem,
    MemoryKindEnum,
    SourceTypeEnum,
    load_memory_item,
    load_memory_items,
)
from co_cli.memory.similarity import find_similar_memory_items, is_content_superset
from co_cli.memory.store import MEMORY_SOURCE

if TYPE_CHECKING:
    from co_cli.index.store import IndexStore

logger = logging.getLogger(__name__)

_LINE_PREFIX_RE = re.compile(r"(?:^|\n)\d+→ ")
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")


@dataclass
class SaveResult:
    """Outcome of save_memory_item — describes what was written (or skipped)."""

    path: Path
    artifact_id: str
    action: Literal["saved", "skipped", "merged", "appended"]
    content: str
    frontmatter_dict: dict
    markdown_content: str
    filename_stem: str


@dataclass
class MutateResult:
    """Outcome of mutate_memory_item — describes what was changed."""

    path: Path
    filename_stem: str
    action: Literal["appended", "replaced"]
    updated_body: str
    markdown_content: str
    frontmatter: dict


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _find_by_filename_stem(memory_dir: Path, filename_stem: str) -> Path | None:
    return next((p for p in memory_dir.glob("*.md") if p.stem == filename_stem), None)


def _find_article_by_url(
    memory_dir: Path,
    origin_url: str,
    index_store: "IndexStore | None" = None,
) -> Path | None:
    if index_store is not None:
        result = index_store.find_by_source_ref(origin_url, MEMORY_SOURCE)
        return Path(result) if result else None
    if not memory_dir.exists():
        return None
    for path in memory_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            if origin_url not in raw:
                continue
            frontmatter, _ = parse_frontmatter(raw)
            if frontmatter.get("source_ref") == origin_url:
                return path
        except Exception:
            continue
    return None


def reindex(
    index_store: "IndexStore | None",
    path: Path,
    body: str,
    markdown_content: str,
    frontmatter: dict,
    filename_stem: str,
    *,
    chunk_tokens: int,
    chunk_overlap_tokens: int,
) -> None:
    """Re-index a single memory file in the index store without RunContext."""
    if index_store is None:
        return
    content_hash = hashlib.sha256(markdown_content.encode()).hexdigest()
    memory_kind = frontmatter.get("memory_kind", MemoryKindEnum.NOTE.value)
    with index_store.transaction() as tx:
        tx.upsert(
            source=MEMORY_SOURCE,
            kind=memory_kind,
            path=str(path),
            title=frontmatter.get("title") or filename_stem,
            mtime=path.stat().st_mtime,
            hash=content_hash,
            created=frontmatter.get("created"),
            description=frontmatter.get("description"),
            source_ref=frontmatter.get("source_ref"),
            artifact_id=str(frontmatter["id"]) if frontmatter.get("id") is not None else None,
        )
        chunks = chunk_text(
            body.strip(), chunk_tokens=chunk_tokens, overlap_tokens=chunk_overlap_tokens
        )
        tx.index_chunks(MEMORY_SOURCE, str(path), chunks)


def save_memory_item(
    memory_dir: Path,
    *,
    content: str,
    memory_kind: str,
    title: str | None = None,
    description: str | None = None,
    source_url: str | None = None,
    source_type: str = SourceTypeEnum.DETECTED.value,
    decay_protected: bool = False,
    consolidation_enabled: bool = False,
    consolidation_similarity_threshold: float = 0.75,
    index_store: "IndexStore | None" = None,
) -> SaveResult:
    """Save or consolidate a memory item. Pure — no RunContext.

    Three dispatch paths:
    - source_url set → URL-keyed dedup (web articles); decay_protected forced True.
    - consolidation_enabled → Jaccard dedup; near-identical skipped, overlapping merged.
    - else → straight create.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)

    if source_url is not None:
        existing_path = _find_article_by_url(memory_dir, source_url, index_store)

        if existing_path is not None:
            try:
                existing = load_memory_item(existing_path)
            except (ValueError, OSError) as exc:
                logger.warning(
                    "Cannot load existing article at %s: %s — creating new", existing_path, exc
                )
                existing = None

        if existing_path is not None and existing is not None:
            item = MemoryItem(
                id=existing.id,
                path=existing_path,
                memory_kind=MemoryKindEnum.ARTICLE.value,
                title=title or existing.title or existing_path.stem,
                content=content,
                created=existing.created,
                updated=datetime.now(UTC).isoformat(),
                related=list(existing.related),
                source_type=SourceTypeEnum.WEB_FETCH.value,
                source_ref=source_url,
                decay_protected=True,
            )
            markdown_content = render_memory_item_file(item)
            atomic_write_text(existing_path, markdown_content)
            return SaveResult(
                path=existing_path,
                artifact_id=existing.id,
                action="merged",
                content=content,
                frontmatter_dict=memory_item_to_frontmatter(item),
                markdown_content=markdown_content,
                filename_stem=existing_path.stem,
            )

        artifact_id = str(uuid4())
        slug = slugify((title or content)[:50])
        filename = f"{slug}-{artifact_id[:8]}.md"
        file_path = memory_dir / filename
        item = MemoryItem(
            id=artifact_id,
            path=file_path,
            memory_kind=MemoryKindEnum.ARTICLE.value,
            title=title,
            content=content,
            created=datetime.now(UTC).isoformat(),
            source_type=SourceTypeEnum.WEB_FETCH.value,
            source_ref=source_url,
            decay_protected=True,
        )
        markdown_content = render_memory_item_file(item)
        atomic_write_text(file_path, markdown_content)
        return SaveResult(
            path=file_path,
            artifact_id=artifact_id,
            action="saved",
            content=content,
            frontmatter_dict=memory_item_to_frontmatter(item),
            markdown_content=markdown_content,
            filename_stem=file_path.stem,
        )

    if consolidation_enabled:
        threshold = consolidation_similarity_threshold
        existing = load_memory_items(
            memory_dir,
            memory_kinds=[memory_kind] if memory_kind is not None else None,
        )
        matches = find_similar_memory_items(content, memory_kind, existing, threshold)
        if matches:
            best_item, best_score = matches[0]
            if best_score > 0.9:
                return SaveResult(
                    path=best_item.path,
                    artifact_id=best_item.id,
                    action="skipped",
                    content=content,
                    frontmatter_dict={},
                    markdown_content="",
                    filename_stem=best_item.path.stem,
                )
            if is_content_superset(content, best_item.content):
                dedup_action: Literal["merged", "appended"] = "merged"
                merged_body = content
            else:
                dedup_action = "appended"
                merged_body = best_item.content.rstrip() + "\n" + content
            raw = best_item.path.read_text(encoding="utf-8")
            frontmatter, _ = parse_frontmatter(raw)
            frontmatter["updated"] = datetime.now(UTC).isoformat()
            markdown_content = render_frontmatter(frontmatter, merged_body)
            atomic_write_text(best_item.path, markdown_content)
            return SaveResult(
                path=best_item.path,
                artifact_id=best_item.id,
                action=dedup_action,
                content=merged_body,
                frontmatter_dict=frontmatter,
                markdown_content=markdown_content,
                filename_stem=best_item.path.stem,
            )

    artifact_id = str(uuid4())
    slug = slugify(title) if title else slugify(content[:50])
    filename = f"{slug}-{artifact_id[:8]}.md"
    file_path = memory_dir / filename

    item = MemoryItem(
        id=artifact_id,
        path=file_path,
        memory_kind=memory_kind,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        description=description,
        source_type=source_type,
        decay_protected=decay_protected,
    )
    markdown_content = render_memory_item_file(item)
    atomic_write_text(file_path, markdown_content)
    return SaveResult(
        path=file_path,
        artifact_id=artifact_id,
        action="saved",
        content=content,
        frontmatter_dict=memory_item_to_frontmatter(item),
        markdown_content=markdown_content,
        filename_stem=file_path.stem,
    )


def mutate_memory_item(
    memory_dir: Path,
    *,
    filename_stem: str,
    action: Literal["append", "replace"],
    content: str,
    target: str = "",
) -> MutateResult:
    """Append or surgically replace a passage in an existing memory item."""
    for s, name in ((content, "content"), (target, "target")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1→ ' or 'Line N: '). "
                "Strip them before calling mutate_memory_item."
            )

    match_path = _find_by_filename_stem(memory_dir, filename_stem)
    if match_path is None:
        raise FileNotFoundError(f"Memory item '{filename_stem}' not found")

    raw = match_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)

    if action == "append":
        updated_body = body.rstrip() + "\n" + content
        result_action: Literal["appended", "replaced"] = "appended"
    else:
        if not target:
            raise ValueError(
                "mutate_memory_item action='replace' requires a non-empty target. "
                "Provide the exact text passage to replace."
            )
        count = body.count(target)
        if count == 0:
            raise ValueError(
                f"target not found in artifact '{filename_stem}'. "
                "Check for exact match (case-sensitive, whitespace-sensitive)."
            )
        if count > 1:
            raise ValueError(
                f"target appears {count} times in '{filename_stem}'. "
                "Provide more context to make it unique."
            )
        updated_body = body.replace(target, content, 1)
        result_action = "replaced"

    frontmatter["updated"] = datetime.now(UTC).isoformat()
    markdown_content = render_frontmatter(frontmatter, updated_body)
    atomic_write_text(match_path, markdown_content)

    return MutateResult(
        path=match_path,
        filename_stem=filename_stem,
        action=result_action,
        updated_body=updated_body,
        markdown_content=markdown_content,
        frontmatter=frontmatter,
    )
