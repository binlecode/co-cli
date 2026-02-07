"""Slash command registry, handlers, and dispatch for the REPL."""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelRequest

from co_cli.display import console


# -- Types -----------------------------------------------------------------

@dataclass(frozen=True)
class CommandContext:
    """Immutable grab-bag passed to every slash-command handler."""

    message_history: list[Any]
    deps: Any  # CoDeps — typed as Any to avoid circular import
    agent: Any  # Agent[CoDeps, ...] — same reason
    tool_names: list[str]


@dataclass(frozen=True)
class SlashCommand:
    """A registered slash command."""

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[list[Any] | None]]


# -- Handlers --------------------------------------------------------------


async def _cmd_help(ctx: CommandContext, args: str) -> None:
    """List available slash commands."""
    from rich.table import Table

    table = Table(title="Slash Commands", border_style="accent", expand=False)
    table.add_column("Command", style="accent")
    table.add_column("Description")
    for cmd in COMMANDS.values():
        table.add_row(f"/{cmd.name}", cmd.description)
    console.print(table)
    return None


async def _cmd_clear(ctx: CommandContext, args: str) -> list[Any]:
    """Clear conversation history."""
    console.print("[info]Conversation history cleared.[/info]")
    return []


async def _cmd_status(ctx: CommandContext, args: str) -> None:
    """Show system health (same as `co status`)."""
    from co_cli.status import get_status, render_status_table

    info = get_status(tool_count=len(ctx.tool_names))
    console.print(render_status_table(info))
    return None


async def _cmd_tools(ctx: CommandContext, args: str) -> None:
    """List registered agent tools."""
    tools = sorted(ctx.tool_names)
    lines = [f"  [accent]{i + 1}.[/accent] {name}" for i, name in enumerate(tools)]
    console.print(f"[info]Registered tools ({len(tools)}):[/info]")
    console.print("\n".join(lines))
    return None


async def _cmd_history(ctx: CommandContext, args: str) -> None:
    """Show conversation turn count."""
    turns = sum(
        1 for msg in ctx.message_history
        if isinstance(msg, ModelRequest)
    )
    console.print(f"[info]Conversation: {turns} user turn(s), {len(ctx.message_history)} total message(s).[/info]")
    return None


async def _cmd_compact(ctx: CommandContext, args: str) -> list[Any] | None:
    """Summarize conversation via LLM to reduce context."""
    from pydantic_ai.messages import ModelResponse, TextPart as _TextPart, UserPromptPart

    from co_cli._history import summarize_messages

    if not ctx.message_history:
        console.print("[dim]Nothing to compact — history is empty.[/dim]")
        return None

    console.print("[dim]Compacting conversation...[/dim]")
    try:
        # Use the agent's model for /compact (user-initiated, quality matters)
        model = ctx.agent.model
        summary = await summarize_messages(ctx.message_history, model)

        # Build a minimal 2-message history: summary request + ack response
        new_history: list[Any] = [
            ModelRequest(parts=[
                UserPromptPart(content=f"[Compacted conversation summary]\n{summary}"),
            ]),
            ModelResponse(parts=[
                _TextPart(content="Understood. I have the conversation context."),
            ]),
        ]
        old_len = len(ctx.message_history)
        console.print(
            f"[info]Compacted: {old_len} messages → {len(new_history)} messages.[/info]"
        )
        return new_history
    except Exception as e:
        console.print(f"[bold red]Compact failed:[/bold red] {e}")
        return None


async def _cmd_yolo(ctx: CommandContext, args: str) -> None:
    """Toggle auto-approve mode."""
    ctx.deps.auto_confirm = not ctx.deps.auto_confirm
    if ctx.deps.auto_confirm:
        console.print("[yolo]YOLO mode enabled — auto-approving all tool calls.[/yolo]")
    else:
        console.print("[info]YOLO mode disabled — tool calls require approval.[/info]")
    return None


# -- Registry --------------------------------------------------------------

COMMANDS: dict[str, SlashCommand] = {
    "help": SlashCommand("help", "List available slash commands", _cmd_help),
    "clear": SlashCommand("clear", "Clear conversation history", _cmd_clear),
    "status": SlashCommand("status", "Show system health", _cmd_status),
    "tools": SlashCommand("tools", "List registered agent tools", _cmd_tools),
    "history": SlashCommand("history", "Show conversation turn count", _cmd_history),
    "compact": SlashCommand("compact", "Summarize conversation via LLM to reduce context", _cmd_compact),
    "yolo": SlashCommand("yolo", "Toggle auto-approve mode", _cmd_yolo),
}


# -- Dispatch --------------------------------------------------------------


async def dispatch(raw_input: str, ctx: CommandContext) -> tuple[bool, list[Any] | None]:
    """Route slash-command input to the appropriate handler.

    Returns (handled, new_history):
      - handled=False  → input was not a slash command, caller should proceed normally
      - handled=True, new_history=None → command executed, history unchanged
      - handled=True, new_history=list → command executed, caller must rebind history
    """
    if not raw_input.startswith("/"):
        return False, None

    parts = raw_input[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    cmd = COMMANDS.get(name)
    if cmd is None:
        console.print(f"[bold red]Unknown command:[/bold red] /{name}")
        console.print("[dim]Type /help to see available commands.[/dim]")
        return True, None

    result = await cmd.handler(ctx, args)
    return True, result
