"""Instruction-block size guard — locks the static-prefix instruction half.

``build_static_instructions(config)`` assembles soul seed + mindsets + the
numbered behavioral rules. This block rides every cold prefill and every
post-compaction state — it is never compacted away — so verbose-rule creep is a
silent, recurring context-budget tax. This guard is the instruction-half
counterpart to ``test_orchestrator_schema_budget.py`` (which guards the
tool-schema half): it pins the measured size so a re-bloated rule file or a new
rule fails CI instead of quietly growing the floor.

Single owner: this is the one instruction-budget ceiling. The
``rules-block-trim-finish`` plan trims rules and re-pins THIS ceiling
downward (its TASK-C); the ``context-stability-sizing-control`` plan reads the
same guard rather than adding a second rules-only test.

Measured 2026-06-03 after rules-block-trim-finish (rules 05+06+07 conservative
dedup; 06 manifest-scan + background-review dedup completed):
``build_static_instructions`` = 23,352 chars / ~5,838 tok (personality=tars).
Ceiling = measurement + ~398-char headroom. The context-stability plan's
illustrative ~24,256-char figure was the PRE-trim value and must never be
re-introduced — the post-trim ceiling sits below it.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

import pytest

from co_cli.bootstrap.core import create_deps
from co_cli.context.assembly import build_static_instructions

INSTRUCTION_BLOCK_CEILING = 23_750


@pytest.mark.asyncio
async def test_static_instructions_within_budget() -> None:
    """Seed + mindsets + rules stays under the pinned ceiling.

    A rule edit that bloats the always-on instruction floor past the ceiling
    fails here. When a trim intentionally lowers the block, re-pin the ceiling
    to the new measurement (do not raise it to accommodate growth).
    """
    async with AsyncExitStack() as stack:
        deps = await create_deps(on_status=lambda _s: None, stack=stack, theme_override=None)
        instructions = build_static_instructions(deps.config)

    size = len(instructions)
    assert size <= INSTRUCTION_BLOCK_CEILING, (
        f"static instructions = {size} chars exceeds ceiling "
        f"{INSTRUCTION_BLOCK_CEILING}. If this is an intentional addition, "
        f"re-pin the ceiling to the new measurement; otherwise trim the bloat."
    )
