"""ALWAYS-visibility tool-schema budget measurement — single source of truth.

Every ALWAYS-visibility tool's ToolDefinition (name + description + minified-parameters-JSON)
ships in every turn's static prefix, so it is part of the floor-inclusive prefill the provider
counts on every request. This module measures that bucket once, from the assembled toolset, and is
consumed by two callers that must agree on the number:

  - ``co_cli/bootstrap/core.py`` — folds the measured chars into ``deps.static_floor_tokens`` so the
    compaction triggers can account for the floor when the provider report is stale.
  - ``tests/test_orchestrator_schema_budget.py`` — the regression guard that pins the bucket size.

Measurement: walk the assembled toolset to the inner ``FunctionToolset.tools`` dict, call each
tool's ``prepare_tool_def(ctx)`` (respects per-turn prepare callbacks), and cross-reference
visibility via ``deps.tool_index[name].visibility``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai._run_context import RunContext
from pydantic_ai.result import RunUsage

from co_cli.deps import CoDeps, VisibilityPolicyEnum


@dataclass(frozen=True)
class AlwaysSchemaBudget:
    """Measured ALWAYS-visibility tool-schema sizes (in chars)."""

    total_chars: int
    per_tool_chars: dict[str, int]
    tool_count: int
    empty_descriptions: list[str]


def _unwrap_function_toolset(toolset: Any) -> Any:
    """Walk toolset wrappers to the inner FunctionToolset holding a .tools dict.

    Handles the FilteredToolset / CombinedToolset / wrapped chain produced by
    ``assemble_routing_toolset``.
    """
    inner: Any = toolset
    for _ in range(12):
        if hasattr(inner, "tools") and isinstance(inner.tools, dict):
            return inner
        if hasattr(inner, "toolsets"):
            for sub in inner.toolsets:
                cur = sub
                for _ in range(8):
                    if hasattr(cur, "tools") and isinstance(cur.tools, dict):
                        return cur
                    cur = getattr(cur, "wrapped", None)
                    if cur is None:
                        break
            return None
        inner = getattr(inner, "wrapped", None)
        if inner is None:
            return None
    return inner


async def measure_always_schema_budget(deps: CoDeps) -> AlwaysSchemaBudget:
    """Measure the ALWAYS-visibility tool-schema prefill bucket from the assembled toolset.

    Sums ``len(name) + len(description) + len(minified-params-JSON)`` over every tool whose
    ``deps.tool_index`` visibility is ALWAYS. ``per_tool_chars`` holds the ALWAYS tools only;
    ``tool_count`` counts every tool measured (a drop guard); ``empty_descriptions`` lists any tool
    with a blank description.
    """
    inner = _unwrap_function_toolset(deps.toolset)
    if inner is None or not hasattr(inner, "tools"):
        raise RuntimeError("could not unwrap toolset to a FunctionToolset with a .tools dict")

    total_chars = 0
    per_tool_chars: dict[str, int] = {}
    empty_descriptions: list[str] = []
    tool_count = 0
    for name, tool in inner.tools.items():
        ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=name)  # type: ignore[arg-type]
        tdef = await tool.prepare_tool_def(ctx)
        if tdef is None:
            continue
        tool_count += 1
        desc = tdef.description or ""
        if not desc.strip():
            empty_descriptions.append(name)
        params_json = json.dumps(tdef.parameters_json_schema or {}, separators=(",", ":"))
        size = len(tdef.name) + len(desc) + len(params_json)
        info = deps.tool_index.get(name)
        if info is not None and info.visibility == VisibilityPolicyEnum.ALWAYS:
            per_tool_chars[name] = size
            total_chars += size

    return AlwaysSchemaBudget(
        total_chars=total_chars,
        per_tool_chars=per_tool_chars,
        tool_count=tool_count,
        empty_descriptions=empty_descriptions,
    )
