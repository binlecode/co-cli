"""Single prompt→response LLM call primitive — no tools, no agent loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, TextPart
from pydantic_ai.settings import ModelSettings

from co_cli.observability.capability import serialize_messages, serialize_response
from co_cli.observability.tracing import pop_span, push_span
from co_cli.session.usage import record_usage

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

    Emits an ``llm_call <model>`` span (``kind="model"``) at attribute parity with
    the agent-path ``chat`` span, so direct calls (compaction summarizer, dream
    merges, eval judges) are trackable in ``co tail`` / ``co trace``. A distinct
    name keeps direct calls separable from agent turns in a trace. The span nests
    under any active parent (e.g. ``compaction.proactive_check``) via push_span.

    Callers that need structured output or tools should use build_task_agent() instead.
    """
    messages: list[ModelMessage] = []
    if instructions:
        messages.append(ModelRequest(parts=[SystemPromptPart(content=instructions)]))
    if message_history:
        messages.extend(message_history)
    messages.append(ModelRequest.user_text_prompt(prompt))

    effective_model = model or deps.model
    model_name = effective_model.model.model_name

    push_span(
        f"llm_call {model_name}",
        kind="model",
        attributes={
            "co.model.name": model_name,
            "co.model.input": serialize_messages(messages),
        },
    )
    try:
        response = await model_request(
            effective_model.model,
            messages,
            model_settings=model_settings or effective_model.settings_noreason,
        )
    except BaseException as exc:
        pop_span(status="ERROR", status_msg=str(exc))
        raise
    usage = response.usage
    record_usage(deps, usage)
    pop_span(
        attributes={
            "co.model.output": serialize_response(response),
            "co.model.tokens.input": getattr(usage, "input_tokens", 0),
            "co.model.tokens.output": getattr(usage, "output_tokens", 0),
            "co.model.name": response.model_name,
            "co.model.finish_reason": str(response.finish_reason)
            if response.finish_reason
            else None,
        },
    )
    return "".join(p.content for p in response.parts if isinstance(p, TextPart))
