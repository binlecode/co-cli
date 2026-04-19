"""Single prompt→response LLM call primitive — no tools, no agent loop."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.settings import ModelSettings

from co_cli.deps import CoDeps


async def llm_call(
    deps: CoDeps,
    prompt: str,
    *,
    instructions: str | None = None,
    message_history: list[ModelMessage] | None = None,
    output_type: type[Any] = str,
    model_settings: ModelSettings | None = None,
) -> Any:
    """Single prompt→response LLM call. No tools, no agent loop.

    Defaults to deps.model.settings_noreason (per-provider noreason config).
    Callers that need reasoning should build an agent via build_agent() instead.
    """
    agent: Agent[None, Any] = Agent(
        deps.model.model,
        output_type=output_type,
        instructions=instructions,
    )
    result = await agent.run(
        prompt,
        message_history=message_history,
        model_settings=model_settings or deps.model.settings_noreason,
    )
    return result.output
