"""Single prompt→response LLM call primitive — no tools, no agent loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, TextPart
from pydantic_ai.settings import ModelSettings

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


async def llm_call(
    deps: CoDeps,
    prompt: str,
    *,
    instructions: str | None = None,
    message_history: list[ModelMessage] | None = None,
    model_settings: ModelSettings | None = None,
) -> str:
    """Single prompt→response LLM call. No tools, no agent loop.

    Defaults to deps.model.settings_noreason (per-provider noreason config).
    Callers that need structured output or reasoning should use build_agent() instead.
    """
    messages: list[ModelMessage] = []
    if instructions:
        messages.append(ModelRequest(parts=[SystemPromptPart(content=instructions)]))
    if message_history:
        messages.extend(message_history)
    messages.append(ModelRequest.user_text_prompt(prompt))

    response = await model_request(
        deps.model.model,
        messages,
        model_settings=model_settings or deps.model.settings_noreason,
    )
    return "".join(p.content for p in response.parts if isinstance(p, TextPart))
