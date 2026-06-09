"""ALWAYS-visibility tool-schema budget measurement — single source of truth.

Every ALWAYS-visibility tool's ToolDefinition (name + description + minified-parameters-JSON)
ships in every turn's static prefix, so it is part of the floor-inclusive prefill the provider
counts on every request. This module measures that bucket once, from the native toolset, and is
consumed by two callers that must agree on the number:

  - ``co_cli/bootstrap/core.py`` — folds the measured chars into ``deps.static_floor_tokens`` so the
    compaction triggers can account for the floor when the provider report is stale.
  - ``tests/test_orchestrator_schema_budget.py`` — the regression guard that pins the bucket size.

Measurement: iterate the native ``FunctionToolset.tools`` dict (passed in by the caller, which
already holds it from ``build_native_toolset``), call each tool's ``prepare_tool_def(ctx)``
(respects per-turn prepare callbacks), and cross-reference visibility via
``deps.tool_catalog[name].visibility``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.deps import CoDeps, VisibilityPolicyEnum

if TYPE_CHECKING:
    from pydantic_ai.toolsets import FunctionToolset


@dataclass(frozen=True)
class AlwaysSchemaBudget:
    """Measured ALWAYS-visibility tool-schema sizes (in chars)."""

    total_chars: int
    per_tool_chars: dict[str, int]
    tool_count: int
    empty_descriptions: list[str]


async def measure_always_schema_budget(
    deps: CoDeps, native_toolset: FunctionToolset[CoDeps]
) -> AlwaysSchemaBudget:
    """Measure the ALWAYS-visibility tool-schema prefill bucket from the native toolset.

    ``native_toolset`` is the inner ``FunctionToolset`` produced by ``build_native_toolset`` —
    the caller (``bootstrap/core.py``) already holds it, so the measurer reads its ``.tools`` dict
    directly instead of duck-typing the SDK's assembled toolset-composition topology.

    Sums ``len(name) + len(description) + len(minified-params-JSON)`` over every tool whose
    ``deps.tool_catalog`` visibility is ALWAYS. ``per_tool_chars`` holds the ALWAYS tools only;
    ``tool_count`` counts every tool measured (a drop guard); ``empty_descriptions`` lists any tool
    with a blank description.
    """
    total_chars = 0
    per_tool_chars: dict[str, int] = {}
    empty_descriptions: list[str] = []
    tool_count = 0
    for name, tool in native_toolset.tools.items():
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
        info = deps.tool_catalog.get(name)
        if info is not None and info.visibility == VisibilityPolicyEnum.ALWAYS:
            per_tool_chars[name] = size
            total_chars += size

    return AlwaysSchemaBudget(
        total_chars=total_chars,
        per_tool_chars=per_tool_chars,
        tool_count=tool_count,
        empty_descriptions=empty_descriptions,
    )
