"""Agent construction core — toolset composition helpers + build_agent()."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.combined import CombinedToolset

from co_cli.config.core import Settings
from co_cli.context.compaction import proactive_window_processor
from co_cli.context.history_processors import (
    dedup_tool_results,
    enforce_request_size,
    evict_old_tool_results,
    sanitize_surrogate_codepoints,
)
from co_cli.deps import CoDeps, ToolInfo
from co_cli.tools.lifecycle import CoToolLifecycle

if TYPE_CHECKING:
    from co_cli.agents.mcp import MCPToolsetEntry


def build_native_toolset(
    config: Settings,
) -> tuple[AbstractToolset[CoDeps], dict[str, ToolInfo]]:
    """Build the unfiltered native toolset and its tool_index.

    Pure config — no IO. Returns the native FunctionToolset and a fresh
    dict copy of the native tool metadata. Caller is responsible for
    combining with MCP toolsets (if any) and applying the approval-resume
    filter via assemble_routing_toolset().
    """
    from co_cli.agents._native_toolset import _build_native_toolset

    native_toolset, native_index = _build_native_toolset(config)
    return native_toolset, dict(native_index)


def build_mcp_entries(config: Settings, tool_index: dict[str, ToolInfo]) -> list[MCPToolsetEntry]:
    """Build MCP toolset entries wrapped for sequential-flag propagation.

    Not yet connected. Each entry's toolset is wrapped with _SequentialMCPToolset
    so that ToolDefinition.sequential is patched from tool_index[name].is_concurrent_safe
    at step time. tool_index is held by reference — discover_mcp_tools() populates
    MCP entries into it after connection, before the first get_tools() call.
    """
    from co_cli.agents.mcp import _build_mcp_toolsets, _SequentialMCPToolset

    entries = _build_mcp_toolsets(config)
    return [
        replace(entry, toolset=_SequentialMCPToolset(entry.toolset, tool_index))
        for entry in entries
    ]


def assemble_routing_toolset(
    native_toolset: AbstractToolset[CoDeps],
    mcp_toolsets: list[AbstractToolset[CoDeps]],
) -> AbstractToolset[CoDeps]:
    """Combine native + connected MCP toolsets and apply the approval-resume filter."""
    from co_cli.agents._native_toolset import _approval_resume_filter

    combined = CombinedToolset([native_toolset, *mcp_toolsets])
    return combined.filtered(_approval_resume_filter)


def discover_delegation_tools(profile: str, config: Settings) -> list[Callable]:
    """Return tool functions tagged for the given delegation profile, filtered by config."""
    # importing _native_toolset triggers all tool-module imports → TOOL_REGISTRY is fully populated
    from co_cli.agents._native_toolset import _config_requirement_met
    from co_cli.tools.agent_tool import AGENT_TOOL_ATTR, TOOL_REGISTRY

    result = []
    for fn in TOOL_REGISTRY:
        info: ToolInfo = getattr(fn, AGENT_TOOL_ATTR)
        if info.delegation is None or profile not in info.delegation:
            continue
        if not _config_requirement_met(info, config):
            continue
        result.append(fn)
    return result


def build_agent(
    *,
    config: Settings,
    model: Any = None,
    toolset: AbstractToolset[CoDeps] | None = None,
    tool_index: dict[str, ToolInfo] | None = None,
    instructions: str | None = None,
    tool_fns: list[Callable] | None = None,
    output_type: type | None = None,
    skill_manifest: str | None = None,
) -> Agent[CoDeps, Any]:
    """Build an agent for the orchestrator or a delegation tool.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
        model: Pre-built LlmModel or raw pydantic-ai model. When omitted,
            built from config internally.
        toolset: Routing toolset for the orchestrator path. Required (with tool_index)
            for the orchestrator path; ignored on the delegation path.
        tool_index: Tool metadata for guidance prompts and category hints. Required
            (with toolset) for the orchestrator path; ignored on the delegation path.
        instructions: Static instruction string for delegation tools.
        tool_fns: Tool functions to register on a delegation agent.
        output_type: Required for delegation path — the Pydantic output model type.
        skill_manifest: Optional pre-rendered bundled-skill manifest string. Injected
            after tool guidance in the orchestrator path; ignored for delegation agents.

    Orchestrator path: caller passes toolset + tool_index (constructed via
    build_native_toolset() + optional MCP composition).
    Delegation path: output_type is provided; builds a minimal agent.
    Raises ValueError if delegation path intent detected but output_type is None,
    or if orchestrator path is missing toolset/tool_index.
    """
    is_delegation = output_type is not None or instructions is not None or bool(tool_fns)

    if is_delegation and output_type is None:
        raise ValueError("output_type is required when instructions or tool_fns is passed.")

    if not is_delegation and (toolset is None or tool_index is None):
        raise ValueError(
            "Orchestrator path requires both toolset and tool_index. "
            "Use build_native_toolset(config) (or bootstrap.create_deps) to construct them."
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
        from co_cli.agents._instructions import current_time_prompt, safety_prompt
        from co_cli.context.assembly import build_static_instructions
        from co_cli.context.guidance import build_toolset_guidance
        from co_cli.tools.deferred_prompt import build_category_awareness_prompt

        static_parts = [build_static_instructions(config)]

        tool_guidance = build_toolset_guidance(tool_index)
        if tool_guidance:
            static_parts.append(tool_guidance)

        category_hint = build_category_awareness_prompt(tool_index)
        if category_hint:
            static_parts.append(category_hint)

        if skill_manifest:
            static_parts.append(skill_manifest)

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
                enforce_request_size,
                proactive_window_processor,
                sanitize_surrogate_codepoints,
            ],
            toolsets=[toolset],
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
