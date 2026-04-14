"""Memory write tool — always creates a new memory file, no dedup, no lifecycle."""

import hashlib
import re
from datetime import UTC, datetime
from uuid import uuid4

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.knowledge._frontmatter import MemoryTypeEnum, render_memory_file
from co_cli.tools.tool_output import tool_output_raw

_TRACER = otel_trace.get_tracer("co.memory")


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug, max 50 chars."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


async def save_memory(
    ctx: RunContext[CoDeps],
    content: str,
    type_: str | None = None,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    always_on: bool = False,
) -> ToolReturn:
    """Save a new memory to the memory directory, always creating a new file.

    Two calls with identical content produce two distinct files (UUID suffix).
    No dedup, no resource locks, no on_failure handling — write always succeeds or raises.
    """
    if type_ is not None and type_ not in {e.value for e in MemoryTypeEnum}:
        raise ValueError(
            f"Unknown memory type: {type_!r}. "
            f"Valid values: {sorted(e.value for e in MemoryTypeEnum)}"
        )

    memory_dir = ctx.deps.memory_dir
    memory_dir.mkdir(parents=True, exist_ok=True)

    memory_id = str(uuid4())

    slug = _slugify(name) if name else _slugify(content[:50])
    filename = f"{slug}-{memory_id[:8]}.md"

    norm_tags = [t.lower() for t in tags] if tags else []

    frontmatter: dict = {
        "id": memory_id,
        "kind": "memory",
        "created": datetime.now(UTC).isoformat(),
        "tags": norm_tags,
    }
    if type_ is not None:
        frontmatter["type"] = type_
    if name is not None:
        frontmatter["name"] = name
    if description is not None:
        frontmatter["description"] = description
    if always_on:
        frontmatter["always_on"] = True

    file_content = render_memory_file(frontmatter, content)

    file_path = memory_dir / filename
    with _TRACER.start_as_current_span("co.memory.save") as span:
        span.set_attribute("memory.type", type_ or "untyped")
        file_path.write_text(file_content, encoding="utf-8")

    if ctx.deps.knowledge_store is not None:
        content_hash = hashlib.sha256(file_content.encode()).hexdigest()
        ctx.deps.knowledge_store.index(
            source="memory",
            kind="memory",
            path=str(file_path),
            title=name or slug,
            content=content.strip(),
            mtime=file_path.stat().st_mtime,
            hash=content_hash,
            tags=" ".join(norm_tags) if norm_tags else None,
            created=frontmatter["created"],
            type=type_,
            description=description,
        )

    return tool_output_raw(
        f"✓ Saved memory: {filename}",
        action="saved",
        path=str(file_path),
        memory_id=memory_id,
    )
