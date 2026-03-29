"""Memory consolidator — single-call consolidation planning.

Module-level Agent singleton for fast write-time dedup and contradiction resolution.
Single LLM call with token cap for sub-2s latency on non-thinking models.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError
from pydantic_ai.settings import ModelSettings

from co_cli._model_factory import ResolvedModel

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_consolidator.md"
_CONSOLIDATION_MAX_TOKENS = 512


class MemoryAction(BaseModel):
    """A single action in a consolidation plan."""

    action: Literal["ADD", "UPDATE", "DELETE", "NONE"]
    target_alias: str | None = None


class ConsolidationPlan(BaseModel):
    """Full consolidation plan produced by the resolver."""

    actions: list[MemoryAction]


_consolidation_agent: Agent[None, ConsolidationPlan] = Agent(
    output_type=ConsolidationPlan,
    system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
    retries=0,
    output_retries=0,
)


async def consolidate(
    candidate: str,
    existing: list[Any],
    resolved: ResolvedModel,
    timeout_seconds: float | None = None,
) -> ConsolidationPlan:
    """Consolidate a candidate memory against existing memories in a single LLM call.

    Args:
        candidate: Raw memory content to consolidate.
        existing: Existing MemoryEntry objects to compare against (aliased M1, M2...).
        resolved: Pre-built model + settings for the consolidation role.
        timeout_seconds: Per-call timeout. 0 = immediate (test mode). None = no timeout.

    Returns:
        ConsolidationPlan with action decisions. Falls back to ADD on ValidationError.

    Raises:
        asyncio.TimeoutError: Propagated to caller for on_failure handling.
        ModelHTTPError, ModelAPIError: Propagated to caller.
    """
    _add_fallback = ConsolidationPlan(actions=[MemoryAction(action="ADD")])

    # Build token-capped settings — shallow-copy extra_body to avoid corrupting registry cache
    base: dict[str, Any] = dict(resolved.settings) if resolved.settings is not None else {}
    base["max_tokens"] = _CONSOLIDATION_MAX_TOKENS
    extra_body = dict(base.get("extra_body") or {})
    extra_body["num_predict"] = _CONSOLIDATION_MAX_TOKENS
    base["extra_body"] = extra_body
    call_settings = ModelSettings(**base)

    # Build user prompt with alias entries
    alias_entries = []
    for i, entry in enumerate(existing):
        alias = f"M{i + 1}"
        alias_entries.append(
            f'{{"alias": "{alias}", "content": {entry.content!r}, "tags": {entry.tags!r}}}'
        )
    aliases_text = "\n".join(alias_entries) if alias_entries else "(none)"
    user_prompt = (
        f"Candidate:\n{candidate}\n\n"
        f"Existing memories:\n{aliases_text}"
    )

    try:
        coro = _consolidation_agent.run(
            user_prompt, model=resolved.model, model_settings=call_settings
        )
        if timeout_seconds is not None:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            result = await coro

        plan = result.output
        action_counts: dict[str, int] = {}
        for a in plan.actions:
            action_counts[a.action] = action_counts.get(a.action, 0) + 1
        logger.info(
            "consolidation resolved",
            extra={
                "candidate_set_size": len(existing),
                "actions": action_counts,
                "fallback": False,
            },
        )
        return plan

    except asyncio.TimeoutError:
        raise
    except (ModelHTTPError, ModelAPIError):
        raise
    except Exception as e:
        logger.info(
            "consolidation fallback",
            extra={
                "candidate_set_size": len(existing),
                "actions": {"ADD": 1},
                "fallback": True,
            },
        )
        logger.debug(f"consolidate fallback reason: {e}")
        return _add_fallback
