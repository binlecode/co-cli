"""Agent builders — build_orchestrator (singleton primary agent) + build_task_agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, DeferredToolRequests

from co_cli.deps import CoDeps

if TYPE_CHECKING:
    from co_cli.agent.spec import OrchestratorSpec, TaskAgentSpec
    from co_cli.context.orchestrate import SessionAgent


def build_orchestrator(spec: OrchestratorSpec, deps: CoDeps) -> SessionAgent:
    """Build the orchestrator agent from a spec.

    Composes static instructions by calling each builder in order, registers
    per-turn instructions via agent.instructions(...), and attaches history
    processors. Toolset is read from deps.toolset directly (singleton) — the
    call-seam wrapper on that toolset hosts the tool span, per-request cap, and
    MCP spill. Output type is fixed [str, DeferredToolRequests]; retries from
    deps.config.tool_retries.
    """
    if deps.toolset is None:
        raise ValueError("build_orchestrator requires deps.toolset to be set.")
    if deps.model is None:
        raise ValueError("build_orchestrator requires deps.model to be set.")

    raw_model = deps.model.model
    llm_settings = deps.model.settings

    parts: list[str] = []
    for builder in spec.static_instruction_builders:
        piece = builder(deps)
        if piece:
            parts.append(piece)
    static_instructions = "\n\n".join(parts)

    agent: SessionAgent = Agent(
        raw_model,
        deps_type=CoDeps,
        instructions=static_instructions,
        model_settings=llm_settings,
        tool_retries=deps.config.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=list(spec.history_processors),
        toolsets=[deps.toolset],
    )

    for per_turn in spec.per_turn_instructions:
        agent.instructions(per_turn)

    return agent


def build_task_agent(spec: TaskAgentSpec, deps: CoDeps, model: Any) -> Agent[CoDeps, Any]:
    """Build a task agent from a spec.

    Resolves spec.tool_names against TOOL_REGISTRY_BY_NAME. Unknown tool names
    raise ValueError at build time. All resolved tools are registered with
    requires_approval=False.

    When spec.include_skill_manifest is True, prepends the rendered skill
    manifest to spec.instructions(deps) output.

    Args:
        spec: The task agent spec.
        deps: Runtime deps — used for config lookups and skill manifest rendering.
        model: Raw pydantic-ai model (not LlmModel).
    """
    from pydantic_ai.toolsets import FunctionToolset

    from co_cli.agent.toolset import _CallSeamToolset
    from co_cli.tools.agent_tool import TOOL_REGISTRY_BY_NAME

    tool_fns: list[Any] = []
    for name in spec.tool_names:
        fn = TOOL_REGISTRY_BY_NAME.get(name)
        if fn is None:
            raise ValueError(f"{spec.name}: unknown tool {name!r}")
        tool_fns.append(fn)

    instructions = spec.instructions(deps)
    if spec.include_skill_manifest:
        from co_cli.skills.manifest import render_skill_manifest

        manifest = render_skill_manifest(deps.skill_catalog, deps.skills_dir, deps.user_skills_dir)
        if manifest:
            instructions = f"{manifest}\n\n{instructions}"

    # Route the task agent's tools through the same call_tool wrapper as the
    # orchestrator so subagent tool calls get the tool span, per-call cap, and
    # co.tool.* attributes (parity with the deleted capability on task agents).
    toolset: FunctionToolset[CoDeps] = FunctionToolset()
    for fn in tool_fns:
        toolset.add_function(fn, requires_approval=False)

    agent: Agent[CoDeps, Any] = Agent(
        model,
        deps_type=CoDeps,
        output_type=spec.output_type,
        instructions=instructions,
        tool_retries=deps.config.tool_retries,
        toolsets=[_CallSeamToolset(toolset)],
    )
    return agent
