"""Tests for history processor behavior: dedup and evict-old."""

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

from co_cli.config.tuning import EVICT_KEEP_RECENT
from co_cli.context.compaction import is_cleared_marker
from co_cli.context.history_processors import (
    dedup_tool_results,
    evict_old_tool_results,
)
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
    total = EVICT_KEEP_RECENT + 1
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
    """Exactly EVICT_KEEP_RECENT returns must all be kept — nothing evicted."""
    messages = []
    for i in range(EVICT_KEEP_RECENT):
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


def test_evict_clears_unknown_tool_via_generic_fallback():
    """Unknown tool names follow the same recency policy as known ones (no whitelist)."""
    total = EVICT_KEEP_RECENT + 1
    messages: list = []
    for i in range(total):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f"call {i}")]))
        messages.append(
            ModelResponse(
                parts=[
                    TextPart(content="ok"),
                    ToolCallPart(
                        tool_name="memory_create",
                        args='{"title": "t"}',
                        tool_call_id=f"call{i}",
                    ),
                ]
            )
        )
        messages.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="memory_create",
                        content=f"created article 'note{i}' " * 20,
                        tool_call_id=f"call{i}",
                    )
                ]
            )
        )
    messages.append(ModelRequest(parts=[UserPromptPart(content="pending")]))

    result = evict_old_tool_results(_ctx(), messages)

    returns = [
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_name == "memory_create"
    ]
    oldest = next(p for p in returns if p.tool_call_id == "call0")
    assert is_cleared_marker(oldest.content)
    assert oldest.content.startswith("[memory_create]")


def test_evict_protects_tool_returns_in_last_turn():
    """Tool returns in the last user turn must never be evicted regardless of count."""
    protected_content = "protected " * 30
    messages = []
    for i in range(EVICT_KEEP_RECENT + 1):
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
# Lone surrogate resilience
# ---------------------------------------------------------------------------


def test_dedup_tolerates_lone_surrogate_content():
    """dedup_tool_results deduplicates correctly with lone-surrogate content (no UnicodeEncodeError)."""
    surrogate_content = "\ud800prefix" * 100  # lone surrogate, well above 200-char floor
    messages = [
        *_file_read_exchange("call1", surrogate_content),
        ModelResponse(parts=[TextPart(content="done")]),
        *_file_read_exchange("call2", surrogate_content),
    ]
    result = dedup_tool_results(_ctx(), messages)
    call1 = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "call1"
    )
    call2 = next(
        p
        for msg in result
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "call2"
    )
    assert surrogate_content not in call1.content, (
        "older duplicate must be replaced with back-reference"
    )
    assert "call2" in call1.content
    assert call2.content == surrogate_content, "newer call must retain original content"
