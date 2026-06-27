"""Agent spec types — declarative records for orchestrator and task agents.

Daemons are task agents — the in-turn vs daemon distinction is lifecycle
ownership (which runner you call), not spec shape. Both share TaskAgentSpec.
run_standalone never depth-checks — daemons are top-level.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel
    from pydantic_ai import RunContext

    from co_cli.deps import CoDeps


class SurfaceModeEnum(StrEnum):
    """How a task agent's tool surface is constructed.

    FLAT_EXACT (default): a plain toolset of exactly ``tool_names``, every tool
    registered ``requires_approval=False`` — the closed-surface specialist
    (daemon specs name their exact tools, including DEFERRED-tier ones directly).

    VISIBILITY_MODEL: the orchestrator's visibility surface (native + MCP,
    DEFERRED hidden until ``tool_view``-revealed, real approval flags) minus a
    structural blocklist — the open general worker (the delegated agent).
    ``tool_names`` is ignored; the delegated agent decides which tools it needs
    and self-loads deferred ones.
    """

    FLAT_EXACT = "flat-exact"
    VISIBILITY_MODEL = "visibility-model"


@dataclass(frozen=True)
class OrchestratorSpec:
    """Declarative spec for the always-present primary agent.

    Singleton — toolset is read from deps.toolset by build_orchestrator (no
    factory field). Output type is fixed [str, DeferredToolRequests]; retries
    from deps.config.tool_retries.
    """

    static_instruction_builders: tuple[Callable[[CoDeps], str | None], ...]
    per_turn_instructions: tuple[Callable[[RunContext[CoDeps]], str], ...]
    history_processors: tuple[Callable[..., Any], ...]


@dataclass(frozen=True)
class TaskAgentSpec:
    """Declarative spec for a focused task agent (in-turn delegation or standalone daemon).

    surface_mode selects how the tool surface is built (see SurfaceModeEnum). In
    FLAT_EXACT mode (default) tool_names is resolved against TOOL_REGISTRY_BY_NAME
    at build time; unknown names fail loud, and every resolved tool is registered
    with requires_approval=False. In VISIBILITY_MODEL mode tool_names is ignored —
    the surface is the orchestrator's native+MCP visibility surface minus a
    structural blocklist, so tools carry their real requires_approval flags and
    DEFERRED tools are hidden until revealed via tool_view.

    include_skill_manifest=True prepends the rendered skill manifest to the
    instructions string (used by SKILL_REVIEW_SPEC in daemons/dream/_reviewer.py).
    """

    name: str
    instructions: Callable[[CoDeps], str]
    tool_names: tuple[str, ...]
    output_type: type[BaseModel]
    default_budget: int
    include_skill_manifest: bool = False
    surface_mode: SurfaceModeEnum = SurfaceModeEnum.FLAT_EXACT
