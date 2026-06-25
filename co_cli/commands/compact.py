"""Slash command handler for /compact."""

from __future__ import annotations

from co_cli.commands.types import CommandContext, ReplaceTranscript
from co_cli.display.core import console


async def _cmd_compact(ctx: CommandContext, args: str) -> ReplaceTranscript | None:
    """Summarize conversation via LLM to reduce context.

    Shares the automatic compaction degradation path: when the summarizer
    provider fails, the model is absent, or the circuit breaker is tripped,
    falls back to a static marker rather than leaving history unchanged.
    """
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.messages import TextPart as _TextPart

    from co_cli.context.compaction import (
        commit_compaction,
        compact_messages,
        estimate_message_tokens,
        resolve_compaction_budget,
    )

    if not ctx.message_history:
        console.print("[dim]Nothing to compact — history is empty.[/dim]")
        return None

    pre_tokens = estimate_message_tokens(ctx.message_history)
    old_len = len(ctx.message_history)

    console.print("[dim]Compacting conversation...[/dim]")
    new_history, summary, _ = await compact_messages(
        ctx.deps,
        ctx.message_history,
        (0, old_len, old_len),
        focus=args.strip() or None,
    )
    commit_compaction(ctx.deps, new_history)
    new_history.append(
        ModelResponse(
            parts=[_TextPart(content="Understood. I have the conversation context.")],
        )
    )
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0

    post_tokens = estimate_message_tokens(new_history)
    budget = resolve_compaction_budget(ctx.deps)
    fallback_note = "" if summary is not None else " (static marker — summary unavailable)"
    console.print(
        f"[info]Compacted: {old_len} → {len(new_history)} messages "
        f"(est. {pre_tokens // 1000}K → {post_tokens // 1000}K of {budget // 1000}K budget)"
        f"{fallback_note}[/info]"
    )
    return ReplaceTranscript(history=new_history, compaction_applied=True)
