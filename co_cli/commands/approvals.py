"""Slash command handler for /approvals."""

from __future__ import annotations

from rich.table import Table

from co_cli.commands.types import CommandContext
from co_cli.deps import ApprovalKindEnum
from co_cli.display.core import console


def _rule_label(kind: ApprovalKindEnum, value: str) -> tuple[str, str]:
    """Return (human-readable scope label, human-readable value hint)."""
    if kind == ApprovalKindEnum.SHELL:
        return "shell utility", value
    if kind == ApprovalKindEnum.PATH:
        return "writable dir", f"{value}/**"
    if kind == ApprovalKindEnum.DOMAIN:
        return "web domain", value
    # kind == ApprovalKindEnum.TOOL
    return "tool", value


async def _cmd_approvals(ctx: CommandContext, args: str) -> None:
    """Manage session approval rules."""
    sub = args.strip().split(maxsplit=1)
    subcmd = sub[0].lower() if sub else "list"
    subargs = sub[1].strip() if len(sub) > 1 else ""

    rules = ctx.deps.session.session_approval_rules

    if subcmd == "list":
        if not rules:
            console.print("[dim]No session approval rules this session.[/dim]")
            return None
        table = Table(title="Session Approval Rules", border_style="accent")
        table.add_column("#", style="dim")
        table.add_column("Scope")
        table.add_column("Approved For")
        for i, rule in enumerate(rules):
            label, hint = _rule_label(rule.kind, rule.value)
            table.add_row(str(i), label, hint)
        console.print(table)

    elif subcmd == "clear":
        if not rules:
            console.print("[dim]No approval rules to clear.[/dim]")
            return None
        if subargs:
            try:
                idx = int(subargs)
                rules.pop(idx)
                console.print(f"[success]✓ Removed approval rule {idx}[/success]")
            except (ValueError, IndexError):
                console.print(f"[bold red]No rule at index:[/bold red] {subargs}")
        else:
            count = len(rules)
            rules.clear()
            console.print(f"[success]✓ Cleared {count} approval rule(s)[/success]")

    else:
        console.print(f"[bold red]Unknown /approvals subcommand:[/bold red] {subcmd}")
        console.print("[dim]Usage: /approvals [list|clear [index]][/dim]")

    return None
