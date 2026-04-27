"""Slash command handler for /compact."""

from __future__ import annotations

from typing import TYPE_CHECKING

from co_cli.commands._types import CommandContext, ReplaceTranscript
from co_cli.display._core import console

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


async def _cmd_compact(ctx: CommandContext, args: str) -> ReplaceTranscript | None:
    """Summarize conversation via LLM to reduce context.

    Shares the automatic compaction degradation path: when the summarizer
    provider fails, the model is absent, or the circuit breaker is tripped,
    falls back to a static marker rather than leaving history unchanged.
    """
    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.messages import TextPart as _TextPart
    from pydantic_ai.usage import RunUsage

    from co_cli.context.compaction import apply_compaction
    from co_cli.context.summarization import (
        estimate_message_tokens,
        resolve_compaction_budget,
    )

    if not ctx.message_history:
        console.print("[dim]Nothing to compact — history is empty.[/dim]")
        return None

    pre_tokens = estimate_message_tokens(ctx.message_history)
    old_len = len(ctx.message_history)

    raw_model = ctx.deps.model.model if ctx.deps.model else None
    run_ctx: RunContext[CoDeps] = RunContext(deps=ctx.deps, model=raw_model, usage=RunUsage())
    new_history, summary = await apply_compaction(
        run_ctx,
        ctx.message_history,
        (0, old_len, old_len),
        announce=True,
        focus=args.strip() or None,
    )
    new_history.append(
        ModelResponse(
            parts=[_TextPart(content="Understood. I have the conversation context.")],
        )
    )
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0
    ctx.deps.runtime.compaction_thrash_hint_emitted = False

    post_tokens = estimate_message_tokens(new_history)
    budget = resolve_compaction_budget(
        ctx.deps.config, ctx.deps.model.context_window if ctx.deps.model else None
    )
    fallback_note = "" if summary is not None else " (static marker — summary unavailable)"
    console.print(
        f"[info]Compacted: {old_len} → {len(new_history)} messages "
        f"(est. {pre_tokens // 1000}K → {post_tokens // 1000}K of {budget // 1000}K budget)"
        f"{fallback_note}[/info]"
    )
    return ReplaceTranscript(history=new_history, compaction_applied=True)
