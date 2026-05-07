"""Agent construction core — ToolRegistry, build_tool_registry(), build_agent()."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.combined import CombinedToolset

from co_cli.config.core import Settings
from co_cli.context.compaction import (
    dedup_tool_results,
    evict_old_tool_results,
    proactive_window_processor,
)
from co_cli.context.history_processors import enforce_turn_budget, sanitize_surrogate_codepoints
from co_cli.deps import CoDeps, ToolInfo
from co_cli.tools.lifecycle import CoToolLifecycle


@dataclass(frozen=True)
class ToolRegistry:
    """Immutable return value of build_tool_registry().

    Holds the combined filtered toolset (native + MCP, approval-resume filter applied),
    the raw MCP toolsets (for bootstrap lifecycle management), and the tool_index
    (native entries; MCP entries added later by discover_mcp_tools()).
    """

    toolset: AbstractToolset[CoDeps]
    mcp_toolsets: list  # list[MCPToolsetEntry] — pydantic_ai.mcp type; avoid circular import
    tool_index: dict[str, ToolInfo]


def build_tool_registry(config: Settings) -> ToolRegistry:
    """Build the tool registry from config.

    Pure config — no IO. Called once in create_deps().
    Combines native and MCP toolsets under a single approval-resume filter.
    MCP tool_index entries are added later by discover_mcp_tools().
    """
    from co_cli.agent._native_toolset import _approval_resume_filter, _build_native_toolset
    from co_cli.agent.mcp import _build_mcp_toolsets

    native_toolset, native_index = _build_native_toolset(config)
    mcp_entries = _build_mcp_toolsets(config)

    combined = CombinedToolset([native_toolset, *(e.toolset for e in mcp_entries)])
    filtered = combined.filtered(_approval_resume_filter)

    return ToolRegistry(
        toolset=filtered,
        mcp_toolsets=mcp_entries,
        tool_index=native_index,
    )


def build_agent(
    *,
    config: Settings,
    model: Any = None,
    tool_registry: ToolRegistry | None = None,
    instructions: str | None = None,
    tool_fns: list[Callable] | None = None,
    output_type: type | None = None,
) -> Agent[CoDeps, Any]:
    """Build an agent for the orchestrator or a delegation tool.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
        model: Pre-built LlmModel or raw pydantic-ai model. When omitted,
            built from config internally.
        tool_registry: Pre-built tool registry. Provide for the orchestrator path.
            When omitted and no delegation params given, built from config internally.
        instructions: Static instruction string for delegation tools.
        tool_fns: Tool functions to register on a delegation agent.
        output_type: Required for delegation path — the Pydantic output model type.

    Orchestrator path: tool_registry is provided (or built internally from config).
    Delegation path: output_type is provided; builds a minimal agent.
    Raises ValueError if delegation path intent detected but output_type is None.
    """
    is_delegation = output_type is not None or instructions is not None or bool(tool_fns)

    if is_delegation and output_type is None:
        raise ValueError(
            "Delegation path requires output_type. "
            "Pass tool_registry instead for the orchestrator path."
        )

    from co_cli.llm.factory import LlmModel as _LlmModel

    llm_settings = None
    if model is None:
        from co_cli.llm.factory import build_model

        _llm = build_model(config.llm)
        raw_model = _llm.model
        llm_settings = _llm.settings
    elif isinstance(model, _LlmModel):
        raw_model = model.model
        llm_settings = model.settings
    else:
        raw_model = model

    if not is_delegation:
        if tool_registry is None:
            tool_registry = build_tool_registry(config)

        from co_cli.agent._instructions import current_time_prompt, safety_prompt
        from co_cli.context.assembly import build_static_instructions
        from co_cli.context.guidance import build_toolset_guidance
        from co_cli.tools.deferred_prompt import build_category_awareness_prompt

        static_parts = [build_static_instructions(config)]

        tool_guidance = build_toolset_guidance(tool_registry.tool_index)
        if tool_guidance:
            static_parts.append(tool_guidance)

        category_hint = build_category_awareness_prompt(tool_registry.tool_index)
        if category_hint:
            static_parts.append(category_hint)

        if config.personality:
            from co_cli.personality.prompts.loader import load_soul_critique

            crit = load_soul_critique(config.personality)
            if crit:
                static_parts.append(f"## Review lens\n\n{crit}")

        static_instructions = "\n\n".join(static_parts)

        agent: Agent[CoDeps, Any] = Agent(
            raw_model,
            deps_type=CoDeps,
            instructions=static_instructions,
            model_settings=llm_settings,
            retries=config.tool_retries,
            output_type=[str, DeferredToolRequests],
            history_processors=[
                dedup_tool_results,
                evict_old_tool_results,
                enforce_turn_budget,
                proactive_window_processor,
                sanitize_surrogate_codepoints,
            ],
            toolsets=[tool_registry.toolset],
            capabilities=[CoToolLifecycle()],
        )

        agent.instructions(safety_prompt)
        agent.instructions(current_time_prompt)

        return agent

    delegation_agent: Agent[CoDeps, Any] = Agent(
        raw_model,
        deps_type=CoDeps,
        output_type=output_type,
        instructions=instructions,
        retries=config.tool_retries,
        capabilities=[CoToolLifecycle()],
    )
    for fn in tool_fns or []:
        delegation_agent.tool(fn, requires_approval=False)  # type: ignore[arg-type]  # pydantic-ai tool() overloads require exact AgentDepsT match
    return delegation_agent
