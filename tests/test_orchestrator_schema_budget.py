"""Cumulative schema-budget guard — locks the ALWAYS tool-schema prefill size.

Every ALWAYS-visibility tool's ToolDefinition (name + description +
minified-parameters-JSON) ships in every turn's static prefix, so docstring
bloat is a silent, recurring context-budget tax. This guard pins the measured
post-trim ALWAYS total and per-tool max so a re-bloated docstring or a new
ALWAYS tool fails CI instead of quietly growing the prefill.

Measurement is factored into ``co_cli.bootstrap.schema_budget.measure_always_schema_budget`` — the
single source of truth shared with the runtime floor measurement (``create_deps`` folds the same
bucket into ``deps.static_floor_tokens``). This guard pins the measured chars; the runtime converts
them to tokens.

The pinned ceilings below were re-measured after the prefill-trim-2
tool-guidance-dedup landing (TASK-1 through 3). Re-run ``tmp/audit_tool_schemas.py``
and update them whenever an ALWAYS tool's surface intentionally changes.
"""

from __future__ import annotations

import pytest

from co_cli.bootstrap.core import create_deps
from co_cli.bootstrap.schema_budget import measure_always_schema_budget
from co_cli.context.assembly import build_static_instructions
from co_cli.context.tokens import CHARS_PER_TOKEN, estimate_text_tokens

# Measured 2026-06-07 after defer-recall-and-skill-edit-tools (TASK A2): session_search,
# session_view, skill_edit, skill_patch flipped ALWAYS → DEFERRED, dropping the bucket
# 20,581 → 17,224. Re-pinned with ~400-char headroom. (Prior: 20,581 after tool-view-load-by-name;
# 19,800 after defer-skill-write-tools; 20,988 pre-defer; 22,589 pre-trim.)
ALWAYS_BUCKET_CEILING = 17_700
# Measured max ALWAYS tool: file_search = 2,111 chars (child 3's, already trimmed),
# shell_exec = 1,966 (untouched canonical routing home). +headroom.
PER_ALWAYS_TOOL_CEILING = 2_300
# Registry is 35 (native; 5 of them DEFERRED Google tools). Floor is a drop guard,
# deliberately well below current — not a pin of the exact count.
MIN_TOOL_COUNT = 27


@pytest.mark.asyncio
async def test_always_bucket_within_budget() -> None:
    """ALWAYS-visibility tool schemas (name+desc+params) stay under the pinned ceiling."""
    # stack=None: headless deps, no MCP connection. The guard measures only the
    # native FunctionToolset, so skipping MCP keeps the count deterministic across
    # environments and avoids the Context7 stdio teardown race.
    deps = await create_deps(on_status=lambda _s: None, stack=None, theme_override=None)

    budget = await measure_always_schema_budget(deps)

    assert not budget.empty_descriptions, (
        f"tools with empty description: {budget.empty_descriptions}"
    )

    assert budget.tool_count >= MIN_TOOL_COUNT, (
        f"registry shrank to {budget.tool_count} tools (floor {MIN_TOOL_COUNT}) — "
        "a tool may have been dropped accidentally"
    )

    max_name = max(budget.per_tool_chars, key=budget.per_tool_chars.__getitem__)
    assert budget.per_tool_chars[max_name] <= PER_ALWAYS_TOOL_CEILING, (
        f"ALWAYS tool '{max_name}' grew to {budget.per_tool_chars[max_name]} chars "
        f"(ceiling {PER_ALWAYS_TOOL_CEILING}) — trim its docstring"
    )

    assert budget.total_chars <= ALWAYS_BUCKET_CEILING, (
        f"ALWAYS tool-schema bucket grew to {budget.total_chars} chars "
        f"(ceiling {ALWAYS_BUCKET_CEILING}) — a docstring re-bloated or a new ALWAYS tool landed"
    )


@pytest.mark.asyncio
async def test_static_floor_tokens_measured_at_bootstrap() -> None:
    """deps.static_floor_tokens is the measured (not literal) instruction + ALWAYS-schema floor."""
    deps = await create_deps(on_status=lambda _s: None, stack=None, theme_override=None)

    budget = await measure_always_schema_budget(deps)
    expected = (
        estimate_text_tokens(build_static_instructions(deps.config))
        + budget.total_chars // CHARS_PER_TOKEN
    )

    assert deps.static_floor_tokens > 0
    assert deps.static_floor_tokens == expected
