"""Agent construction core — ToolRegistry, build_tool_registry(), build_agent()."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.combined import CombinedToolset

from co_cli.config._core import Settings
from co_cli.context._history import (
    compact_assistant_responses,
    detect_safety_issues,
    inject_opening_context,
    summarize_history_window,
    truncate_tool_results,
)
from co_cli.context._tool_lifecycle import CoToolLifecycle
from co_cli.deps import CoDeps, ToolInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolRegistry:
    """Immutable return value of build_tool_registry().

    Holds the combined filtered toolset (native + MCP, approval-resume filter applied),
    the raw MCP toolsets (for bootstrap lifecycle management), and the tool_index
    (native entries; MCP entries added later by discover_mcp_tools()).
    """

    toolset: AbstractToolset[CoDeps]
    mcp_toolsets: list
    tool_index: dict[str, ToolInfo]


def build_tool_registry(config: Settings) -> ToolRegistry:
    """Build the tool registry from config.

    Pure config — no IO. Called once in create_deps().
    Combines native and MCP toolsets under a single approval-resume filter.
    MCP tool_index entries are added later by discover_mcp_tools().
    """
    from co_cli.agent._mcp import _build_mcp_toolsets
    from co_cli.agent._native_toolset import _approval_resume_filter, _build_native_toolset

    native_toolset, native_index = _build_native_toolset(config)
    mcp_toolsets = _build_mcp_toolsets(config)

    # Combine all toolsets under one filter so approval-resume narrowing
    # applies uniformly to native and MCP tools.
    combined = CombinedToolset([native_toolset, *mcp_toolsets])
    filtered = combined.filtered(_approval_resume_filter)

    return ToolRegistry(
        toolset=filtered,
        mcp_toolsets=mcp_toolsets,
        tool_index=native_index,
    )


def build_agent(
    *,
    config: Settings,
    model: Any = None,
    # Orchestrator path
    tool_registry: ToolRegistry | None = None,
    # Delegation tool path
    instructions: str | None = None,
    tool_fns: list[Callable] | None = None,
    output_type: type | None = None,
) -> Agent[CoDeps, Any]:
    """Build an agent for the orchestrator or a delegation tool.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
        model: Pre-built LlmModel or raw pydantic-ai model. When omitted,
            built from config internally (used by evals and tests).
        tool_registry: Pre-built tool registry. Provide for the orchestrator path.
            When omitted and no delegation params given, built from config internally.
        instructions: Static instruction string for delegation tools.
        tool_fns: Tool functions to register on a delegation agent.
        output_type: Required for delegation path — the Pydantic output model type.

    Orchestrator path: tool_registry is provided (or built internally from config).
    Delegation path: output_type is provided; builds a minimal agent.
    Raises ValueError if delegation path intent detected but output_type is None.
    """
    is_delegation = output_type is not None or instructions is not None or bool(tool_fns or [])

    if is_delegation and output_type is None:
        raise ValueError(
            "Delegation path requires output_type. "
            "Pass tool_registry instead for the orchestrator path."
        )

    # Normalize model: accept LlmModel or raw pydantic-ai model.
    # Orchestrator path: model=None → build from config.
    # Delegation path: model is expected to be a raw pydantic-ai model.
    from co_cli.llm._factory import LlmModel as _LlmModel

    raw_model = model
    llm_settings = None
    if model is None:
        from co_cli.llm._factory import build_model

        _llm = build_model(config.llm)
        raw_model = _llm.model
        llm_settings = _llm.settings
    elif isinstance(model, _LlmModel):
        raw_model = model.model
        llm_settings = model.settings

    if not is_delegation:
        # Orchestrator path
        if tool_registry is None:
            tool_registry = build_tool_registry(config)

        from co_cli.agent._instructions import (
            add_always_on_memories,
            add_category_awareness_prompt,
            add_current_date,
            add_personality_memories,
            add_shell_guidance,
        )
        from co_cli.prompts._assembly import build_static_instructions

        static_instructions = build_static_instructions(config)

        # Static layer — set once at agent construction; does not change between turns.
        # Single filtered toolset (native + MCP combined); SDK adds ToolSearchToolset automatically.
        agent: Agent[CoDeps, Any] = Agent(
            raw_model,
            deps_type=CoDeps,
            instructions=static_instructions,
            model_settings=llm_settings,
            retries=config.tool_retries,
            output_type=[str, DeferredToolRequests],
            history_processors=[
                truncate_tool_results,
                compact_assistant_responses,
                detect_safety_issues,
                inject_opening_context,
                summarize_history_window,
            ],
            toolsets=[tool_registry.toolset],
            capabilities=[CoToolLifecycle()],
        )

        # Conditional prompt layers — runtime-gated (fresh per turn, never accumulated)
        agent.instructions(add_current_date)
        agent.instructions(add_shell_guidance)
        agent.instructions(add_always_on_memories)
        agent.instructions(add_personality_memories)
        agent.instructions(add_category_awareness_prompt)

        return agent

    else:
        # Delegation path: minimal agent with inline instructions and tool_fns.
        delegation_agent: Agent[CoDeps, Any] = Agent(
            raw_model,
            deps_type=CoDeps,
            output_type=output_type,
            instructions=instructions,
            retries=config.tool_retries,
        )
        for fn in tool_fns or []:
            delegation_agent.tool(fn, requires_approval=False)  # type: ignore[arg-type]  # pydantic-ai tool() overloads require exact AgentDepsT match
        return delegation_agent
