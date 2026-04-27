"""Slash command handler for /resume."""

from __future__ import annotations

from co_cli.commands._types import CommandContext, ReplaceTranscript
from co_cli.display._core import console


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
