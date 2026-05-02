"""Semantic 1-line markers for compacted tool results.

Replaces the static "[tool result cleared — older than 5 most recent calls]"
placeholder with a per-tool description that preserves intent and outcome
signal (tool name, key args, char/line count) so the summarizer and future
turns retain a recognizable trace of what the cleared call did.

Per-tool handlers cover every member of ``COMPACTABLE_TOOLS``. A generic
fallback handles any tool that drifts into the compactable set without an
explicit handler.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from co_cli.tools.categories import COMPACTABLE_TOOLS

_ARG_PREVIEW_MAX = 40
_CMD_PREVIEW_MAX = 80
_URL_PREVIEW_MAX = 80
_QUERY_PREVIEW_MAX = 60

_SHELL_EXIT_RE = re.compile(r"^exit (-?\d+):")


def is_cleared_marker(content: object) -> bool:
    """True when content was produced by ``evict_old_tool_results`` as a replacement.

    Matches the static ``_CLEARED_PLACEHOLDER`` fallback (for non-string
    content) and per-tool semantic markers whose prefix is a known
    compactable tool name. Used by tests and evals to detect "was this
    return cleared?" without depending on the exact marker format.

    Unknown prefixes (``[file] ...`` from verbatim file_find output,
    ``[Reply]`` in an email body, ``[TODO]`` in a note) correctly return
    False — only compactable-tool markers are recognized.
    """
    if not isinstance(content, str):
        return False
    if content.startswith("[tool result cleared"):
        return True
    return any(content.startswith(f"[{tool}] ") for tool in COMPACTABLE_TOOLS)


def _truncate(value: str, max_len: int) -> str:
    """Truncate to max_len chars with ellipsis when longer."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _line_count(content: str) -> int:
    """Rough line count. Empty → 0; otherwise newlines + 1."""
    if not content:
        return 0
    return content.count("\n") + 1


def _shell_marker(args: dict[str, Any], content: str, _chars: int, lines: int) -> str:
    cmd = _truncate(str(args.get("cmd", "")), _CMD_PREVIEW_MAX)
    match = _SHELL_EXIT_RE.match(content)
    if match:
        return f"[shell] ran `{cmd}` → exit {match.group(1)}, {lines} lines"
    return f"[shell] ran `{cmd}` → ok, {lines} lines"


def _file_read_marker(args: dict[str, Any], _content: str, chars: int, _lines: int) -> str:
    path = args.get("path", "?")
    start = args.get("start_line")
    end = args.get("end_line")
    span = f"lines {start or 1}-{end or '?'}" if start or end else "full"
    return f"[file_read] {path} ({span}, {chars:,} chars)"


def _file_search_marker(args: dict[str, Any], content: str, _chars: int, lines: int) -> str:
    pattern = args.get("pattern", "?")
    path = args.get("path", ".")
    if content.startswith("(no matches)"):
        return f"[file_search] '{pattern}' in {path} → no matches"
    return f"[file_search] '{pattern}' in {path} ({lines} result lines)"


def _file_find_marker(args: dict[str, Any], content: str, _chars: int, lines: int) -> str:
    path = args.get("path", ".")
    pattern = args.get("pattern", "*")
    if content.startswith("(empty)"):
        return f"[file_find] {pattern} in {path} → no entries"
    return f"[file_find] {pattern} in {path} ({lines} entries)"


def _web_search_marker(args: dict[str, Any], content: str, chars: int, _lines: int) -> str:
    query = _truncate(str(args.get("query", "")), _QUERY_PREVIEW_MAX)
    if content.startswith("No results"):
        return f"[web_search] '{query}' → no results"
    return f"[web_search] '{query}' ({chars:,} chars)"


def _web_fetch_marker(args: dict[str, Any], _content: str, chars: int, _lines: int) -> str:
    url = _truncate(str(args.get("url", "")), _URL_PREVIEW_MAX)
    return f"[web_fetch] {url} ({chars:,} chars)"


def _obsidian_read_marker(args: dict[str, Any], _content: str, chars: int, _lines: int) -> str:
    filename = args.get("filename", "?")
    return f"[obsidian_read] {filename} ({chars:,} chars)"


_MarkerFn = Callable[[dict[str, Any], str, int, int], str]

_TOOL_MARKERS: dict[str, _MarkerFn] = {
    "shell": _shell_marker,
    "file_read": _file_read_marker,
    "file_search": _file_search_marker,
    "file_find": _file_find_marker,
    "web_search": _web_search_marker,
    "web_fetch": _web_fetch_marker,
    "obsidian_read": _obsidian_read_marker,
}


def _generic_marker(tool_name: str, args: dict[str, Any], chars: int) -> str:
    arg_preview_parts = [
        f"{key}={_truncate(str(val), _ARG_PREVIEW_MAX)}" for key, val in list(args.items())[:2]
    ]
    arg_preview = " " + " ".join(arg_preview_parts) if arg_preview_parts else ""
    return f"[{tool_name}]{arg_preview} ({chars:,} chars)"


def semantic_marker(tool_name: str, args: dict[str, Any], content: str) -> str:
    """Return a 1-line semantic description for a compacted tool result.

    The marker carries tool name, the 1-3 most informative args, and a
    size/outcome signal derived from the original content. Used by
    ``evict_old_tool_results`` as the replacement string for older-than-5
    compactable returns. Tools without an explicit handler fall back to a
    generic ``[tool] k=v (N chars)`` marker so future compactable tools are
    forward-compatible without code changes here.
    """
    chars = len(content)
    lines = _line_count(content)
    handler = _TOOL_MARKERS.get(tool_name)
    if handler is not None:
        return handler(args, content, chars, lines)
    return _generic_marker(tool_name, args, chars)
