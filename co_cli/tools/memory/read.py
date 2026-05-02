"""Memory read tools — session turn reader.

Full-body reads route through the generic `file_read` tool using the path that
`memory_search` surfaces in its rendered output. Artifact listing is folded into
`memory_search`'s empty-query path.
"""

import logging
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import KnowledgeArtifact
from co_cli.memory.indexer import extract_messages
from co_cli.memory.session import parse_session_filename
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SESSION_TURN_MAX_LINES = 200
_SESSION_TURN_MAX_BYTES = 16 * 1024


def grep_recall(
    artifacts: list[KnowledgeArtifact],
    query: str,
    max_results: int,
) -> list[KnowledgeArtifact]:
    """Case-insensitive substring search across content and tags.

    Sorts by recency (updated or created, newest first).
    """
    query_lower = query.lower()
    matches = [m for m in artifacts if query_lower in m.content.lower()]
    matches.sort(key=lambda m: m.updated or m.created, reverse=True)
    return matches[:max_results]


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
)
async def memory_read_session_turn(
    ctx: RunContext[CoDeps],
    session_id: str,
    start_line: int,
    end_line: int,
) -> ToolReturn:
    """Read verbatim turns from a past session by JSONL line range.

    Use after memory_search returns a session chunk hit when you need the
    exact turn content — commands, file paths, error messages, tool args —
    rather than the chunk-level snippet. Line numbers are 1-indexed JSONL
    lines as reported in the search hit's start_line/end_line.

    Refuses ranges over 200 lines or content over 16KB to keep context tight.

    Returns: {session_id, lines: [...], truncated: bool}
        lines[i] = {line, role, content_preview, tool_name|None}
    """
    if start_line < 1 or end_line < start_line:
        return tool_output(
            f"Validation error: start_line must be >= 1 and end_line >= start_line "
            f"(got start_line={start_line}, end_line={end_line}).",
            ctx=ctx,
        )

    # Locate the JSONL file by matching uuid8 prefix against session filenames
    sessions_dir = ctx.deps.sessions_dir
    jsonl_path: Any = None
    for candidate in sessions_dir.glob("*.jsonl"):
        parsed = parse_session_filename(candidate.name)
        if parsed is not None and parsed[0] == session_id:
            jsonl_path = candidate
            break

    if jsonl_path is None:
        return tool_output(
            f"Unknown session_id '{session_id}': no matching session file found.",
            ctx=ctx,
        )

    # Apply line-count ceiling before reading
    requested_lines = end_line - start_line + 1
    truncated = False
    effective_end = end_line
    if requested_lines > _SESSION_TURN_MAX_LINES:
        effective_end = start_line + _SESSION_TURN_MAX_LINES - 1
        truncated = True

    # Extract messages from the full file, filtered to the requested line range
    all_messages = extract_messages(jsonl_path)
    # line_index in ExtractedMessage is 0-based; start_line/end_line are 1-based
    lo = start_line - 1
    hi = effective_end - 1
    in_range = [m for m in all_messages if lo <= m.line_index <= hi]

    # Build output lines, applying byte ceiling
    output_lines: list[dict[str, Any]] = []
    total_bytes = 0
    for msg in in_range:
        preview = msg.content[:200]
        entry: dict[str, Any] = {
            "line": msg.line_index + 1,
            "role": msg.role,
            "content_preview": preview,
            "tool_name": msg.tool_name,
        }
        entry_bytes = len(preview.encode("utf-8"))
        if total_bytes + entry_bytes > _SESSION_TURN_MAX_BYTES:
            truncated = True
            break
        output_lines.append(entry)
        total_bytes += entry_bytes

    display_lines = [f"Session {session_id} — lines {start_line}–{effective_end}"]
    if truncated:
        display_lines.append(f"(truncated — showing {len(output_lines)} entries)")
    for entry in output_lines:
        tool_tag = f" [{entry['tool_name']}]" if entry["tool_name"] else ""
        display_lines.append(
            f"  L{entry['line']} {entry['role']}{tool_tag}: {entry['content_preview']}"
        )

    return tool_output(
        "\n".join(display_lines),
        ctx=ctx,
        session_id=session_id,
        lines=output_lines,
        truncated=truncated,
    )
