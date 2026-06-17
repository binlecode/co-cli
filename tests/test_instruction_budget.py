"""Instruction-floor size guard — locks the full delivered instruction half.

The instruction half of the fixed prefill floor is assembled by the three static
builders the orchestrator joins into the cached prefix:

1. ``build_base_instructions(config)`` — soul seed + mindsets + numbered rules.
2. ``build_toolset_guidance(tool_catalog)`` — toolset-gated guidance
   (``CAPABILITIES_GUIDANCE``; ``MEMORY_GUIDANCE`` was deleted in floor-audit TASK-1).
3. ``load_soul_critique(personality)`` — the always-on ``## Review lens`` critique.

All three ride every cold prefill and every post-compaction state — never
compacted away — so creep in any of them is a silent, recurring context-budget
tax. This guard is the instruction-half counterpart to
``test_orchestrator_schema_budget.py`` (which guards the tool-schema half): it
pins the measured size so a re-bloated rule file, a longer critique, or grown
guidance fails CI instead of quietly growing the floor.

Single owner: this is the one instruction-budget ceiling. The
``rules-block-trim-finish`` plan trims rules and re-pins THIS ceiling
downward (its TASK-C); the ``context-stability-sizing-control`` plan reads the
same guard rather than adding a second rules-only test.

Measured 2026-06-07 after instruction-floor-audit TASK-1/TASK-2 (single-owner
dedup + deferred-tool signature decouple) and extended in TASK-4 to the full
delivered floor: base 23,129 + guidance 416 + critique 162 = 23,707 chars
(personality=tars). TASK-4 widened the *measured surface* from base-only to the
full floor, so the ceiling moved UP to absorb the two components that always rode
the floor but were previously unmeasured (this is a surface-definition change, not
a growth accommodation — the downward-only rule still holds for the same surface).
Ceiling = full-floor measurement + ~490-char headroom; the headroom absorbs the
+23-char max critique across shipped souls (finch 185 vs tars 162, pre-measured).

Re-pinned 2026-06-17 (session-recall-concept-expansion TASK-3): the cross-session
recall cascade added to ``07_memory_protocol.md`` is an intentional rule addition;
floor measured 24,694 (personality=tars), ceiling moved to 25,000 (~306 headroom).
The cascade was trimmed to a terse 3-rung skeleton first (full concept-expansion
detail lives in the ``session_search`` docstring, which is deferred and not in the
floor), so the re-pin absorbs only the irreducible rung guidance.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

import pytest

from co_cli.bootstrap.core import create_deps
from co_cli.context.assembly import build_base_instructions
from co_cli.context.guidance import build_toolset_guidance
from co_cli.personality.prompts.loader import load_soul_critique

INSTRUCTION_BLOCK_CEILING = 25_000


@pytest.mark.asyncio
async def test_instruction_floor_within_budget() -> None:
    """Base + toolset guidance + critique stays under the pinned ceiling.

    Measures the full delivered instruction floor (the three static builders the
    orchestrator joins). A rule edit, a longer critique, or grown guidance that
    pushes the floor past the ceiling fails here. When a trim intentionally lowers
    the floor, re-pin the ceiling to the new measurement (do not raise it to
    accommodate growth of the same surface).
    """
    async with AsyncExitStack() as stack:
        deps = await create_deps(on_status=lambda _s: None, stack=stack, theme_override=None)
        floor = build_base_instructions(deps.config)
        floor += build_toolset_guidance(deps.tool_catalog)
        if deps.config.personality:
            floor += load_soul_critique(deps.config.personality)

    size = len(floor)
    assert size <= INSTRUCTION_BLOCK_CEILING, (
        f"instruction floor = {size} chars exceeds ceiling "
        f"{INSTRUCTION_BLOCK_CEILING}. If this is an intentional addition, "
        f"re-pin the ceiling to the new measurement; otherwise trim the bloat."
    )
