"""Cumulative schema-budget guard — locks the ALWAYS tool-schema prefill size.

Every ALWAYS-visibility tool's ToolDefinition (name + description +
minified-parameters-JSON) ships in every turn's static prefix, so docstring
bloat is a silent, recurring context-budget tax. This guard pins the measured
post-trim ALWAYS total and per-tool max so a re-bloated docstring or a new
ALWAYS tool fails CI instead of quietly growing the prefill.

Measurement mirrors ``tmp/audit_tool_schemas.py``: build deps via
``create_deps``, unwrap the toolset to the inner ``FunctionToolset.tools``
dict, call each tool's ``prepare_tool_def(ctx)``, and cross-reference
visibility via ``deps.tool_index[name].visibility``.

The pinned ceilings below were re-measured after the prefill-trim-2
tool-guidance-dedup landing (TASK-1 through 3). Re-run ``tmp/audit_tool_schemas.py``
and update them whenever an ALWAYS tool's surface intentionally changes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai._run_context import RunContext
from pydantic_ai.result import RunUsage

from co_cli.bootstrap.core import create_deps
from co_cli.deps import VisibilityPolicyEnum

# Measured 2026-06-02 after defer-skill-write-tools (skill_create + skill_delete → DEFERRED):
# ALWAYS bucket = 19,800 chars (was 20,988 pre-defer; 22,589 pre-trim). +~400-char headroom.
ALWAYS_BUCKET_CEILING = 20_200
# Measured max ALWAYS tool: file_search = 2,111 chars (child 3's, already trimmed),
# shell_exec = 1,966 (untouched canonical routing home). +headroom.
PER_ALWAYS_TOOL_CEILING = 2_300
# Registry is 35 (native; 5 of them DEFERRED Google tools). Floor is a drop guard,
# deliberately well below current — not a pin of the exact count.
MIN_TOOL_COUNT = 27


def _unwrap_function_toolset(toolset: Any) -> Any:
    """Walk toolset wrappers to the inner FunctionToolset holding a .tools dict.

    Mirrors the proven unwrap in tmp/audit_tool_schemas.py: handles the
    FilteredToolset / CombinedToolset / wrapped chain.
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


@pytest.mark.asyncio
async def test_always_bucket_within_budget() -> None:
    """ALWAYS-visibility tool schemas (name+desc+params) stay under the pinned ceiling."""
    # stack=None: headless deps, no MCP connection. The guard measures only the
    # native FunctionToolset, so skipping MCP keeps the count deterministic across
    # environments and avoids the Context7 stdio teardown race.
    deps = await create_deps(on_status=lambda _s: None, stack=None, theme_override=None)

    inner = _unwrap_function_toolset(deps.toolset)
    assert inner is not None, "could not unwrap toolset to a FunctionToolset"
    assert hasattr(inner, "tools"), "unwrapped toolset has no .tools dict"

    always_total = 0
    per_tool_totals: dict[str, int] = {}
    empty_desc: list[str] = []
    tool_count = 0
    for name, tool in inner.tools.items():
        ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=name)  # type: ignore[arg-type]
        tdef = await tool.prepare_tool_def(ctx)
        if tdef is None:
            continue
        tool_count += 1
        desc = tdef.description or ""
        if not desc.strip():
            empty_desc.append(name)
        params_json = json.dumps(tdef.parameters_json_schema or {}, separators=(",", ":"))
        total = len(tdef.name) + len(desc) + len(params_json)
        per_tool_totals[name] = total
        info = deps.tool_index.get(name)
        if info is not None and info.visibility == VisibilityPolicyEnum.ALWAYS:
            always_total += total

    assert not empty_desc, f"tools with empty description: {empty_desc}"

    assert tool_count >= MIN_TOOL_COUNT, (
        f"registry shrank to {tool_count} tools (floor {MIN_TOOL_COUNT}) — "
        "a tool may have been dropped accidentally"
    )

    always_tools = {
        name: total
        for name, total in per_tool_totals.items()
        if (info := deps.tool_index.get(name)) is not None
        and info.visibility == VisibilityPolicyEnum.ALWAYS
    }
    max_name = max(always_tools, key=always_tools.__getitem__)
    assert always_tools[max_name] <= PER_ALWAYS_TOOL_CEILING, (
        f"ALWAYS tool '{max_name}' grew to {always_tools[max_name]} chars "
        f"(ceiling {PER_ALWAYS_TOOL_CEILING}) — trim its docstring"
    )

    assert always_total <= ALWAYS_BUCKET_CEILING, (
        f"ALWAYS tool-schema bucket grew to {always_total} chars "
        f"(ceiling {ALWAYS_BUCKET_CEILING}) — a docstring re-bloated or a new ALWAYS tool landed"
    )
