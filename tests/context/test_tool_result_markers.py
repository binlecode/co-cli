"""Functional tests for semantic_marker — per-tool 1-line markers replacing cleared tool returns."""

from __future__ import annotations

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.config._compaction import CompactionSettings
from co_cli.context._history import (
    _CLEARED_PLACEHOLDER,
    truncate_tool_results,
)
from co_cli.context._tool_result_markers import (
    is_cleared_marker,
    semantic_marker,
)
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend


def _processor_ctx() -> RunContext:
    """Minimal RunContext for truncate_tool_results — no LLM call."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(
            compaction=CompactionSettings(min_context_length_tokens=0),
        ),
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


# ---------------------------------------------------------------------------
# semantic_marker — per-tool markers
# ---------------------------------------------------------------------------


def test_shell_success_marker_reports_ok_and_line_count():
    marker = semantic_marker("shell", {"cmd": "ls -1"}, "file_a\nfile_b\nfile_c")
    assert marker.startswith("[shell] ran `ls -1`")
    assert "ok" in marker
    assert "3 lines" in marker


def test_shell_failure_marker_extracts_exit_code():
    marker = semantic_marker("shell", {"cmd": "false"}, "exit 1:\nsomething went wrong")
    assert marker.startswith("[shell] ran `false`")
    assert "exit 1" in marker
    assert "ok" not in marker


def test_shell_truncates_long_command():
    long_cmd = "echo " + "x" * 200
    marker = semantic_marker("shell", {"cmd": long_cmd}, "")
    assert "..." in marker
    # Full 200-char suffix must not appear verbatim.
    assert "x" * 200 not in marker


def test_file_read_full_marker_includes_path_and_chars():
    marker = semantic_marker("file_read", {"path": "foo.py"}, "line1\nline2\nline3\n")
    assert marker.startswith("[file_read] foo.py")
    assert "full" in marker
    assert "18 chars" in marker


def test_file_read_range_marker_includes_line_span():
    marker = semantic_marker(
        "file_read", {"path": "foo.py", "start_line": 10, "end_line": 20}, "abc"
    )
    assert marker.startswith("[file_read] foo.py")
    assert "lines 10-20" in marker


def test_file_search_no_matches_marker():
    marker = semantic_marker("file_search", {"pattern": "xyz", "path": "."}, "(no matches)")
    assert marker == "[file_search] 'xyz' in . → no matches"


def test_file_search_with_matches_marker_reports_line_count():
    content = "foo.py:1: match a\nfoo.py:2: match b\nbar.py:5: match c"
    marker = semantic_marker("file_search", {"pattern": "match", "path": "src"}, content)
    assert marker.startswith("[file_search] 'match' in src")
    assert "3 result lines" in marker


def test_file_find_empty_marker():
    marker = semantic_marker("file_find", {"path": ".", "pattern": "*.foo"}, "(empty)")
    assert marker == "[file_find] *.foo in . → no entries"


def test_file_find_entries_marker():
    content = "[file] a.py\n[file] b.py\n[dir] sub"
    marker = semantic_marker("file_find", {"path": "src", "pattern": "**/*"}, content)
    assert marker.startswith("[file_find] **/* in src")
    assert "3 entries" in marker


def test_web_search_no_results_marker():
    marker = semantic_marker("web_search", {"query": "abc"}, "No results for 'abc'.")
    assert marker == "[web_search] 'abc' → no results"


def test_web_search_results_marker_reports_chars():
    marker = semantic_marker(
        "web_search",
        {"query": "python asyncio"},
        "Web search results for 'python asyncio':\n\n...",
    )
    assert marker.startswith("[web_search] 'python asyncio'")
    assert "chars" in marker


def test_web_fetch_marker_includes_url():
    marker = semantic_marker(
        "web_fetch",
        {"url": "https://example.com/doc"},
        "Content from https://example.com/doc:\n\nbody",
    )
    assert marker.startswith("[web_fetch] https://example.com/doc")
    assert "chars" in marker


def test_knowledge_article_read_not_found_marker():
    marker = semantic_marker(
        "knowledge_article_read", {"slug": "missing"}, "Article 'missing' not found."
    )
    assert marker == "[knowledge_article_read] 'missing' → not found"


def test_knowledge_article_read_success_marker():
    marker = semantic_marker(
        "knowledge_article_read", {"slug": "py-asyncio"}, "# Title\nSource: http://x\n\nbody"
    )
    assert marker.startswith("[knowledge_article_read] 'py-asyncio'")
    assert "chars" in marker


def test_obsidian_read_marker_includes_filename():
    marker = semantic_marker("obsidian_read", {"filename": "Work/Project.md"}, "note body here")
    assert marker.startswith("[obsidian_read] Work/Project.md")
    assert "chars" in marker


def test_generic_fallback_for_unknown_tool():
    marker = semantic_marker("new_tool", {"key1": "val1", "key2": "val2"}, "body text")
    assert marker.startswith("[new_tool]")
    assert "key1=val1" in marker
    assert "key2=val2" in marker
    assert "chars" in marker


def test_generic_fallback_no_args():
    marker = semantic_marker("new_tool", {}, "body")
    assert marker.startswith("[new_tool]")
    assert "chars" in marker


def test_marker_missing_args_use_question_mark():
    marker = semantic_marker("file_read", {}, "content")
    assert "?" in marker


# ---------------------------------------------------------------------------
# is_cleared_marker predicate
# ---------------------------------------------------------------------------


def test_is_cleared_marker_recognizes_static_placeholder():
    assert is_cleared_marker(_CLEARED_PLACEHOLDER) is True


def test_is_cleared_marker_recognizes_semantic_markers():
    assert is_cleared_marker("[shell] ran `ls` → ok, 3 lines") is True
    assert is_cleared_marker("[file_read] path.py (full, 100 chars)") is True


def test_is_cleared_marker_rejects_verbatim_content():
    assert is_cleared_marker("some tool output text") is False
    assert is_cleared_marker("(no matches)") is False


def test_is_cleared_marker_rejects_non_string():
    assert is_cleared_marker(None) is False
    assert is_cleared_marker({"key": "val"}) is False
    assert is_cleared_marker(["multimodal", "list"]) is False


# ---------------------------------------------------------------------------
# truncate_tool_results end-to-end — replacement is a semantic marker
# ---------------------------------------------------------------------------


def _user_msg(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_msg(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _build_shell_conversation(n: int) -> list:
    """n shell calls across n turns, then a final user turn to protect the tail."""
    msgs: list = []
    for idx in range(n):
        cid = f"sh{idx}"
        msgs.append(_user_msg(f"run command {idx}"))
        msgs.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="shell",
                        args={"cmd": f"echo hello {idx}"},
                        tool_call_id=cid,
                    )
                ]
            )
        )
        msgs.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="shell",
                        content=f"hello {idx}",
                        tool_call_id=cid,
                    )
                ]
            )
        )
        msgs.append(_assistant_msg(f"did {idx}"))
    msgs.append(_user_msg("final"))
    msgs.append(_assistant_msg("done"))
    return msgs


def test_truncate_replaces_older_shell_returns_with_semantic_markers():
    msgs = _build_shell_conversation(8)
    ctx = _processor_ctx()
    result = truncate_tool_results(ctx, msgs)

    shell_contents = [
        part.content
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "shell"
    ]

    verbatim = [c for c in shell_contents if c.startswith("hello ")]
    markers = [c for c in shell_contents if c.startswith("[shell]")]

    # 8 total: 5 most recent kept, 3 replaced with markers
    assert len(verbatim) == 5
    assert len(markers) == 3
    # Markers must reference the original command (args preserved via call_id index)
    assert all("echo hello" in m for m in markers)


def test_truncate_falls_back_to_static_placeholder_on_non_string_content():
    ctx = _processor_ctx()
    # Build 8 file_read calls where older returns carry non-string (multimodal-like) content.
    msgs: list = []
    for idx in range(8):
        cid = f"fr{idx}"
        msgs.append(_user_msg(f"read {idx}"))
        msgs.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_read", args={"path": f"f{idx}.py"}, tool_call_id=cid
                    )
                ]
            )
        )
        content: object = (
            [{"type": "text", "text": f"file {idx}"}] if idx < 3 else f"content {idx}"
        )
        msgs.append(
            ModelRequest(
                parts=[ToolReturnPart(tool_name="file_read", content=content, tool_call_id=cid)]
            )
        )
        msgs.append(_assistant_msg(f"ack {idx}"))
    msgs.append(_user_msg("final"))
    msgs.append(_assistant_msg("done"))

    result = truncate_tool_results(ctx, msgs)

    file_read_returns = [
        part
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "file_read"
    ]
    # The 3 oldest (indices 0-2) had non-string content → static placeholder fallback.
    # Indices 3-7 are the 5 most recent → kept verbatim.
    non_string_replaced = [r for r in file_read_returns if r.content == _CLEARED_PLACEHOLDER]
    verbatim_kept = [
        r
        for r in file_read_returns
        if isinstance(r.content, str) and r.content.startswith("content ")
    ]
    assert len(non_string_replaced) == 3
    assert len(verbatim_kept) == 5


@pytest.mark.parametrize(
    ("tool_name", "args", "verbatim_content"),
    [
        ("file_read", {"path": "a.py"}, "a" * 500),
        ("file_search", {"pattern": "x", "path": "."}, "a.py:1: x\nb.py:2: x"),
        ("file_find", {"path": ".", "pattern": "*"}, "[file] a\n[file] b"),
        ("web_search", {"query": "q"}, "Web search results for 'q':\n\n1. Title"),
        ("web_fetch", {"url": "https://e.com"}, "Content from https://e.com:\n\nbody"),
        ("knowledge_article_read", {"slug": "s"}, "# Title\n\nbody"),
        ("obsidian_read", {"filename": "n.md"}, "note body"),
    ],
)
def test_truncate_uses_marker_per_compactable_tool(tool_name, args, verbatim_content):
    """Each compactable tool gets its own tool-name-prefixed marker after the 5-window."""
    ctx = _processor_ctx()
    msgs: list = []
    for idx in range(7):
        cid = f"{tool_name}{idx}"
        msgs.append(_user_msg(f"call {idx}"))
        msgs.append(
            ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=cid)])
        )
        msgs.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name=tool_name, content=verbatim_content, tool_call_id=cid)
                ]
            )
        )
        msgs.append(_assistant_msg(f"ack {idx}"))
    msgs.append(_user_msg("final"))
    msgs.append(_assistant_msg("done"))

    result = truncate_tool_results(ctx, msgs)

    markers = [
        part.content
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == tool_name
        and is_cleared_marker(part.content)
    ]
    # 7 calls, 5 kept, 2 replaced with markers
    assert len(markers) == 2
    assert all(m.startswith(f"[{tool_name}]") for m in markers)
