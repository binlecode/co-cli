"""Tests for history processor behavior: dedup, evict-old, and batch spill."""

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
from tests._settings import SETTINGS_NO_MCP

from co_cli.context._history_processors import (
    COMPACTABLE_KEEP_RECENT,
    dedup_tool_results,
    evict_batch_tool_outputs,
    evict_old_tool_results,
)
from co_cli.context._tool_result_markers import is_cleared_marker
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend

_DEPS = CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, session=CoSessionState())


def _ctx(deps: CoDeps | None = None) -> RunContext:
    return RunContext(deps=deps or _DEPS, model=None, usage=RunUsage())


def _file_read_exchange(call_id: str, content: str) -> list:
    """Return [user_request, model_response_with_call, tool_return] for one file_read turn."""
    return [
        ModelRequest(parts=[UserPromptPart(content=f"read {call_id}")]),
        ModelResponse(
            parts=[
                TextPart(content="ok"),
                ToolCallPart(
                    tool_name="file_read", args='{"path": "/a.txt"}', tool_call_id=call_id
                ),
            ]
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_read", content=content, tool_call_id=call_id)]
        ),
    ]


# ---------------------------------------------------------------------------
# dedup_tool_results
# ---------------------------------------------------------------------------


def test_dedup_replaces_older_identical_return_with_back_reference():
    """Older identical tool return must be collapsed to a back-reference naming the newer call."""
    content = "x" * 300
    messages = [
        *_file_read_exchange("call1", content),
        ModelResponse(parts=[TextPart(content="done")]),
        *_file_read_exchange("call2", content),
        ModelResponse(parts=[TextPart(content="same")]),
        ModelRequest(parts=[UserPromptPart(content="pending")]),
    ]
    result = dedup_tool_results(_ctx(), messages)

    older = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "call1"
    )
    newer = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "call2"
    )
    assert content not in older.content
    assert "call2" in older.content
    assert newer.content == content


def test_dedup_passes_through_short_content():
    """Tool returns below the 200-char minimum must not be deduplicated."""
    short = "x" * 50
    messages = [
        *_file_read_exchange("call1", short),
        ModelResponse(parts=[TextPart(content="done")]),
        *_file_read_exchange("call2", short),
        ModelResponse(parts=[TextPart(content="same")]),
        ModelRequest(parts=[UserPromptPart(content="pending")]),
    ]
    result = dedup_tool_results(_ctx(), messages)
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "file_read":
                    assert part.content == short


def test_dedup_distinct_content_not_replaced():
    """Returns with distinct content must not be collapsed to back-references."""
    messages = [
        *_file_read_exchange("call1", "content A " * 30),
        ModelResponse(parts=[TextPart(content="done")]),
        *_file_read_exchange("call2", "content B " * 30),
        ModelResponse(parts=[TextPart(content="different")]),
        ModelRequest(parts=[UserPromptPart(content="pending")]),
    ]
    result = dedup_tool_results(_ctx(), messages)
    returns = [
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert all("content" in p.content for p in returns)


# ---------------------------------------------------------------------------
# evict_old_tool_results
# ---------------------------------------------------------------------------


def test_evict_clears_oldest_when_over_keep_limit():
    """The oldest compactable return must be content-cleared once more than 5 exist."""
    total = COMPACTABLE_KEEP_RECENT + 1
    messages = []
    for i in range(total):
        messages.extend(_file_read_exchange(f"call{i}", f"content{i} " * 20))
        messages.append(ModelResponse(parts=[TextPart(content="ok")]))
    messages.append(ModelRequest(parts=[UserPromptPart(content="pending")]))

    result = evict_old_tool_results(_ctx(), messages)

    returns = [
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_name == "file_read"
    ]
    assert len(returns) == total

    oldest = next(p for p in returns if p.tool_call_id == "call0")
    assert is_cleared_marker(oldest.content)

    for i in range(1, total):
        recent = next(p for p in returns if p.tool_call_id == f"call{i}")
        assert not is_cleared_marker(recent.content)


def test_evict_keeps_all_when_at_limit():
    """Exactly COMPACTABLE_KEEP_RECENT returns must all be kept — nothing evicted."""
    messages = []
    for i in range(COMPACTABLE_KEEP_RECENT):
        messages.extend(_file_read_exchange(f"call{i}", f"content{i} " * 20))
        messages.append(ModelResponse(parts=[TextPart(content="ok")]))
    messages.append(ModelRequest(parts=[UserPromptPart(content="pending")]))

    result = evict_old_tool_results(_ctx(), messages)

    returns = [
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_name == "file_read"
    ]
    assert not any(is_cleared_marker(p.content) for p in returns)


def test_evict_protects_tool_returns_in_last_turn():
    """Tool returns in the last user turn must never be evicted regardless of count."""
    protected_content = "protected " * 30
    messages = []
    for i in range(COMPACTABLE_KEEP_RECENT + 1):
        messages.extend(_file_read_exchange(f"old{i}", f"old{i} " * 20))
        messages.append(ModelResponse(parts=[TextPart(content="ok")]))
    messages.append(ModelRequest(parts=[UserPromptPart(content="current turn")]))
    messages.append(
        ModelResponse(parts=[ToolCallPart(tool_name="file_read", args="{}", tool_call_id="prot")])
    )
    messages.append(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read", content=protected_content, tool_call_id="prot"
                )
            ]
        )
    )

    result = evict_old_tool_results(_ctx(), messages)

    protected = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "prot"
    )
    assert protected.content == protected_content


# ---------------------------------------------------------------------------
# evict_batch_tool_outputs
# ---------------------------------------------------------------------------


def test_evict_batch_spills_largest_output_when_over_threshold(tmp_path):
    """Batch aggregate over batch_spill_chars must have the largest return spilled to disk."""
    from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG

    threshold = SETTINGS_NO_MCP.tools.batch_spill_chars
    large_content = "y" * (threshold + 10_000)

    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        tool_results_dir=tmp_path,
    )
    messages = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="file_read", args="{}", tool_call_id="call1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="file_read", content=large_content, tool_call_id="call1")
            ]
        ),
    ]
    result = evict_batch_tool_outputs(_ctx(deps), messages)

    spilled = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "call1"
    )
    assert PERSISTED_OUTPUT_TAG in spilled.content
    assert large_content not in spilled.content


def test_evict_batch_passes_through_when_under_threshold(tmp_path):
    """Batch aggregate under batch_spill_chars must pass through unchanged."""
    from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG

    small_content = "z" * 100

    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        tool_results_dir=tmp_path,
    )
    messages = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="file_read", args="{}", tool_call_id="call1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="file_read", content=small_content, tool_call_id="call1")
            ]
        ),
    ]
    result = evict_batch_tool_outputs(_ctx(deps), messages)

    unchanged = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "call1"
    )
    assert unchanged.content == small_content
    assert PERSISTED_OUTPUT_TAG not in unchanged.content


# ---------------------------------------------------------------------------
# group_by_turn
# ---------------------------------------------------------------------------


def test_group_by_turn_multi_turn():
    """group_by_turn must split a two-turn history into exactly two turn groups."""
    from co_cli.context._compaction_boundaries import group_by_turn

    messages = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[TextPart(content="turn 1 resp")]),
        ModelRequest(parts=[UserPromptPart(content="turn 2")]),
        ModelResponse(parts=[TextPart(content="turn 2 resp")]),
    ]
    groups = group_by_turn(messages)
    assert len(groups) == 2
    assert groups[0].messages[0].parts[0].content == "turn 1"
    assert groups[1].messages[0].parts[0].content == "turn 2"
