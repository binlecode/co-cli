"""Memory view tool — read full artifact body by filename_stem."""

import logging
import math

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.memory.item import MemoryKindEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output

logger = logging.getLogger(__name__)


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    spill_threshold_chars=math.inf,
)
async def memory_view(
    ctx: RunContext[CoDeps],
    name: str,
) -> ToolReturn:
    """Load the full body of a memory artifact by its filename_stem.

    Use after memory_search returns a hit when you need the complete artifact
    content — not just the snippet. The `name` is the `filename_stem` field from
    search results.

    Returns: artifact body (post-frontmatter), plus kind, name, and path metadata.
    Returns tool_error when the artifact does not exist.

    Args:
        name: The artifact filename_stem (no directory, no .md extension).
    """
    path = ctx.deps.memory_dir / f"{name}.md"
    if not path.exists():
        return tool_error(f"memory_view: unknown artifact {name!r}.", ctx=ctx)

    raw = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    kind = frontmatter.get("memory_kind", MemoryKindEnum.NOTE.value)
    return tool_output(
        body.strip(),
        ctx=ctx,
        name=name,
        kind=kind,
        path=str(path),
    )
