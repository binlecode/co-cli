"""Knowledge service layer — pure CRUD functions, no RunContext or agent dependencies.

Provides save_artifact and mutate_artifact as the canonical write path for all
knowledge mutations. Tool wrappers acquire resource locks before calling these
functions, then call reindex explicitly after a successful write.
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
    artifact_to_frontmatter,
    parse_frontmatter,
    render_frontmatter,
    render_knowledge_file,
)
from co_cli.memory.mutator import atomic_write
from co_cli.memory.similarity import find_similar_artifacts, is_content_superset

if TYPE_CHECKING:
    from co_cli.memory.memory_store import MemoryStore

_LINE_PREFIX_RE = re.compile(r"(?:^|\n)\d+→ ")
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")


@dataclass
class SaveResult:
    """Outcome of save_artifact — describes what was written (or skipped)."""

    path: Path
    artifact_id: str
    action: Literal["saved", "skipped", "merged", "appended"]
    content: str
    frontmatter_dict: dict
    markdown_content: str
    filename_stem: str


@dataclass
class MutateResult:
    """Outcome of mutate_artifact — describes what was changed."""

    path: Path
    filename_stem: str
    action: Literal["appended", "replaced"]
    updated_body: str
    markdown_content: str
    frontmatter: dict


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _find_by_filename_stem(knowledge_dir: Path, filename_stem: str) -> Path | None:
    return next((p for p in knowledge_dir.glob("*.md") if p.stem == filename_stem), None)


def _find_article_by_url(knowledge_dir: Path, origin_url: str) -> Path | None:
    if not knowledge_dir.exists():
        return None
    for path in knowledge_dir.glob("*.md"):
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
    store: "MemoryStore",
    path: Path,
    body: str,
    markdown_content: str,
    frontmatter: dict,
    filename_stem: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    """Re-index a single knowledge file in the FTS store without RunContext."""
    content_hash = hashlib.sha256(markdown_content.encode()).hexdigest()
    artifact_kind = frontmatter.get("artifact_kind", ArtifactKindEnum.NOTE.value)
    store.index(
        source=IndexSourceEnum.KNOWLEDGE,
        kind=artifact_kind,
        path=str(path),
        title=frontmatter.get("title") or filename_stem,
        content=body.strip(),
        mtime=path.stat().st_mtime,
        hash=content_hash,
        created=frontmatter.get("created"),
        description=frontmatter.get("description"),
        source_ref=frontmatter.get("source_ref"),
        artifact_id=str(frontmatter["id"]) if frontmatter.get("id") is not None else None,
    )
    chunks = chunk_text(body.strip(), chunk_size=chunk_size, overlap=chunk_overlap)
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
    decay_protected: bool = False,
    consolidation_enabled: bool = False,
    consolidation_similarity_threshold: float = 0.75,
) -> SaveResult:
    """Save or consolidate a knowledge artifact. Pure — no RunContext.

    Three dispatch paths:
    - source_url set → URL-keyed dedup (web articles); decay_protected forced True.
    - consolidation_enabled → Jaccard dedup; near-identical skipped, overlapping merged.
    - else → straight create.
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    if source_url is not None:
        existing_path = _find_article_by_url(knowledge_dir, source_url)

        if existing_path is not None:
            raw = existing_path.read_text(encoding="utf-8")
            frontmatter, _ = parse_frontmatter(raw)
            artifact_id = str(frontmatter.get("id") or "")
            created = frontmatter.get("created") or datetime.now(UTC).isoformat()
            artifact = KnowledgeArtifact(
                id=artifact_id,
                path=existing_path,
                artifact_kind=ArtifactKindEnum.ARTICLE.value,
                title=title or frontmatter.get("title", existing_path.stem),
                content=content,
                created=created,
                updated=datetime.now(UTC).isoformat(),
                related=list(frontmatter.get("related") or []),
                source_type=SourceTypeEnum.WEB_FETCH.value,
                source_ref=source_url,
                decay_protected=True,
            )
            markdown_content = render_knowledge_file(artifact)
            atomic_write(existing_path, markdown_content)
            return SaveResult(
                path=existing_path,
                artifact_id=artifact_id,
                action="merged",
                content=content,
                frontmatter_dict=artifact_to_frontmatter(artifact),
                markdown_content=markdown_content,
                filename_stem=existing_path.stem,
            )

        artifact_id = str(uuid4())
        slug = slugify((title or content)[:50])
        filename = f"{slug}-{artifact_id[:8]}.md"
        file_path = knowledge_dir / filename
        artifact = KnowledgeArtifact(
            id=artifact_id,
            path=file_path,
            artifact_kind=ArtifactKindEnum.ARTICLE.value,
            title=title,
            content=content,
            created=datetime.now(UTC).isoformat(),
            source_type=SourceTypeEnum.WEB_FETCH.value,
            source_ref=source_url,
            decay_protected=True,
        )
        markdown_content = render_knowledge_file(artifact)
        atomic_write(file_path, markdown_content)
        return SaveResult(
            path=file_path,
            artifact_id=artifact_id,
            action="saved",
            content=content,
            frontmatter_dict=artifact_to_frontmatter(artifact),
            markdown_content=markdown_content,
            filename_stem=file_path.stem,
        )

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
                return SaveResult(
                    path=best_artifact.path,
                    artifact_id=best_artifact.id,
                    action="skipped",
                    content=content,
                    frontmatter_dict={},
                    markdown_content="",
                    filename_stem=best_artifact.path.stem,
                )
            if is_content_superset(content, best_artifact.content):
                dedup_action: Literal["merged", "appended"] = "merged"
                merged_body = content
            else:
                dedup_action = "appended"
                merged_body = best_artifact.content.rstrip() + "\n" + content
            raw = best_artifact.path.read_text(encoding="utf-8")
            frontmatter, _ = parse_frontmatter(raw)
            frontmatter["updated"] = datetime.now(UTC).isoformat()
            markdown_content = render_frontmatter(frontmatter, merged_body)
            atomic_write(best_artifact.path, markdown_content)
            return SaveResult(
                path=best_artifact.path,
                artifact_id=best_artifact.id,
                action=dedup_action,
                content=merged_body,
                frontmatter_dict=frontmatter,
                markdown_content=markdown_content,
                filename_stem=best_artifact.path.stem,
            )

    artifact_id = str(uuid4())
    slug = slugify(title) if title else slugify(content[:50])
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
        decay_protected=decay_protected,
    )
    markdown_content = render_knowledge_file(artifact)
    atomic_write(file_path, markdown_content)
    return SaveResult(
        path=file_path,
        artifact_id=artifact_id,
        action="saved",
        content=content,
        frontmatter_dict=artifact_to_frontmatter(artifact),
        markdown_content=markdown_content,
        filename_stem=file_path.stem,
    )


def mutate_artifact(
    knowledge_dir: Path,
    *,
    filename_stem: str,
    action: Literal["append", "replace"],
    content: str,
    target: str = "",
) -> MutateResult:
    """Append or surgically replace a passage in an existing knowledge artifact.

    Guards applied before any I/O:
    - Rejects content / target containing Read-tool line-number prefixes.
    - For replace: target must appear exactly once in the body.
    - For replace: empty target is rejected (ambiguous).
    """
    for s, name in ((content, "content"), (target, "target")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1→ ' or 'Line N: '). "
                "Strip them before calling mutate_artifact."
            )

    match_path = _find_by_filename_stem(knowledge_dir, filename_stem)
    if match_path is None:
        raise FileNotFoundError(f"Knowledge artifact '{filename_stem}' not found")

    raw = match_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)

    if action == "append":
        updated_body = body.rstrip() + "\n" + content
        result_action: Literal["appended", "replaced"] = "appended"
    else:
        if not target:
            raise ValueError(
                "mutate_artifact action='replace' requires a non-empty target. "
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
    atomic_write(match_path, markdown_content)

    return MutateResult(
        path=match_path,
        filename_stem=filename_stem,
        action=result_action,
        updated_body=updated_body,
        markdown_content=markdown_content,
        frontmatter=frontmatter,
    )
