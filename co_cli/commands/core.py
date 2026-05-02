"""Slash command registry, handlers, and dispatch for the REPL."""

from __future__ import annotations

import logging

from co_cli.commands.approvals import _cmd_approvals
from co_cli.commands.background import _cmd_background
from co_cli.commands.cancel import _cmd_cancel
from co_cli.commands.clear import _cmd_clear
from co_cli.commands.compact import _cmd_compact
from co_cli.commands.help import _cmd_help
from co_cli.commands.history import _cmd_history
from co_cli.commands.knowledge import _cmd_memory
from co_cli.commands.new import _cmd_new
from co_cli.commands.reasoning import _cmd_reasoning
from co_cli.commands.registry import (
    BUILTIN_COMMANDS,
    SlashCommand,
)
from co_cli.commands.resume import _cmd_resume
from co_cli.commands.sessions import _cmd_sessions
from co_cli.commands.skills import _cmd_skills
from co_cli.commands.tasks import _cmd_tasks
from co_cli.commands.tools import _cmd_tools
from co_cli.commands.types import (
    CommandContext,
    DelegateToAgent,
    LocalOnly,
    ReplaceTranscript,
    SlashOutcome,
)
from co_cli.display.core import console

logger = logging.getLogger(__name__)


# -- Registry --------------------------------------------------------------

BUILTIN_COMMANDS["help"] = SlashCommand("help", "List available slash commands", _cmd_help)
BUILTIN_COMMANDS["clear"] = SlashCommand("clear", "Clear conversation history", _cmd_clear)
BUILTIN_COMMANDS["new"] = SlashCommand("new", "Start a fresh session", _cmd_new)
BUILTIN_COMMANDS["tools"] = SlashCommand("tools", "List registered agent tools", _cmd_tools)
BUILTIN_COMMANDS["history"] = SlashCommand(
    "history", "Show delegation history (delegation agents + background tasks)", _cmd_history
)
BUILTIN_COMMANDS["compact"] = SlashCommand(
    "compact", "Summarize conversation via LLM to reduce context", _cmd_compact
)
BUILTIN_COMMANDS["memory"] = SlashCommand(
    "memory",
    "Manage memory artifacts — /memory list|count|forget|dream|restore|decay-review|stats [args]",
    _cmd_memory,
)
BUILTIN_COMMANDS["approvals"] = SlashCommand(
    "approvals", "Manage session approval rules", _cmd_approvals
)
BUILTIN_COMMANDS["skills"] = SlashCommand("skills", "List and inspect loaded skills", _cmd_skills)
BUILTIN_COMMANDS["background"] = SlashCommand(
    "background", "Run a command in the background", _cmd_background
)
BUILTIN_COMMANDS["tasks"] = SlashCommand(
    "tasks",
    "List background tasks or show task detail: /tasks [status-filter | task-id]",
    _cmd_tasks,
)
BUILTIN_COMMANDS["cancel"] = SlashCommand(
    "cancel", "Cancel a running background task", _cmd_cancel
)
BUILTIN_COMMANDS["resume"] = SlashCommand("resume", "Resume a past session", _cmd_resume)
BUILTIN_COMMANDS["sessions"] = SlashCommand("sessions", "List past sessions", _cmd_sessions)
BUILTIN_COMMANDS["reasoning"] = SlashCommand(
    "reasoning",
    "Show or set reasoning display: /reasoning [off|summary|full|next]",
    _cmd_reasoning,
)


# -- Dispatch --------------------------------------------------------------


async def dispatch(raw_input: str, ctx: CommandContext) -> SlashOutcome:
    """Route slash-command input to the appropriate handler.

    Returns a SlashOutcome encoding the command intent:
      - LocalOnly → command ran locally; caller returns to prompt
      - ReplaceTranscript → command replaced history; caller adopts new history and returns to prompt
      - DelegateToAgent → skill command; caller enters run_turn() with delegated_input
    """
    if not raw_input.startswith("/"):
        return LocalOnly()

    parts = raw_input[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    cmd = BUILTIN_COMMANDS.get(name)
    if cmd is not None:
        result = await cmd.handler(ctx, args)
        if isinstance(result, ReplaceTranscript):
            return result
        if result is not None:
            return ReplaceTranscript(history=result)
        return LocalOnly()

    # Check skill registry after built-in commands (skills cannot shadow builtins)
    skill = ctx.deps.skill_commands.get(name)
    if skill is not None:
        body = skill.body
        if args and "$ARGUMENTS" in body:
            args_list = args.split()
            body = body.replace("$ARGUMENTS", args)
            body = body.replace("$0", name)
            for i, arg in reversed(list(enumerate(args_list, 1))):
                body = body.replace(f"${i}", arg)
        return DelegateToAgent(
            delegated_input=body,
            skill_env=dict(skill.skill_env),
            skill_name=skill.name,
        )

    console.print(f"[bold red]Unknown command:[/bold red] /{name}")
    console.print("[dim]Type /help to see available commands.[/dim]")
    return LocalOnly()
