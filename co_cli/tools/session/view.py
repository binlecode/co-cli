"""Session view tool — read verbatim JSONL turn slice by session_id + line range."""

import logging
import math
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.session.transcript import extract_messages
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import READ_MAX_LINES, tool_error, tool_output

logger = logging.getLogger(__name__)


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    spill_threshold_chars=math.inf,
)
async def session_view(
    ctx: RunContext[CoDeps],
    session_id: str,
    start_line: int,
    end_line: int,
) -> ToolReturn:
    """Read verbatim turns from a past session by JSONL line range.

    Use after session_search returns a chunk hit when you need the exact turn
    content — commands, file paths, error messages, tool args — rather than the
    chunk-level snippet. Line numbers are 1-indexed JSONL lines as reported in
    the search hit's start_line/end_line.

    Clamps ranges over READ_MAX_LINES turns to keep context tight; turns are
    returned verbatim with no per-turn char clip.

    Returns: {session_id, lines: [...], truncated: bool}
        lines[i] = {line, role, content, tool_name|None}

    Args:
        session_id: 8-char session UUID suffix.
        start_line: First JSONL line to read (1-indexed).
        end_line: Last JSONL line to read (1-indexed, inclusive).
    """
    if start_line < 1 or end_line < start_line:
        return tool_output(
            f"Validation error: start_line must be >= 1 and end_line >= start_line "
            f"(got start_line={start_line}, end_line={end_line}).",
            ctx=ctx,
        )

    sessions_dir = ctx.deps.sessions_dir
    candidates = list(sessions_dir.glob(f"*-{session_id}.jsonl"))
    jsonl_path = candidates[0] if candidates else None

    if jsonl_path is None:
        return tool_error(
            f"Unknown session_id '{session_id}': no matching session file found.",
            ctx=ctx,
        )

    requested_lines = end_line - start_line + 1
    truncated = False
    effective_end = end_line
    if requested_lines > READ_MAX_LINES:
        effective_end = start_line + READ_MAX_LINES - 1
        truncated = True

    all_messages = extract_messages(jsonl_path)
    lo = start_line - 1
    hi = effective_end - 1
    in_range = [m for m in all_messages if lo <= m.line_index <= hi]

    output_lines: list[dict[str, Any]] = []
    for msg in in_range:
        entry: dict[str, Any] = {
            "line": msg.line_index + 1,
            "role": msg.role,
            "content": msg.content,
            "tool_name": msg.tool_name,
        }
        output_lines.append(entry)

    display_lines = [f"Session {session_id} — lines {start_line}–{effective_end}"]
    if truncated:
        display_lines.append(f"(truncated — showing {len(output_lines)} entries)")
    for entry in output_lines:
        tool_tag = f" [{entry['tool_name']}]" if entry["tool_name"] else ""
        display_lines.append(f"  L{entry['line']} {entry['role']}{tool_tag}: {entry['content']}")

    return tool_output(
        "\n".join(display_lines),
        ctx=ctx,
        session_id=session_id,
        lines=output_lines,
        truncated=truncated,
    )
