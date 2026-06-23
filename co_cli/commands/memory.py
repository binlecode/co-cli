"""Slash command handler for /memory."""

from __future__ import annotations

from typing import Any

from co_cli.commands._utils import _confirm
from co_cli.commands.types import CommandContext
from co_cli.display.core import console, glyphs
from co_cli.memory.item import filter_memory_items, format_memory_item_row, load_memory_items
from co_cli.tools.memory.recall import grep_recall

_MEMORY_USAGE = (
    "[bold]Usage:[/bold] /memory list|count|forget|restore|stats "
    "[query] [--older-than N] "
    "[--kind preference|feedback|rule|decision|article|reference|note] [--dry]"
)


def _parse_memory_args(args: str) -> tuple[str | None, dict[str, Any]]:
    """Parse /memory subcommand args into (query, filters).

    Flags: ``--older-than N`` (int days), ``--kind X`` (memory_kind).
    Remaining non-flag tokens are joined as the query string.
    Returns (None, filters) when no query tokens are present.
    """
    tokens = args.split()
    filters: dict[str, Any] = {}
    query_tokens: list[str] = []
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "--older-than" and idx + 1 < len(tokens):
            try:
                filters["older_than_days"] = int(tokens[idx + 1])
                idx += 2
                continue
            except ValueError:
                pass
        elif tok == "--kind" and idx + 1 < len(tokens):
            filters["kind"] = tokens[idx + 1]
            idx += 2
            continue
        query_tokens.append(tok)
        idx += 1
    query = " ".join(query_tokens) if query_tokens else None
    return query, filters


async def _subcmd_memory_list(
    ctx: CommandContext, query: str | None, filters: dict[str, Any]
) -> None:
    """Display matching knowledge artifacts — one line each, with a count footer."""
    kind_filter = filters.get("kind")
    entries = load_memory_items(
        ctx.deps.memory_dir,
        memory_kinds=[kind_filter] if kind_filter is not None else None,
    )
    entries = filter_memory_items(entries, filters)
    if query is not None:
        entries = grep_recall(entries, query, max_results=len(entries) or 1)
    if not entries:
        console.print("[dim]No memories found.[/dim]")
    else:
        for m in entries:
            console.print(format_memory_item_row(m))
    console.print(f"[dim]{len(entries)} memories[/dim]")


async def _subcmd_memory_count(
    ctx: CommandContext, query: str | None, filters: dict[str, Any]
) -> None:
    """Print the count of matching artifacts."""
    kind_filter = filters.get("kind")
    entries = load_memory_items(
        ctx.deps.memory_dir,
        memory_kinds=[kind_filter] if kind_filter is not None else None,
    )
    entries = filter_memory_items(entries, filters)
    if query is not None:
        entries = grep_recall(entries, query, max_results=len(entries) or 1)
    console.print(f"{len(entries)} memories")


async def _subcmd_memory_forget(
    ctx: CommandContext, query: str | None, filters: dict[str, Any]
) -> None:
    """Delete matching artifacts after user confirmation.

    Refuses if no query and no filters supplied.
    Always prompts for y/N confirmation before deleting.
    """
    if query is None and not filters:
        console.print(
            "[bold red]Usage:[/bold red] /memory forget <query> [--older-than N] [--kind X]"
        )
        console.print("[dim]Provide a query or at least one filter to select memories.[/dim]")
        return None

    kind_filter = filters.get("kind")
    entries = load_memory_items(
        ctx.deps.memory_dir,
        memory_kinds=[kind_filter] if kind_filter is not None else None,
    )
    entries = filter_memory_items(entries, filters)
    if query is not None:
        entries = grep_recall(entries, query, max_results=len(entries) or 1)

    if not entries:
        console.print("[dim]No memories matched.[/dim]")
        return None

    for m in entries:
        console.print(format_memory_item_row(m))

    prompt_text = f"Delete {len(entries)} memories? [y/N] "
    confirmed = await _confirm(ctx, prompt_text)
    if not confirmed:
        console.print("[dim]Aborted.[/dim]")
        return None

    for m in entries:
        m.path.unlink()
        if ctx.deps.memory_store is not None:
            ctx.deps.memory_store.remove(m.path)

    console.print(f"[success]{glyphs().success} Deleted {len(entries)} memories.[/success]")
    return None


async def _subcmd_knowledge_restore(ctx: CommandContext, rest: str) -> None:
    """List archived artifacts, or restore one whose filename starts with the given slug."""
    from co_cli.memory.archive import restore_artifact
    from co_cli.memory.item import load_memory_item

    tokens = [t for t in rest.split() if not t.startswith("--")]
    slug = tokens[0] if tokens else ""

    archive_dir = ctx.deps.memory_dir / "_archive"
    if not slug:
        if not archive_dir.exists():
            console.print("[dim]No archived artifacts.[/dim]")
            return None
        entries = sorted(p for p in archive_dir.glob("*.md") if p.is_file())
        if not entries:
            console.print("[dim]No archived artifacts.[/dim]")
            return None
        for path in entries:
            slug_prefix = path.stem
            try:
                artifact = load_memory_item(path)
                title = artifact.title or "(untitled)"
            except ValueError as exc:
                title = f"[warning]unreadable: {exc}[/warning]"
            console.print(f"  {slug_prefix}  {title}")
        console.print(f"[dim]{len(entries)} archived artifact(s)[/dim]")
        return None

    restored = restore_artifact(slug, ctx.deps.memory_dir, ctx.deps.memory_store)
    if restored:
        console.print(f"[success]{glyphs().success} Restored {slug}[/success]")
    else:
        console.print(
            f"[bold red]Restore failed:[/bold red] no unambiguous archive match for {slug!r}"
        )


async def _subcmd_knowledge_stats(ctx: CommandContext) -> None:
    """Display knowledge health dashboard: artifact counts, archive size, housekeeping state."""
    from co_cli.config.core import DREAM_DAEMON_DIR
    from co_cli.config.memory import MEMORY_ITEM_COUNT_WARN
    from co_cli.daemons.dream.state import load_housekeeping_state

    knowledge_dir = ctx.deps.memory_dir
    artifacts = load_memory_items(knowledge_dir)
    total = len(artifacts)

    kind_counts: dict[str, int] = {}
    for a in artifacts:
        kind_counts[a.memory_kind] = kind_counts.get(a.memory_kind, 0) + 1
    kind_parts = ", ".join(f"{kind}: {count}" for kind, count in sorted(kind_counts.items()))

    protected = sum(1 for a in artifacts if a.decay_protected)

    archive_dir = knowledge_dir / "_archive"
    archived = len(list(archive_dir.glob("*.md"))) if archive_dir.exists() else 0

    hk = load_housekeeping_state(DREAM_DAEMON_DIR)
    if hk.last_housekeeping_at:
        last_pass = (
            f"{hk.last_housekeeping_at}"
            f" (memory: {hk.stats.memory_merged} merged; "
            f"skill: {hk.stats.skill_merged} merged, {hk.stats.skill_decayed} archived)"
        )
    else:
        last_pass = "never"

    console.print(f"Knowledge: {total} artifacts")
    if kind_parts:
        console.print(f"  {kind_parts}")
    console.print(f"  decay-protected: {protected}")
    if total > MEMORY_ITEM_COUNT_WARN:
        console.print(
            f"[warning]{glyphs().warning} {total} active items exceeds the warn threshold "
            f"({MEMORY_ITEM_COUNT_WARN}) — investigate a possible write loop or "
            f"pollution; nothing is auto-archived.[/warning]"
        )
    console.print(f"Archived: {archived}")
    console.print(f"Last housekeeping: {last_pass}")
    console.print("[dim]hint:[/dim] `co dream tidy` to request a one-shot housekeeping pass")


async def _cmd_memory(ctx: CommandContext, args: str) -> None:
    """Dispatch /memory subcommands: list, count, forget, restore, stats."""
    parts = args.strip().split(maxsplit=1)
    if not parts:
        console.print(_MEMORY_USAGE)
        return None
    subcommand = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if subcommand == "list":
        query, filters = _parse_memory_args(rest)
        await _subcmd_memory_list(ctx, query, filters)
    elif subcommand == "count":
        query, filters = _parse_memory_args(rest)
        await _subcmd_memory_count(ctx, query, filters)
    elif subcommand == "forget":
        query, filters = _parse_memory_args(rest)
        await _subcmd_memory_forget(ctx, query, filters)
    elif subcommand == "restore":
        await _subcmd_knowledge_restore(ctx, rest)
    elif subcommand == "stats":
        await _subcmd_knowledge_stats(ctx)
    else:
        console.print(f"[bold red]Unknown /memory subcommand:[/bold red] {subcommand}")
        console.print(_MEMORY_USAGE)
    return None
