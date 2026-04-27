"""Slash command handler for /reasoning."""

from __future__ import annotations

from co_cli.commands._types import CommandContext
from co_cli.config._core import VALID_REASONING_DISPLAY_MODES
from co_cli.display._core import console

_REASONING_CYCLE = ["off", "summary", "full"]


async def _cmd_reasoning(ctx: CommandContext, args: str) -> None:
    """Show or set the reasoning display mode for this session."""
    token = args.strip().lower()
    if not token:
        console.print(
            f"Reasoning display: [highlight]{ctx.deps.session.reasoning_display}[/highlight]"
        )
        return None
    if token in ("next", "cycle"):
        current = ctx.deps.session.reasoning_display
        idx = _REASONING_CYCLE.index(current) if current in _REASONING_CYCLE else 0
        ctx.deps.session.reasoning_display = _REASONING_CYCLE[(idx + 1) % len(_REASONING_CYCLE)]
    elif token in VALID_REASONING_DISPLAY_MODES:
        ctx.deps.session.reasoning_display = token
    else:
        console.print(
            f"[error]Unknown reasoning mode: {token!r}. Valid: off, summary, full, next[/error]"
        )
        return None
    console.print(
        f"Reasoning display: [highlight]{ctx.deps.session.reasoning_display}[/highlight]"
    )
    return None
