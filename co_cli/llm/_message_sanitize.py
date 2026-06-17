"""Lone-surrogate code point sanitizer for model messages (surrogate recovery)."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _replace_surrogates(text: str) -> str:
    if not _LONE_SURROGATE_RE.search(text):
        return text
    return _LONE_SURROGATE_RE.sub("�", text)


def _sanitize_structure(payload: Any) -> tuple[Any, bool]:
    """Recursively sanitize string leaves in dict/list payloads.

    Returns ``(new_payload, modified)``. Rebuilds dict/list branches only
    where a string actually changed; leaves untouched branches identity-equal
    to the input so downstream change detection stays cheap.
    """
    if isinstance(payload, dict):
        modified = False
        new_dict: dict[Any, Any] = {}
        for key, value in payload.items():
            new_value, changed = _sanitize_structure(value)
            if changed:
                modified = True
            new_dict[key] = new_value
        return (new_dict, True) if modified else (payload, False)
    if isinstance(payload, list):
        modified = False
        new_list: list[Any] = []
        for value in payload:
            new_value, changed = _sanitize_structure(value)
            if changed:
                modified = True
            new_list.append(new_value)
        return (new_list, True) if modified else (payload, False)
    if isinstance(payload, str):
        sanitized = _replace_surrogates(payload)
        return (sanitized, True) if sanitized is not payload else (payload, False)
    return payload, False


def _sanitize_request_parts(msg: ModelRequest) -> ModelRequest:
    new_parts: list = []
    modified = False
    for part in msg.parts:
        if isinstance(
            part, (UserPromptPart, SystemPromptPart, RetryPromptPart, ToolReturnPart)
        ) and isinstance(part.content, str):
            sanitized = _replace_surrogates(part.content)
            if sanitized is not part.content:
                part = replace(part, content=sanitized)
                modified = True
        new_parts.append(part)
    return replace(msg, parts=new_parts) if modified else msg


def _sanitize_response_parts(msg: ModelResponse) -> ModelResponse:
    new_parts: list = []
    modified = False
    for part in msg.parts:
        if isinstance(part, (TextPart, ThinkingPart)):
            if isinstance(part.content, str):
                sanitized = _replace_surrogates(part.content)
                if sanitized is not part.content:
                    part = replace(part, content=sanitized)
                    modified = True
        elif isinstance(part, ToolCallPart):
            if isinstance(part.args, str):
                sanitized = _replace_surrogates(part.args)
                if sanitized is not part.args:
                    part = replace(part, args=sanitized)
                    modified = True
            elif isinstance(part.args, dict):
                new_args, changed = _sanitize_structure(part.args)
                if changed:
                    part = replace(part, args=new_args)
                    modified = True
        new_parts.append(part)
    return replace(msg, parts=new_parts) if modified else msg


def sanitize_surrogate_codepoints_messages(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Replace lone Unicode surrogate code points (U+D800-U+DFFF) with U+FFFD.

    Pure function — called by ``SurrogateRecoveryModel`` as a reactive
    backstop on ``UnicodeEncodeError``.

    Byte-token reasoning models (Qwen3 quantizations, GLM-5, Kimi K2.5)
    occasionally emit lone surrogates that crash json.dumps() with
    UnicodeEncodeError inside the OpenAI SDK.
    """
    result: list[ModelMessage] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            result.append(_sanitize_request_parts(msg))
        elif isinstance(msg, ModelResponse):
            result.append(_sanitize_response_parts(msg))
        else:
            result.append(msg)
    return result
