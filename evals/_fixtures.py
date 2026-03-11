"""Eval test data fixtures: memory seeding, message history builders."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
) -> Path:
    """Write a memory markdown file with valid YAML frontmatter.

    Creates a file named ``{memory_id:03d}-{slug}.md`` in ``memory_dir``.
    Used by memory evals to pre-populate the knowledge store before running
    agent turns.

    Args:
        memory_dir: Directory to write the memory file into (must exist).
        memory_id: Numeric ID embedded in the frontmatter and filename.
        content: Memory content body (written below the frontmatter block).
        days_ago: How many days in the past to set the ``created`` timestamp.
        tags: Optional list of tags to embed in frontmatter.
    """
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    slug = content[:40].lower().replace(" ", "-").replace(",", "")
    filename = f"{memory_id:03d}-{slug}.md"

    fm = {
        "id": memory_id,
        "created": created,
        "tags": tags or [],
        "source": "user-told",
        "auto_category": None,
    }

    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def single_user_turn(text: str) -> list[Any]:
    """Build a minimal one-message history with a single user prompt.

    Convenience wrapper for constructing a one-turn ``[ModelRequest]`` list
    when calling LLM sub-agents (e.g. ``analyze_for_signals``) directly.
    """
    return [ModelRequest(parts=[UserPromptPart(content=text)])]


def build_message_history(entries: list[tuple]) -> list[Any]:
    """Convert a list of tuples into pydantic-ai message objects.

    Supported entry formats::

        ("user",        "text")
        ("assistant",   "text")
        ("tool_call",   tool_name, args_json_str, call_id)
        ("tool_return", tool_name, content,       call_id)

    Used by Tier 3 conversation-history evals to construct synthetic
    histories with tool calls and returns without running live LLM turns.
    """
    messages: list[Any] = []
    for entry in entries:
        kind = entry[0]
        if kind == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=entry[1])]))
        elif kind == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=entry[1])]))
        elif kind == "tool_call":
            _, tool_name, args, call_id = entry
            messages.append(ModelResponse(parts=[
                ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id),
            ]))
        elif kind == "tool_return":
            _, tool_name, content, call_id = entry
            messages.append(ModelRequest(parts=[
                ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id),
            ]))
    return messages


def patch_dangling_tool_calls(messages: list[Any]) -> list[Any]:
    """Inject dummy ToolReturnPart for any unanswered ToolCallPart at end of history.

    pydantic-ai rejects ``agent.run()`` if the history ends with a
    ``ModelResponse`` containing ``ToolCallPart`` with no matching
    ``ToolReturnPart``. Some overeager models trigger tool calls during
    conversational setup turns — this patcher makes the history valid so the
    scored turn can proceed.

    Mirrors the same safety logic in ``co_cli/_orchestrate.py``.
    """
    if not messages:
        return messages

    last_msg = messages[-1]
    if not (hasattr(last_msg, "kind") and last_msg.kind == "response"):
        return messages

    tool_calls = [p for p in last_msg.parts if isinstance(p, ToolCallPart)]
    if not tool_calls:
        return messages

    return_parts = [
        ToolReturnPart(
            tool_name=tc.tool_name,
            tool_call_id=tc.tool_call_id,
            content="[eval: tool not available during setup turn]",
        )
        for tc in tool_calls
    ]
    return messages + [ModelRequest(parts=return_parts)]
