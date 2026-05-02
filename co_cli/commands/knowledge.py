"""Slash command handlers for /knowledge and /memory (deprecated alias)."""

from __future__ import annotations

from typing import Any

from co_cli.commands.types import CommandContext
from co_cli.display.core import console
from co_cli.memory.artifact import load_knowledge_artifacts
from co_cli.memory.query import _apply_memory_filters, _format_memory_row
from co_cli.tools.memory.read import grep_recall

_MEMORY_USAGE = (
    "[bold]Usage:[/bold] /memory list|count|forget [query] "
    "[--older-than N] [--kind preference|feedback|rule|decision|article|reference|note]"
)

_KNOWLEDGE_USAGE = (
    "[bold]Usage:[/bold] /knowledge list|count|forget|dream|restore|decay-review|stats "
    "[query] [--older-than N] "
    "[--kind preference|feedback|rule|decision|article|reference|note] [--dry]"
)


def _parse_memory_args(args: str) -> tuple[str | None, dict[str, Any]]:
    """Parse /memory subcommand args into (query, filters).

    Flags: ``--older-than N`` (int days), ``--kind X`` (artifact_kind).
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
    entries = load_knowledge_artifacts(
        ctx.deps.knowledge_dir,
        artifact_kinds=[kind_filter] if kind_filter is not None else None,
    )
    entries = _apply_memory_filters(entries, filters)
    if query is not None:
        entries = grep_recall(entries, query, max_results=len(entries) or 1)
    if not entries:
        console.print("[dim]No memories found.[/dim]")
    else:
        for m in entries:
            console.print(_format_memory_row(m))
    console.print(f"[dim]{len(entries)} memories[/dim]")


async def _subcmd_memory_count(
    ctx: CommandContext, query: str | None, filters: dict[str, Any]
) -> None:
    """Print the count of matching artifacts."""
    kind_filter = filters.get("kind")
    entries = load_knowledge_artifacts(
        ctx.deps.knowledge_dir,
        artifact_kinds=[kind_filter] if kind_filter is not None else None,
    )
    entries = _apply_memory_filters(entries, filters)
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
    entries = load_knowledge_artifacts(
        ctx.deps.knowledge_dir,
        artifact_kinds=[kind_filter] if kind_filter is not None else None,
    )
    entries = _apply_memory_filters(entries, filters)
    if query is not None:
        entries = grep_recall(entries, query, max_results=len(entries) or 1)

    if not entries:
        console.print("[dim]No memories matched.[/dim]")
        return None

    for m in entries:
        console.print(_format_memory_row(m))

    prompt_text = f"Delete {len(entries)} memories? [y/N] "
    confirmed = (
        ctx.frontend.prompt_confirm(prompt_text)
        if ctx.frontend
        else console.input(prompt_text).strip().lower() == "y"
    )
    if not confirmed:
        console.print("[dim]Aborted.[/dim]")
        return None

    for m in entries:
        m.path.unlink()
        if ctx.deps.memory_store is not None:
            ctx.deps.memory_store.remove("knowledge", str(m.path))

    console.print(f"[success]✓ Deleted {len(entries)} memories.[/success]")
    return None


async def _subcmd_knowledge_dream(ctx: CommandContext, rest: str) -> None:
    """Manually trigger a dream cycle; honour ``--dry`` for a non-destructive preview."""
    from co_cli.memory.dream import run_dream_cycle

    tokens = rest.split()
    dry_run = "--dry" in tokens

    result = await run_dream_cycle(ctx.deps, dry_run=dry_run)

    header = "Dream cycle — dry run — no changes written" if dry_run else "Dream cycle complete"
    console.print(f"[info]{header}[/info]")
    console.print(
        f"  extracted: {result.extracted}  merged: {result.merged}  decayed: {result.decayed}"
    )
    if result.errors:
        console.print(f"[warning]errors ({len(result.errors)}):[/warning]")
        for err in result.errors:
            console.print(f"  - {err}")


async def _subcmd_knowledge_restore(ctx: CommandContext, rest: str) -> None:
    """List archived artifacts, or restore one whose filename starts with the given slug."""
    from co_cli.memory.archive import restore_artifact
    from co_cli.memory.artifact import load_knowledge_artifact

    tokens = [t for t in rest.split() if not t.startswith("--")]
    slug = tokens[0] if tokens else ""

    archive_dir = ctx.deps.knowledge_dir / "_archive"
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
                artifact = load_knowledge_artifact(path)
                title = artifact.title or "(untitled)"
            except ValueError as exc:
                title = f"[warning]unreadable: {exc}[/warning]"
            console.print(f"  {slug_prefix}  {title}")
        console.print(f"[dim]{len(entries)} archived artifact(s)[/dim]")
        return None

    restored = restore_artifact(slug, ctx.deps.knowledge_dir, ctx.deps.memory_store)
    if restored:
        console.print(f"[success]✓ Restored {slug}[/success]")
    else:
        console.print(
            f"[bold red]Restore failed:[/bold red] no unambiguous archive match for {slug!r}"
        )


async def _subcmd_knowledge_decay_review(ctx: CommandContext, rest: str) -> None:
    """Preview decay candidates and, with confirmation, archive them."""
    from co_cli.memory.archive import archive_artifacts
    from co_cli.memory.decay import find_decay_candidates

    tokens = rest.split()
    dry_run = "--dry" in tokens

    candidates = find_decay_candidates(ctx.deps.knowledge_dir, ctx.deps.config.knowledge)
    if not candidates:
        console.print("[dim]No decay candidates.[/dim]")
        return None

    for art in candidates:
        created = (art.created or "")[:10]
        last = art.last_recalled[:10] if art.last_recalled else "never"
        slug_prefix = art.path.stem
        console.print(f"  {slug_prefix}  created={created}  last_recalled={last}")
    console.print(f"[dim]{len(candidates)} decay candidate(s)[/dim]")

    if dry_run:
        return None

    prompt_text = f"Archive {len(candidates)} decay candidates? [y/N] "
    confirmed = (
        ctx.frontend.prompt_confirm(prompt_text)
        if ctx.frontend
        else console.input(prompt_text).strip().lower() == "y"
    )
    if not confirmed:
        console.print("[dim]Aborted.[/dim]")
        return None

    archived = archive_artifacts(candidates, ctx.deps.knowledge_dir, ctx.deps.memory_store)
    console.print(f"[success]✓ Archived {archived}.[/success]")


async def _subcmd_knowledge_stats(ctx: CommandContext) -> None:
    """Display knowledge health dashboard: artifact counts, archive size, dream state, decay."""
    from co_cli.memory.decay import find_decay_candidates
    from co_cli.memory.dream import load_dream_state

    knowledge_dir = ctx.deps.knowledge_dir
    artifacts = load_knowledge_artifacts(knowledge_dir)
    total = len(artifacts)

    kind_counts: dict[str, int] = {}
    for a in artifacts:
        kind_counts[a.artifact_kind] = kind_counts.get(a.artifact_kind, 0) + 1
    kind_parts = ", ".join(f"{kind}: {count}" for kind, count in sorted(kind_counts.items()))

    protected = sum(1 for a in artifacts if a.decay_protected)

    archive_dir = knowledge_dir / "_archive"
    archived = len(list(archive_dir.glob("*.md"))) if archive_dir.exists() else 0

    state = load_dream_state(knowledge_dir)
    if state.last_dream_at:
        s = state.stats
        last_dream = (
            f"{state.last_dream_at}"
            f" (total: {s.total_extracted} extracted, {s.total_merged} merged,"
            f" {s.total_decayed} archived)"
        )
    else:
        last_dream = "never"

    candidates = find_decay_candidates(knowledge_dir, ctx.deps.config.knowledge)

    console.print(f"Knowledge: {total} artifacts")
    if kind_parts:
        console.print(f"  {kind_parts}")
    console.print(f"  decay-protected: {protected}")
    console.print(f"Archived: {archived}")
    console.print(f"Last dream: {last_dream}")
    console.print(f"Decay candidates: {len(candidates)}")


async def _cmd_knowledge(ctx: CommandContext, args: str) -> None:
    """Dispatch /knowledge subcommands: list, count, forget, dream, restore, decay-review, stats."""
    parts = args.strip().split(maxsplit=1)
    if not parts:
        console.print(_KNOWLEDGE_USAGE)
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
    elif subcommand == "dream":
        await _subcmd_knowledge_dream(ctx, rest)
    elif subcommand == "restore":
        await _subcmd_knowledge_restore(ctx, rest)
    elif subcommand == "decay-review":
        await _subcmd_knowledge_decay_review(ctx, rest)
    elif subcommand == "stats":
        await _subcmd_knowledge_stats(ctx)
    else:
        console.print(f"[bold red]Unknown /knowledge subcommand:[/bold red] {subcommand}")
        console.print(_KNOWLEDGE_USAGE)
    return None


async def _cmd_memory(ctx: CommandContext, args: str) -> None:
    """[Deprecated] Use /knowledge instead. Dispatch /memory subcommands: list, count, forget."""
    console.print("[dim]/memory is deprecated — use /knowledge instead.[/dim]")
    parts = args.strip().split(maxsplit=1)
    if not parts:
        console.print(_MEMORY_USAGE)
        return None
    subcommand = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    query, filters = _parse_memory_args(rest)
    if subcommand == "list":
        await _subcmd_memory_list(ctx, query, filters)
    elif subcommand == "count":
        await _subcmd_memory_count(ctx, query, filters)
    elif subcommand == "forget":
        await _subcmd_memory_forget(ctx, query, filters)
    else:
        console.print(f"[bold red]Unknown /memory subcommand:[/bold red] {subcommand}")
        console.print(_MEMORY_USAGE)
    return None
