"""Knowledge write tools — save, update, and append knowledge artifacts."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    SourceTypeEnum,
    load_knowledge_artifacts,
)
from co_cli.knowledge._frontmatter import (
    parse_frontmatter,
    render_frontmatter,
    render_knowledge_file,
)
from co_cli.knowledge._similarity import find_similar_artifacts, is_content_superset
from co_cli.knowledge.mutator import _atomic_write, _reindex_knowledge_file, _update_artifact_body
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.knowledge._helpers import (
    _find_article_by_url,
    _find_by_slug,
    _slugify,
)
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_io import tool_error, tool_output

_TRACER = otel_trace.get_tracer("co.knowledge")

logger = logging.getLogger(__name__)

# Matches line-number prefixes injected by the Read tool (e.g. "1→ " or "Line 1: ")
_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+\u2192 ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")


async def save_knowledge(
    ctx: RunContext[CoDeps],
    content: str,
    artifact_kind: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> ToolReturn:
    """Save a reusable knowledge artifact (preference, rule, feedback, decision, article, reference, note).

    Writes a canonical kind=knowledge markdown file under ctx.deps.knowledge_dir
    and indexes it under source='knowledge' so search_knowledge can retrieve it.

    When consolidation_enabled=True, a similarity check runs before writing:
    near-identical content (>0.9 Jaccard) is skipped; overlapping content is
    merged into the existing artifact rather than creating a duplicate.

    Args:
        content: Primary text of the artifact.
        artifact_kind: One of preference | decision | rule | feedback | article | reference | note.
        title: Optional human-readable label.
        description: Optional ≤200-char hook used for retrieval ranking.
        tags: Optional retrieval labels (lowercased before writing).
    """
    valid_kinds = {e.value for e in ArtifactKindEnum}
    if artifact_kind not in valid_kinds:
        raise ValueError(
            f"Unknown artifact_kind: {artifact_kind!r}. Valid values: {sorted(valid_kinds)}"
        )

    knowledge_dir = ctx.deps.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    if ctx.deps.config.knowledge.consolidation_enabled:
        threshold = ctx.deps.config.knowledge.consolidation_similarity_threshold
        existing = load_knowledge_artifacts(knowledge_dir, artifact_kind=artifact_kind)
        matches = find_similar_artifacts(content, artifact_kind, existing, threshold)
        if matches:
            best_artifact, best_score = matches[0]
            with _TRACER.start_as_current_span("co.knowledge.dedup") as span:
                if best_score > 0.9:
                    span.set_attribute("knowledge.dedup_action", "skipped")
                    return tool_output(
                        f"Skipped (near-identical to {best_artifact.path.name})",
                        ctx=ctx,
                        action="skipped",
                        path=str(best_artifact.path),
                        artifact_id=best_artifact.id,
                    )
                if is_content_superset(content, best_artifact.content):
                    dedup_action = "merged"
                    merged_body = content
                else:
                    dedup_action = "appended"
                    merged_body = best_artifact.content.rstrip() + "\n" + content
                span.set_attribute("knowledge.dedup_action", dedup_action)
                _update_artifact_body(best_artifact, merged_body, ctx)
                return tool_output(
                    f"✓ Saved knowledge ({dedup_action}): {best_artifact.path.name}",
                    ctx=ctx,
                    action=dedup_action,
                    path=str(best_artifact.path),
                    artifact_id=best_artifact.id,
                )

    artifact_id = str(uuid4())
    slug = _slugify(title) if title else _slugify(content[:50])
    filename = f"{slug}-{artifact_id[:8]}.md"
    file_path = knowledge_dir / filename

    session_path = ctx.deps.session.session_path
    source_ref = session_path.stem if session_path and str(session_path) else None

    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=file_path,
        artifact_kind=artifact_kind,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        description=description,
        tags=[t.lower() for t in (tags or [])],
        source_type=SourceTypeEnum.DETECTED.value,
        source_ref=source_ref,
    )

    file_content = render_knowledge_file(artifact)
    fm_dict = {
        "artifact_kind": artifact_kind,
        "title": artifact.title,
        "tags": artifact.tags,
        "created": artifact.created,
        "description": artifact.description,
    }
    with _TRACER.start_as_current_span("co.knowledge.save") as span:
        span.set_attribute("knowledge.artifact_kind", artifact_kind)
        _atomic_write(file_path, file_content)
    _reindex_knowledge_file(ctx, file_path, content, file_content, fm_dict, slug)

    return tool_output(
        f"✓ Saved knowledge: {filename}",
        ctx=ctx,
        action="saved",
        path=str(file_path),
        artifact_id=artifact_id,
    )


def _consolidate_and_reindex(
    ctx: RunContext[CoDeps],
    path: Path,
    new_content: str,
    new_title: str,
    new_tags: list[str] | None,
    origin_url: str,
) -> ToolReturn:
    """Consolidate an existing article (same source_ref) and re-sync the index."""
    raw = path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)

    existing_tags = fm.get("tags") or []
    merged_tags = sorted(set(existing_tags) | set(new_tags or []))
    artifact_id = str(fm.get("id") or "")
    created = fm.get("created") or datetime.now(UTC).isoformat()

    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=path,
        artifact_kind=ArtifactKindEnum.ARTICLE.value,
        title=new_title,
        content=new_content,
        created=created,
        updated=datetime.now(UTC).isoformat(),
        tags=merged_tags,
        related=list(fm.get("related") or []),
        source_type=SourceTypeEnum.WEB_FETCH.value,
        source_ref=origin_url,
        decay_protected=True,
    )
    md_content = render_knowledge_file(artifact)
    _atomic_write(path, md_content)
    logger.info(f"Consolidated article {artifact_id} (same origin_url)")

    fm_dict = {
        "artifact_kind": ArtifactKindEnum.ARTICLE.value,
        "title": new_title,
        "tags": merged_tags,
        "created": created,
        "source_ref": origin_url,
        "id": fm.get("id"),
    }
    try:
        _reindex_knowledge_file(ctx, path, new_content, md_content, fm_dict, path.stem)
    except Exception as e:
        logger.warning(f"Failed to reindex consolidated article: {e}")

    return tool_output(
        f"✓ Updated article {artifact_id}: {path.name}\nSource: {origin_url}\nLocation: {path}",
        ctx=ctx,
        article_id=artifact_id,
        action="consolidated",
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    approval=True,
    is_concurrent_safe=True,
    retries=1,
)
async def save_article(
    ctx: RunContext[CoDeps],
    content: str,
    title: str,
    origin_url: str,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> ToolReturn:
    """Save an article from external reference material for long-term retrieval.

    Use for web pages, documentation, or research worth keeping permanently.
    Articles are decay-protected and persist indefinitely.

    Deduplication by origin_url: saving the same URL a second time consolidates
    (updates content and tags) rather than creating a duplicate. The origin_url
    is stored as ``source_ref`` on the artifact and serves as the dedup key.

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - article_id: assigned ID
    - action: "saved" (new) or "consolidated" (merged with existing URL)

    Args:
        content: Full markdown body of the article.
        title: Article title (used in display and as slug base).
        origin_url: Source URL the article was fetched from.
        tags: Categorization tags (e.g. ["python", "async", "reference"]).
        related: Slugs of related knowledge artifacts for cross-linking.
    """
    knowledge_dir = ctx.deps.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    existing = _find_article_by_url(knowledge_dir, origin_url)
    if existing is not None:
        return _consolidate_and_reindex(ctx, existing, content, title, tags, origin_url)

    article_id = str(uuid4())
    slug = _slugify(title[:50])
    filename = f"{slug}-{article_id[:6]}.md"
    file_path = knowledge_dir / filename

    artifact = KnowledgeArtifact(
        id=article_id,
        path=file_path,
        artifact_kind=ArtifactKindEnum.ARTICLE.value,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        tags=list(tags or []),
        related=list(related or []),
        source_type=SourceTypeEnum.WEB_FETCH.value,
        source_ref=origin_url,
        decay_protected=True,
    )
    md_content = render_knowledge_file(artifact)
    _atomic_write(file_path, md_content)
    logger.info(f"Saved article {article_id} to {file_path}")

    fm_dict = {
        "artifact_kind": ArtifactKindEnum.ARTICLE.value,
        "title": title,
        "tags": list(tags or []),
        "created": artifact.created,
        "source_ref": origin_url,
        "id": article_id,
    }
    try:
        _reindex_knowledge_file(ctx, file_path, content, md_content, fm_dict, slug)
    except Exception as e:
        logger.warning(f"Failed to index article {article_id}: {e}")

    return tool_output(
        f"✓ Saved article {article_id}: {filename}\nSource: {origin_url}\nLocation: {file_path}",
        ctx=ctx,
        article_id=article_id,
        action="saved",
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    approval=True,
    is_concurrent_safe=True,
    retries=1,
)
async def append_knowledge(
    ctx: RunContext[CoDeps],
    slug: str,
    content: str,
) -> ToolReturn:
    """Append content to the end of an existing knowledge artifact.

    Use when new information extends an artifact rather than replacing it.
    Safer than update_knowledge when you don't have an exact passage to match.

    *slug* is the full file stem, e.g. "001-dont-use-trailing-comments".
    Use list_knowledge to find it.

    Returns a dict with:
    - display: confirmation message
    - slug: the artifact slug that was appended to

    Args:
        slug: Full file stem of the target artifact (e.g. "003-user-prefers-pytest").
        content: Text to append (added on a new line at the end of the body).
    """
    knowledge_dir = ctx.deps.knowledge_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Knowledge artifact '{slug}' not found")

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            with _TRACER.start_as_current_span("co.knowledge.append") as span:
                span.set_attribute("knowledge.slug", slug)
                span.set_attribute("knowledge.action", "append")

                updated_body = body.rstrip() + "\n" + content
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_frontmatter(fm, updated_body)
                _atomic_write(match, md_content)

                if ctx.deps.knowledge_store is not None:
                    _reindex_knowledge_file(ctx, match, updated_body, md_content, fm, slug)

            return tool_output(
                f"Appended to artifact '{slug}'.",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Artifact '{slug}' is being modified by another tool call — retry next turn",
            ctx=ctx,
        )


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    approval=True,
    is_concurrent_safe=True,
    retries=1,
)
async def update_knowledge(
    ctx: RunContext[CoDeps],
    slug: str,
    old_content: str,
    new_content: str,
) -> ToolReturn:
    """Surgically replace a specific passage in a saved knowledge artifact without
    rewriting the entire body. Safer than save_knowledge for targeted edits —
    no dedup path, no full-body replacement.

    *slug* is the full file stem, e.g. "001-dont-use-trailing-comments".
    Use list_knowledge to find it.

    Guards applied before any I/O:
    - Rejects old_content / new_content that contain Read-tool line-number
      prefixes (``1→ `` or ``Line N: ``).
    - old_content must appear exactly once in the body (case-sensitive).

    Returns a dict with:
    - display: confirmation + updated body text
    - slug: the artifact slug that was edited

    Args:
        slug: Full file stem of the target artifact (e.g. "003-user-prefers-pytest").
        old_content: Exact passage to replace (must appear exactly once).
        new_content: Replacement text.
    """
    knowledge_dir = ctx.deps.knowledge_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Knowledge artifact '{slug}' not found")

    for s, name in ((old_content, "old_content"), (new_content, "new_content")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1\u2192 ' or 'Line N: '). "
                "Strip them before calling update_knowledge."
            )

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            body_text = body.expandtabs()
            old_norm = old_content.expandtabs()
            new_norm = new_content.expandtabs()

            count = body_text.count(old_norm)
            if count == 0:
                raise ValueError(
                    f"old_content not found in artifact '{slug}'. "
                    "Check for exact match (case-sensitive, whitespace-sensitive)."
                )
            if count > 1:
                positions: list[int] = []
                pos = 0
                while True:
                    idx = body_text.find(old_norm, pos)
                    if idx == -1:
                        break
                    line_num = body_text[:idx].count("\n") + 1
                    positions.append(line_num)
                    pos = idx + 1
                raise ValueError(
                    f"old_content appears {count} times in '{slug}' "
                    f"(body lines ~{positions}). Provide more context to make it unique."
                )

            with _TRACER.start_as_current_span("co.knowledge.update") as span:
                span.set_attribute("knowledge.slug", slug)
                span.set_attribute("knowledge.action", "update")

                updated_body = body_text.replace(old_norm, new_norm, 1)
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_frontmatter(fm, updated_body)
                _atomic_write(match, md_content)

                if ctx.deps.knowledge_store is not None:
                    _reindex_knowledge_file(ctx, match, updated_body, md_content, fm, slug)

            return tool_output(
                f"Updated artifact '{slug}'.\n{updated_body.strip()}",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Artifact '{slug}' is being modified by another tool call — retry next turn",
            ctx=ctx,
        )
