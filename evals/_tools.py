"""Eval tool-call extraction and chain verification helpers."""

from typing import Any

from pydantic_ai.messages import ToolCallPart


def extract_first_tool_call(
    messages: list[Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract the first ToolCallPart from agent messages."""
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                return part.tool_name, part.args_as_dict()
    return None, None


def extract_tool_calls(messages: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract all ToolCallParts from agent messages as (name, args) tuples."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                calls.append((part.tool_name, part.args_as_dict()))
    return calls


def tool_names(messages: list[Any]) -> list[str]:
    """Extract ordered list of tool names called in agent messages."""
    return [name for name, _ in extract_tool_calls(messages)]


def is_ordered_subsequence(expected: list[str], actual: list[str]) -> bool:
    """Return True if ``expected`` appears as an ordered subsequence of ``actual``.

    Each element of ``expected`` must appear in ``actual`` in order, but
    ``actual`` may have additional tool calls in between.

    Example::

        is_ordered_subsequence(
            ["web_search", "web_fetch", "save_article"],
            ["web_search", "web_fetch", "web_fetch", "save_article"],
        )  # True
    """
    it = iter(actual)
    return all(tool in it for tool in expected)
