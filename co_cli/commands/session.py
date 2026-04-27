"""Session slash-command handlers: /clear, /new, /compact, /resume, /sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from co_cli.commands._types import CommandContext, ReplaceTranscript
from co_cli.display._core import console

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


async def _cmd_clear(ctx: CommandContext, args: str) -> list[Any]:
    """Clear conversation history."""
    ctx.deps.runtime.previous_compaction_summary = None
    console.print("[info]Conversation history cleared.[/info]")
    return []


async def _cmd_new(ctx: CommandContext, _args: str) -> list[Any] | None:
    """Start a fresh session."""
    from co_cli.memory.session import new_session_path

    if not ctx.message_history:
        console.print("[dim]Nothing to rotate — history is empty.[/dim]")
        return None

    # Rotate session path — transcript writer derives path from deps.session.session_path,
    # so assigning a new path ensures the next write goes to a new file.
    ctx.deps.session.session_path = new_session_path(ctx.deps.sessions_dir)
    ctx.deps.runtime.previous_compaction_summary = None
    console.print("[dim]Session rotated.[/dim]")
    return []


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
    # Manual /compact unsticks the auto-compaction gate — banner-text contract
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


async def _cmd_resume(ctx: CommandContext, args: str) -> ReplaceTranscript | None:
    """Resume a past session via interactive picker."""
    from co_cli.display._core import prompt_selection
    from co_cli.memory.session_browser import format_file_size, list_sessions
    from co_cli.memory.transcript import load_transcript

    sessions = list_sessions(ctx.deps.sessions_dir)
    if not sessions:
        console.print("[dim]No past sessions found.[/dim]")
        return None

    items: list[str] = []
    for s in sessions:
        date_str = s.last_modified.strftime("%Y-%m-%d %H:%M")
        items.append(f"{s.title} ({date_str} · {format_file_size(s.file_size)})")

    selection = prompt_selection(items, title="Resume session")
    if selection is None:
        return None

    selected_idx = items.index(selection)
    selected = sessions[selected_idx]

    messages = load_transcript(selected.path)
    if not messages:
        console.print("[dim]Could not load transcript (empty or too large).[/dim]")
        return None
    ctx.deps.session.session_path = selected.path
    return ReplaceTranscript(history=messages)


async def _cmd_sessions(ctx: CommandContext, args: str) -> None:
    """List past sessions, optionally filtered by keyword."""
    from rich.table import Table

    from co_cli.memory.session_browser import format_file_size, list_sessions

    summaries = list_sessions(ctx.deps.sessions_dir)
    if args:
        keyword = args.lower()
        summaries = [s for s in summaries if keyword in s.title.lower()]

    if not summaries:
        console.print("[dim]No sessions found.[/dim]")
        return None

    table = Table(title="Sessions", border_style="accent", expand=False)
    table.add_column("Title", style="accent")
    table.add_column("Date")
    table.add_column("Size")
    for s in summaries:
        table.add_row(
            s.title,
            s.last_modified.strftime("%Y-%m-%d %H:%M"),
            format_file_size(s.file_size),
        )
    console.print(table)
    return None
