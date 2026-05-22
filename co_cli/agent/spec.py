"""Agent spec types — declarative records for orchestrator and task agents.

Daemons are task agents — the in-turn vs daemon distinction is lifecycle
ownership (which runner you call), not spec shape. Both share TaskAgentSpec.
run_in_turn always depth-checks; run_standalone never does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel
    from pydantic_ai import RunContext

    from co_cli.deps import CoDeps


@dataclass(frozen=True)
class OrchestratorSpec:
    """Declarative spec for the always-present primary agent.

    Singleton — toolset is read from deps.toolset by build_orchestrator (no
    factory field). Output type is fixed [str, DeferredToolRequests];
    capabilities is fixed [CoToolLifecycle()]; retries from
    deps.config.tool_retries.
    """

    name: str
    static_instruction_builders: tuple[Callable[[CoDeps], str | None], ...]
    per_turn_instructions: tuple[Callable[[RunContext[CoDeps]], str], ...]
    history_processors: tuple[Callable[..., Any], ...]


@dataclass(frozen=True)
class TaskAgentSpec:
    """Declarative spec for a focused task agent (in-turn delegation or standalone daemon).

    tool_names is resolved against TOOL_REGISTRY_BY_NAME at build time. Unknown
    names fail loud. Config-conditional tools (Google/Obsidian) drop out when
    credentials are absent. All resolved tools are registered with
    requires_approval=False.

    error_message is raised inside ModelRetry on in-turn failure. Unused by
    run_standalone — daemons propagate plain exceptions.

    include_skill_manifest=True prepends the rendered skill manifest to the
    instructions string (used by SKILL_REVIEW_SPEC in daemons/dream/_reviewer.py).
    """

    name: str
    instructions: Callable[[CoDeps], str]
    tool_names: tuple[str, ...]
    output_type: type[BaseModel]
    default_budget: int
    error_message: str
    include_skill_manifest: bool = False
