"""Slash command handler for /history."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ToolReturnPart
from rich.table import Table

from co_cli.commands.types import CommandContext
from co_cli.display._core import console

_DELEGATION_TOOLS = frozenset(
    {
        "web_research",
        "knowledge_analyze",
        "reason",
        "task_start",
    }
)


async def _cmd_history(ctx: CommandContext, args: str) -> None:
    """Show conversation delegation history (run_id, role, requests, scope)."""
    rows = []
    for msg in ctx.message_history:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name not in _DELEGATION_TOOLS:
                continue
            content = part.content
            if not isinstance(content, dict):
                continue
            run_id = content.get("run_id") or content.get("task_id") or ""
            rows.append(
                {
                    "tool": part.tool_name,
                    "run_id": str(run_id)[:20],
                    "role": str(content.get("role", "")),
                    "requests": f"{content.get('requests_used', '')} / {content.get('request_limit', '')}",
                    "scope": str(content.get("scope", ""))[:50],
                }
            )

    if not rows:
        console.print("[dim]No delegations this session.[/dim]")
        return None

    table = Table(title="Delegation History", border_style="accent", expand=False)
    table.add_column("Tool", style="accent")
    table.add_column("Run ID")
    table.add_column("Role")
    table.add_column("Requests")
    table.add_column("Scope")
    for r in rows:
        table.add_row(r["tool"], r["run_id"], r["role"], r["requests"], r["scope"])
    console.print(table)
    return None
