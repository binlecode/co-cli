"""Single prompt→response LLM call primitive — no tools, no agent loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, TextPart
from pydantic_ai.settings import ModelSettings

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.llm.factory import LlmModel


async def llm_call(
    deps: CoDeps,
    prompt: str,
    *,
    instructions: str | None = None,
    message_history: list[ModelMessage] | None = None,
    model_settings: ModelSettings | None = None,
    model: LlmModel | None = None,
) -> str:
    """Single prompt→response LLM call. No tools, no agent loop.

    Defaults to deps.model.settings_noreason (per-provider noreason config).
    When ``model`` is passed, uses it instead — phase-2 evals route judge calls
    through ``deps.judge_model`` this way so the judge and the agent under test
    don't share a model handle. ``model_settings`` fallback resolves from the
    effective model so a judge call uses the judge's noreason settings.

    Callers that need structured output or tools should use build_task_agent() instead.
    """
    messages: list[ModelMessage] = []
    if instructions:
        messages.append(ModelRequest(parts=[SystemPromptPart(content=instructions)]))
    if message_history:
        messages.extend(message_history)
    messages.append(ModelRequest.user_text_prompt(prompt))

    effective_model = model or deps.model
    response = await model_request(
        effective_model.model,
        messages,
        model_settings=model_settings or effective_model.settings_noreason,
    )
    return "".join(p.content for p in response.parts if isinstance(p, TextPart))
