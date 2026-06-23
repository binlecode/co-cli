"""ORCHESTRATOR_SPEC — declarative record for the always-present primary agent.

Static-part builders are thin closures over CoDeps. build_orchestrator
composes them in order and joins with double-newlines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from co_cli.agent._instructions import (
    current_time_prompt,
    deferred_tool_awareness_prompt,
    safety_prompt,
    skill_manifest_prompt,
    wrap_up_prompt,
)
from co_cli.agent.spec import OrchestratorSpec
from co_cli.context.compaction import proactive_window_processor
from co_cli.context.history_processors import (
    dedup_tool_results,
    elide_old_multimodal_prompts,
    evict_old_tool_results,
    spill_largest_tool_results,
)

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


def _base_instructions_provider(deps: CoDeps) -> str | None:
    from co_cli.context.assembly import build_base_instructions

    return build_base_instructions(deps.config)


def _user_profile_provider(deps: CoDeps) -> str | None:
    if not deps.config.memory.user_profile_enabled:
        return None
    from co_cli.memory.user_profile import read_user_profile

    profile = read_user_profile(deps.user_profile_path).strip()
    if not profile:
        return None
    return f"## USER PROFILE (who the user is)\n\n{profile}"


def _toolset_guidance_provider(deps: CoDeps) -> str | None:
    from co_cli.context.guidance import build_toolset_guidance

    return build_toolset_guidance(deps.tool_catalog)


def _model_profile_overlay_provider(deps: CoDeps) -> str | None:
    """Append the resolved model profile's prompt overlay after the base.

    Resolves ``resolve_model_profile(deps.config.llm)`` and returns that profile's
    ``overlays/<profile>.md`` block (or ``None`` when absent/empty). Append-only:
    the overlay only ADDS to the base — no base content is filtered or removed. The
    per-profile content lands at this single seam, no model-id branch elsewhere.
    """
    from co_cli.config.llm import resolve_model_profile
    from co_cli.context.assembly import build_profile_overlay

    return build_profile_overlay(resolve_model_profile(deps.config.llm))


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
        _base_instructions_provider,
        _model_profile_overlay_provider,
        _user_profile_provider,
        _toolset_guidance_provider,
        _personality_critique_provider,
    ),
    per_turn_instructions=(
        safety_prompt,
        wrap_up_prompt,
        current_time_prompt,
        deferred_tool_awareness_prompt,
        skill_manifest_prompt,
    ),
    history_processors=(
        elide_old_multimodal_prompts,
        dedup_tool_results,
        evict_old_tool_results,
        spill_largest_tool_results,
        proactive_window_processor,
    ),
)
