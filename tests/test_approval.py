"""Functional tests for _patch_dangling_tool_calls.

All tests use real pydantic-ai message objects — no mocks, no stubs.
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli._orchestrate import _patch_dangling_tool_calls


# ---------------------------------------------------------------------------
# Helpers — real pydantic-ai message objects
# ---------------------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call_response(name: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[
        ToolCallPart(tool_name=name, args="{}", tool_call_id=call_id),
    ])


def _tool_return_request(name: str, call_id: str, content: str = "ok") -> ModelRequest:
    return ModelRequest(parts=[
        ToolReturnPart(tool_name=name, tool_call_id=call_id, content=content),
    ])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_patch_empty_history():
    """Empty list is returned unchanged — no crash."""
    result = _patch_dangling_tool_calls([])
    assert result == []


def test_patch_no_dangling_calls():
    """History ending with a ModelRequest (user prompt) is unchanged."""
    msgs = [_user("hello"), _assistant("hi"), _user("do something")]
    result = _patch_dangling_tool_calls(msgs)
    assert result is msgs  # same object, not copied


def test_patch_single_dangling_call():
    """One unanswered tool call gets a synthetic ToolReturnPart."""
    msgs = [
        _user("list files"),
        _tool_call_response("run_shell_command", "c1"),
    ]
    result = _patch_dangling_tool_calls(msgs)

    assert len(result) == 3
    patch = result[2]
    assert patch.kind == "request"
    assert len(patch.parts) == 1

    ret = patch.parts[0]
    assert isinstance(ret, ToolReturnPart)
    assert ret.tool_name == "run_shell_command"
    assert ret.tool_call_id == "c1"
    assert ret.content == "Interrupted by user."


def test_patch_multiple_dangling_calls():
    """Each of N tool calls gets its own return with correct IDs."""
    response = ModelResponse(parts=[
        ToolCallPart(tool_name="run_shell_command", args='{"cmd":"ls"}', tool_call_id="c1"),
        ToolCallPart(tool_name="search_notes", args='{"q":"x"}', tool_call_id="c2"),
        ToolCallPart(tool_name="web_fetch", args='{"url":"http://x"}', tool_call_id="c3"),
    ])
    msgs = [_user("do three things"), response]
    result = _patch_dangling_tool_calls(msgs)

    assert len(result) == 3
    patch = result[2]
    assert len(patch.parts) == 3

    for i, (expected_name, expected_id) in enumerate([
        ("run_shell_command", "c1"),
        ("search_notes", "c2"),
        ("web_fetch", "c3"),
    ]):
        ret = patch.parts[i]
        assert isinstance(ret, ToolReturnPart)
        assert ret.tool_name == expected_name
        assert ret.tool_call_id == expected_id
        assert ret.content == "Interrupted by user."


def test_patch_already_answered_calls():
    """History with tool call followed by its return is unchanged."""
    msgs = [
        _user("list files"),
        _tool_call_response("run_shell_command", "c1"),
        _tool_return_request("run_shell_command", "c1", "file.txt"),
    ]
    result = _patch_dangling_tool_calls(msgs)
    # Last message is a ModelRequest (the return), so function exits early
    assert result is msgs


def test_patch_custom_error_message():
    """The error_message parameter flows through to ToolReturnPart.content."""
    msgs = [
        _user("do it"),
        _tool_call_response("run_shell_command", "c1"),
    ]
    result = _patch_dangling_tool_calls(msgs, error_message="Cancelled")

    assert len(result) == 3
    ret = result[2].parts[0]
    assert ret.content == "Cancelled"


def test_patch_preserves_prior_history():
    """Earlier messages are untouched — only appends to the end."""
    prior = [_user(f"msg-{i}") for i in range(5)]
    prior += [_assistant(f"resp-{i}") for i in range(5)]
    dangling = _tool_call_response("run_shell_command", "c1")
    msgs = prior + [dangling]

    result = _patch_dangling_tool_calls(msgs)

    assert len(result) == 12  # 10 prior + 1 dangling response + 1 patch
    # First 11 messages are identical objects
    for i in range(11):
        assert result[i] is msgs[i]


def test_patch_response_with_text_and_tool_call():
    """A response containing both TextPart and ToolCallPart gets patched."""
    response = ModelResponse(parts=[
        TextPart(content="I'll run that for you."),
        ToolCallPart(tool_name="run_shell_command", args='{"cmd":"ls"}', tool_call_id="c1"),
    ])
    msgs = [_user("list files"), response]
    result = _patch_dangling_tool_calls(msgs)

    assert len(result) == 3
    # Original response still has both parts
    assert len(result[1].parts) == 2
    # Patch has one return for the one tool call
    patch = result[2]
    assert len(patch.parts) == 1
    ret = patch.parts[0]
    assert isinstance(ret, ToolReturnPart)
    assert ret.tool_name == "run_shell_command"
    assert ret.tool_call_id == "c1"
