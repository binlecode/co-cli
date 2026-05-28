"""ORCHESTRATOR_SPEC — declarative record for the always-present primary agent.

Static-part builders are thin closures over CoDeps. build_orchestrator
composes them in order and joins with double-newlines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from co_cli.agent._instructions import (
    current_time_prompt,
    safety_prompt,
    skill_manifest_prompt,
    tool_category_awareness_prompt,
)
from co_cli.agent.spec import OrchestratorSpec
from co_cli.context.compaction import proactive_window_processor
from co_cli.context.history_processors import (
    dedup_tool_results,
    enforce_request_size,
    evict_old_tool_results,
)

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


def _static_instructions_provider(deps: CoDeps) -> str | None:
    from co_cli.context.assembly import build_static_instructions

    return build_static_instructions(deps.config)


def _toolset_guidance_provider(deps: CoDeps) -> str | None:
    from co_cli.context.guidance import build_toolset_guidance

    return build_toolset_guidance(deps.tool_index)


def _personality_critique_provider(deps: CoDeps) -> str | None:
    if not deps.config.personality:
        return None
    from co_cli.personality.prompts.loader import load_soul_critique

    crit = load_soul_critique(deps.config.personality)
    if not crit:
        return None
    return f"## Review lens\n\n{crit}"


ORCHESTRATOR_SPEC = OrchestratorSpec(
    name="orchestrator",
    static_instruction_builders=(
        _static_instructions_provider,
        _toolset_guidance_provider,
        _personality_critique_provider,
    ),
    per_turn_instructions=(
        safety_prompt,
        current_time_prompt,
        tool_category_awareness_prompt,
        skill_manifest_prompt,
    ),
    history_processors=(
        dedup_tool_results,
        evict_old_tool_results,
        enforce_request_size,
        proactive_window_processor,
    ),
)
