"""Knowledge artifact mutation helpers — inline frontmatter update and FTS re-index."""

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.knowledge.artifact import ArtifactKindEnum, IndexSourceEnum, KnowledgeArtifact
from co_cli.knowledge.chunker import chunk_text
from co_cli.knowledge.frontmatter import parse_frontmatter, render_frontmatter


def _atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
    os.replace(tmp.name, path)


def _reindex_knowledge_file(
    ctx: RunContext[CoDeps],
    path: Path,
    body: str,
    md_content: str,
    fm: dict[str, Any],
    slug: str,
) -> None:
    """Re-index a knowledge file's docs row and chunk rows after in-place mutation.

    Both legs must stay in sync when the file body changes: docs_fts serves
    non-chunks queries, chunks_fts serves chunk-level queries. sync_dir normally
    handles both at once, but callers that mutate a single file (e.g. knowledge_update,
    knowledge_append, _update_artifact_body) need to refresh the DB inline.
    """
    store = ctx.deps.knowledge_store
    if store is None:
        return
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
        body.strip(),
        chunk_size=ctx.deps.config.knowledge.chunk_size,
        overlap=ctx.deps.config.knowledge.chunk_overlap,
    )
    store.index_chunks(IndexSourceEnum.KNOWLEDGE, str(path), chunks)


def _update_artifact_body(
    artifact: KnowledgeArtifact,
    new_body: str,
    ctx: RunContext[CoDeps],
) -> None:
    """Atomically overwrite the body of an existing artifact and re-index it."""
    raw = artifact.path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)
    fm["updated"] = datetime.now(UTC).isoformat()
    md_content = render_frontmatter(fm, new_body)
    _atomic_write(artifact.path, md_content)
    if ctx.deps.knowledge_store is not None:
        _reindex_knowledge_file(ctx, artifact.path, new_body, md_content, fm, artifact.path.stem)
