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

from co_cli.agent.core import build_native_toolset
from co_cli.bootstrap.core import create_deps
from co_cli.bootstrap.schema_budget import measure_always_schema_budget
from co_cli.config.tuning import ESTIMATE_CHARS_PER_TOKEN
from co_cli.context.assembly import build_base_instructions
from co_cli.context.guidance import build_toolset_guidance
from co_cli.context.tokens import estimate_text_tokens
from co_cli.personality.prompts.loader import load_soul_critique

# Measured 2026-06-07 after defer-recall-and-skill-edit-tools (TASK A2): session_search,
# session_view, skill_edit, skill_patch flipped ALWAYS → DEFERRED, dropping the bucket
# 20,581 → 17,224. Re-pinned with ~400-char headroom. (Prior: 20,581 after tool-view-load-by-name;
# 19,800 after defer-skill-write-tools; 20,988 pre-defer; 22,589 pre-trim.)
# 2026-06-13: interactive-terminal plan added shell_exec's `pty` param → bucket 17,224 → 17,674.
# Ceiling held at 17,700 (still passes, headroom now ~26) — the principled cap is deliberately NOT
# loosened for a single param; the next ALWAYS growth must defer something or re-pin consciously.
# 2026-06-15: shell-exec-auto-yield added a one-sentence return-contract note (a long command may
# auto-promote to a background task and return a task handle) → bucket 17,674 → 17,808. Re-pinned
# consciously: the model MUST know a shell_exec result can be a task handle, a permanent
# non-deferrable cost on an ALWAYS tool (same class as the `pty` param); note kept to one sentence.
# 2026-06-17: fts-hybrid-recall-hardening (TASK-5, Gate-1 Option B) flipped session_search
# DEFERRED → ALWAYS so past-conversation questions reach session recall instead of misrouting to
# memory_search → bucket 17,808 → 20,016. Re-pinned consciously: the one-schema prefill cost is a
# deliberate, reviewed trade against the misroute-to-wrong-tier failure for a common query class
# (its concept-expansion docstring is what elicits the cascade; trimming it would defeat the fix).
ALWAYS_BUCKET_CEILING = 20_100
# Per-tool tripwire against UNINTENDED docstring bloat — pinned just above the
# largest ALWAYS tool, not a first-principle budget (the principled cap is the
# cumulative ALWAYS_BUCKET_CEILING: every ALWAYS schema ships in every turn's
# uncompactable prefix). Re-pin on an INTENTIONAL surface change, per this file's
# header. Measured max ALWAYS tool: shell_exec = 2,420 chars after the interactive-
# terminal plan added the `pty` param + a task_write-loop pointer (was 1,966 — the
# `pty` param is a permanent, non-deferrable cost of a real capability on an ALWAYS
# tool; its pre-existing routing/diagnosis guidance was kept intact). +headroom.
# 2026-06-15: auto-yield return-contract note → shell_exec 2,420 → 2,554. Re-pinned to 2,600
# (still the largest ALWAYS tool; the note is the minimal sentence for a changed return contract).
PER_ALWAYS_TOOL_CEILING = 2_600
# Registry is 35 (native; 5 of them DEFERRED Google tools). Floor is a drop guard,
# deliberately well below current — not a pin of the exact count.
MIN_TOOL_COUNT = 27


@pytest.mark.asyncio
async def test_always_bucket_within_budget() -> None:
    """ALWAYS-visibility tool schemas (name+desc+params) stay under the pinned ceiling."""
    # stack=None: headless deps, no MCP connection. The guard measures only the
    # native FunctionToolset, so skipping MCP keeps the count deterministic across
    # environments and avoids the Context7 stdio teardown race.
    deps = await create_deps(on_status=lambda _s: None, stack=None)

    native_toolset, _ = build_native_toolset()
    budget = await measure_always_schema_budget(deps, native_toolset)

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
    """deps.static_floor_tokens is the measured full instruction floor + ALWAYS-schema.

    The instruction half is the full delivered floor — base instructions + toolset
    guidance + personality critique (the three static builders the orchestrator joins)
    — per instruction-floor-audit TASK-4, not base instructions alone.
    """
    deps = await create_deps(on_status=lambda _s: None, stack=None)

    native_toolset, _ = build_native_toolset()
    budget = await measure_always_schema_budget(deps, native_toolset)
    instruction_tokens = estimate_text_tokens(build_base_instructions(deps.config))
    instruction_tokens += estimate_text_tokens(build_toolset_guidance(deps.tool_catalog))
    if deps.config.personality:
        instruction_tokens += estimate_text_tokens(load_soul_critique(deps.config.personality))
    expected = instruction_tokens + budget.total_chars // ESTIMATE_CHARS_PER_TOKEN

    assert deps.static_floor_tokens > 0
    assert deps.static_floor_tokens == expected
