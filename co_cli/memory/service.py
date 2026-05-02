"""Knowledge service layer — pure CRUD functions, no RunContext or agent dependencies.

Provides save_artifact and mutate_artifact as the canonical write path for all
knowledge mutations. Tool wrappers acquire resource locks before calling these
functions; reindexing is triggered here when memory_store is provided.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from co_cli.memory.artifact import (
    ArtifactKindEnum,
    IndexSourceEnum,
    KnowledgeArtifact,
    SourceTypeEnum,
    load_knowledge_artifacts,
)
from co_cli.memory.chunker import chunk_text
from co_cli.memory.frontmatter import (
    parse_frontmatter,
    render_frontmatter,
    render_knowledge_file,
)
from co_cli.memory.mutator import _atomic_write
from co_cli.memory.similarity import find_similar_artifacts, is_content_superset

if TYPE_CHECKING:
    from co_cli.memory.memory_store import MemoryStore

_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+→ ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")

# Match config defaults — used when memory_store reindexing is triggered without config access.
_DEFAULT_CHUNK_SIZE = 600
_DEFAULT_CHUNK_OVERLAP = 80


@dataclass
class SaveResult:
    """Outcome of save_artifact — describes what was written (or skipped)."""

    path: Path
    artifact_id: str
    action: Literal["saved", "skipped", "merged", "appended"]
    content: str
    fm_dict: dict
    slug: str


@dataclass
class MutateResult:
    """Outcome of mutate_artifact — describes what was changed."""

    path: Path
    slug: str
    action: Literal["appended", "replaced"]
    updated_body: str
    fm: dict


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _find_by_slug(knowledge_dir: Path, slug: str) -> Path | None:
    return next((p for p in knowledge_dir.glob("*.md") if p.stem == slug), None)


def _find_article_by_url(knowledge_dir: Path, origin_url: str) -> Path | None:
    if not knowledge_dir.exists():
        return None
    for path in knowledge_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            if origin_url not in raw:
                continue
            fm, _ = parse_frontmatter(raw)
            if fm.get("source_ref") == origin_url:
                return path
        except Exception:
            continue
    return None


def _reindex(
    store: "MemoryStore",
    path: Path,
    body: str,
    md_content: str,
    fm: dict,
    slug: str,
) -> None:
    """Re-index a single knowledge file in the FTS store without RunContext."""
    content_hash = hashlib.sha256(md_content.encode()).hexdigest()
    artifact_kind = fm.get("artifact_kind", ArtifactKindEnum.NOTE.value)
    store.index(
        source=IndexSourceEnum.KNOWLEDGE,
        kind=artifact_kind,
        path=str(path),
        title=fm.get("title") or slug,
        content=body.strip(),
        mtime=path.stat().st_mtime,
        hash=content_hash,
        tags=" ".join(fm.get("tags", [])) or None,
        created=fm.get("created"),
        type=artifact_kind,
        description=fm.get("description"),
        source_ref=fm.get("source_ref"),
        artifact_id=str(fm["id"]) if fm.get("id") is not None else None,
    )
    chunks = chunk_text(
        body.strip(), chunk_size=_DEFAULT_CHUNK_SIZE, overlap=_DEFAULT_CHUNK_OVERLAP
    )
    store.index_chunks(IndexSourceEnum.KNOWLEDGE, str(path), chunks)


def save_artifact(
    knowledge_dir: Path,
    *,
    content: str,
    artifact_kind: str,
    title: str | None = None,
    description: str | None = None,
    source_url: str | None = None,
    source_type: str = SourceTypeEnum.DETECTED.value,
    source_ref: str | None = None,
    decay_protected: bool = False,
    related: list[str] | None = None,
    consolidation_enabled: bool = False,
    consolidation_similarity_threshold: float = 0.75,
    memory_store: "MemoryStore | None" = None,
) -> SaveResult:
    """Save or consolidate a knowledge artifact. Pure — no RunContext.

    Three dispatch paths:
    - source_url set → URL-keyed dedup (web articles); decay_protected forced True.
    - consolidation_enabled → Jaccard dedup; near-identical skipped, overlapping merged.
    - else → straight create.
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    # URL-keyed dedup path (web-fetched articles)
    if source_url is not None:
        effective_decay = True  # web articles are always decay-protected
        existing_path = _find_article_by_url(knowledge_dir, source_url)

        if existing_path is not None:
            raw = existing_path.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(raw)
            artifact_id = str(fm.get("id") or "")
            created = fm.get("created") or datetime.now(UTC).isoformat()
            artifact = KnowledgeArtifact(
                id=artifact_id,
                path=existing_path,
                artifact_kind=ArtifactKindEnum.ARTICLE.value,
                title=title or fm.get("title", existing_path.stem),
                content=content,
                created=created,
                updated=datetime.now(UTC).isoformat(),
                related=list(fm.get("related") or []),
                source_type=SourceTypeEnum.WEB_FETCH.value,
                source_ref=source_url,
                decay_protected=effective_decay,
            )
            md_content = render_knowledge_file(artifact)
            _atomic_write(existing_path, md_content)
            fm_dict = {
                "artifact_kind": ArtifactKindEnum.ARTICLE.value,
                "title": artifact.title,
                "created": created,
                "source_ref": source_url,
                "id": fm.get("id"),
            }
            if memory_store is not None:
                _reindex(
                    memory_store,
                    existing_path,
                    content,
                    md_content,
                    fm_dict,
                    existing_path.stem,
                )
            return SaveResult(
                path=existing_path,
                artifact_id=artifact_id,
                action="merged",
                content=content,
                fm_dict=fm_dict,
                slug=existing_path.stem,
            )

        # New web article
        artifact_id = str(uuid4())
        slug = _slugify((title or content)[:50])
        filename = f"{slug}-{artifact_id[:6]}.md"
        file_path = knowledge_dir / filename
        artifact = KnowledgeArtifact(
            id=artifact_id,
            path=file_path,
            artifact_kind=ArtifactKindEnum.ARTICLE.value,
            title=title,
            content=content,
            created=datetime.now(UTC).isoformat(),
            related=list(related or []),
            source_type=SourceTypeEnum.WEB_FETCH.value,
            source_ref=source_url,
            decay_protected=effective_decay,
        )
        md_content = render_knowledge_file(artifact)
        _atomic_write(file_path, md_content)
        fm_dict = {
            "artifact_kind": ArtifactKindEnum.ARTICLE.value,
            "title": title,
            "created": artifact.created,
            "source_ref": source_url,
            "id": artifact_id,
        }
        if memory_store is not None:
            _reindex(memory_store, file_path, content, md_content, fm_dict, file_path.stem)
        return SaveResult(
            path=file_path,
            artifact_id=artifact_id,
            action="saved",
            content=content,
            fm_dict=fm_dict,
            slug=file_path.stem,
        )

    # Jaccard dedup path
    if consolidation_enabled:
        threshold = consolidation_similarity_threshold
        existing = load_knowledge_artifacts(
            knowledge_dir,
            artifact_kinds=[artifact_kind] if artifact_kind is not None else None,
        )
        matches = find_similar_artifacts(content, artifact_kind, existing, threshold)
        if matches:
            best_artifact, best_score = matches[0]
            if best_score > 0.9:
                # Near-identical — skip without writing
                return SaveResult(
                    path=best_artifact.path,
                    artifact_id=best_artifact.id,
                    action="skipped",
                    content=content,
                    fm_dict={},
                    slug=best_artifact.path.stem,
                )
            if is_content_superset(content, best_artifact.content):
                dedup_action: Literal["merged", "appended"] = "merged"
                merged_body = content
            else:
                dedup_action = "appended"
                merged_body = best_artifact.content.rstrip() + "\n" + content
            raw = best_artifact.path.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(raw)
            fm["updated"] = datetime.now(UTC).isoformat()
            md_content = render_frontmatter(fm, merged_body)
            _atomic_write(best_artifact.path, md_content)
            if memory_store is not None:
                _reindex(
                    memory_store,
                    best_artifact.path,
                    merged_body,
                    md_content,
                    fm,
                    best_artifact.path.stem,
                )
            return SaveResult(
                path=best_artifact.path,
                artifact_id=best_artifact.id,
                action=dedup_action,
                content=merged_body,
                fm_dict=fm,
                slug=best_artifact.path.stem,
            )

    # Straight create
    artifact_id = str(uuid4())
    slug = _slugify(title) if title else _slugify(content[:50])
    filename = f"{slug}-{artifact_id[:8]}.md"
    file_path = knowledge_dir / filename

    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=file_path,
        artifact_kind=artifact_kind,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        description=description,
        source_type=source_type,
        source_ref=source_ref,
        decay_protected=decay_protected,
    )
    md_content = render_knowledge_file(artifact)
    fm_dict = {
        "artifact_kind": artifact_kind,
        "title": artifact.title,
        "created": artifact.created,
        "description": artifact.description,
        "id": artifact_id,
    }
    _atomic_write(file_path, md_content)
    if memory_store is not None:
        _reindex(memory_store, file_path, content, md_content, fm_dict, file_path.stem)
    return SaveResult(
        path=file_path,
        artifact_id=artifact_id,
        action="saved",
        content=content,
        fm_dict=fm_dict,
        slug=file_path.stem,
    )


def mutate_artifact(
    knowledge_dir: Path,
    *,
    slug: str,
    action: Literal["append", "replace"],
    content: str,
    target: str = "",
    memory_store: "MemoryStore | None" = None,
) -> MutateResult:
    """Append or surgically replace a passage in an existing knowledge artifact.

    Guards applied before any I/O:
    - Rejects content / target containing Read-tool line-number prefixes.
    - For replace: target must appear exactly once in the body.
    - For replace: empty target is rejected (ambiguous).

    Reindexes via memory_store if provided.
    """
    for s, name in ((content, "content"), (target, "target")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1→ ' or 'Line N: '). "
                "Strip them before calling mutate_artifact."
            )

    match_path = _find_by_slug(knowledge_dir, slug)
    if match_path is None:
        raise FileNotFoundError(f"Knowledge artifact '{slug}' not found")

    raw = match_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)

    if action == "append":
        updated_body = body.rstrip() + "\n" + content
        result_action: Literal["appended", "replaced"] = "appended"
    else:
        if not target:
            raise ValueError(
                "mutate_artifact action='replace' requires a non-empty target. "
                "Provide the exact text passage to replace."
            )
        body_text = body.expandtabs()
        target_norm = target.expandtabs()
        count = body_text.count(target_norm)
        if count == 0:
            raise ValueError(
                f"target not found in artifact '{slug}'. "
                "Check for exact match (case-sensitive, whitespace-sensitive)."
            )
        if count > 1:
            raise ValueError(
                f"target appears {count} times in '{slug}'. "
                "Provide more context to make it unique."
            )
        updated_body = body_text.replace(target_norm, content, 1)
        result_action = "replaced"

    fm["updated"] = datetime.now(UTC).isoformat()
    md_content = render_frontmatter(fm, updated_body)
    _atomic_write(match_path, md_content)

    if memory_store is not None:
        _reindex(memory_store, match_path, updated_body, md_content, fm, slug)

    return MutateResult(
        path=match_path,
        slug=slug,
        action=result_action,
        updated_body=updated_body,
        fm=fm,
    )
