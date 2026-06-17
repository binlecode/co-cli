"""Tests for lone-surrogate code point sanitization of model messages."""

from pydantic_ai.messages import ModelResponse, ToolCallPart

from co_cli.llm._message_sanitize import sanitize_surrogate_codepoints_messages


def test_sanitize_nested_dict_list_args():
    """sanitize_surrogate_codepoints_messages walks nested dict/list structures."""
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="batch_op",
                    args={
                        "items": [
                            {"name": "x\ud800"},
                            {"name": "clean"},
                            {"nested": {"deep": "z\udfff"}},
                        ]
                    },
                    tool_call_id="c2",
                )
            ]
        )
    ]
    result = sanitize_surrogate_codepoints_messages(messages)
    args = result[0].parts[0].args
    assert args["items"][0]["name"] == "x�"
    assert args["items"][1]["name"] == "clean"
    assert args["items"][2]["nested"]["deep"] == "z�"


def test_sanitize_clean_args_unchanged():
    """sanitize_surrogate_codepoints_messages is identity on clean dict args (no allocation)."""
    clean_args = {"path": "/a.txt", "items": [{"name": "clean"}]}
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name="t", args=clean_args, tool_call_id="c3")])
    ]
    result = sanitize_surrogate_codepoints_messages(messages)
    assert result[0].parts[0].args is clean_args
