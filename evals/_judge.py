"""Common LLM judge helper for eval quality checks.

Each eval defines a case-specific Pydantic result type and a domain-specific
prompt, then calls run_judge() to obtain a structured quality verdict.

The judge uses noreason model settings (no chain-of-thought overhead) and
falls back to the base settings when noreason is not configured.
"""

from __future__ import annotations

import logging

import anyio
from pydantic import BaseModel
from pydantic_ai import Agent

from co_cli.llm.factory import LlmModel

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: float = 30.0
DEFAULT_SYSTEM_PROMPT: str = (
    "You are a strict quality evaluator. Assess the provided content honestly "
    "and return a structured judgment. Score conservatively."
)


async def run_judge[T: BaseModel](
    prompt: str,
    result_type: type[T],
    *,
    llm_model: LlmModel,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[T | None, str | None]:
    """Run an LLM judge and return (result, error_message).

    Uses noreason model settings to avoid reasoning overhead; falls back to
    base settings when noreason is not configured.

    Returns (None, error_msg) on timeout or failure — callers treat this as a
    skip, not a hard failure.
    """
    model_settings = llm_model.settings_noreason or llm_model.settings
    judge_agent: Agent[None, T] = Agent(
        model=llm_model.model,
        output_type=result_type,
        model_settings=model_settings,
        system_prompt=system_prompt,
    )
    try:
        with anyio.fail_after(timeout):
            result = await judge_agent.run(prompt)
        return result.output, None
    except TimeoutError:
        return None, f"judge call timed out ({timeout:.0f}s)"
    except Exception as exc:
        logger.debug("Judge call failed: %s", exc, exc_info=True)
        return None, f"judge call failed: {exc}"
