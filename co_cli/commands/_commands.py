"""Slash command registry, handlers, and dispatch for the REPL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelRequest

from co_cli.commands._registry import (
    BUILTIN_COMMANDS,
    SlashCommand,
    _refresh_completer,
    filter_namespace_conflicts,
)
from co_cli.commands._types import (
    CommandContext,
    DelegateToAgent,
    LocalOnly,
    ReplaceTranscript,
    SlashOutcome,
    _confirm,
)
from co_cli.commands.session import (
    _cmd_clear,
    _cmd_compact,
    _cmd_new,
    _cmd_resume,
    _cmd_sessions,
)
from co_cli.config._core import VALID_REASONING_DISPLAY_MODES
from co_cli.deps import ApprovalKindEnum
from co_cli.display._core import console
from co_cli.knowledge._artifact import KnowledgeArtifact, load_knowledge_artifacts
from co_cli.skills._skill_types import SkillConfig
from co_cli.skills.installer import (
    SkillFetchError,
    discover_skill_files,
    fetch_skill_content,
    find_skill_source_url,
    read_skill_meta,
    write_skill_file,
)
from co_cli.skills.loader import (
    _diagnose_requires_failures,
    _scan_skill_content,
    load_skills,
)
from co_cli.skills.registry import set_skill_commands
from co_cli.tools.knowledge.read import grep_recall

logger = logging.getLogger(__name__)


def get_skill_registry(skill_commands: dict[str, SkillConfig]) -> list[dict]:
    """Derive model-facing skill registry from skill_commands."""
    return [
        {"name": s.name, "description": s.description}
        for s in skill_commands.values()
        if s.description and not s.disable_model_invocation
    ]


# -- Handlers --------------------------------------------------------------


async def _cmd_help(ctx: CommandContext, args: str) -> None:
    """List available slash commands."""
    from rich.table import Table

    table = Table(title="Slash Commands", border_style="accent", expand=False)
    table.add_column("Command", style="accent")
    table.add_column("Description")
    for cmd in BUILTIN_COMMANDS.values():
        table.add_row(f"/{cmd.name}", cmd.description)
    if ctx.deps.skill_commands:
        for skill in ctx.deps.skill_commands.values():
            if skill.user_invocable:
                hint = f"  [{skill.argument_hint}]" if skill.argument_hint else ""
                table.add_row(f"/{skill.name}{hint}", skill.description or "(skill)")
    console.print(table)
    console.print(
        "[dim]Usage: /status shows system health; /status <task-id> shows a background task.[/dim]"
    )
    return None


async def _cmd_status(ctx: CommandContext, args: str) -> None:
    """Show system health, or task status when <id> is given."""
    task_id = args.strip()
    if task_id:
        state = ctx.deps.session.background_tasks.get(task_id)
        if state is None:
            console.print(f"[bold red]Task not found:[/bold red] {task_id}")
            return None
        from rich.table import Table

        table = Table(title=f"Task: {task_id}", border_style="accent", expand=False)
        table.add_column("Field", style="accent")
        table.add_column("Value")
        for k, v in [
            ("task_id", state.task_id),
            ("status", state.status),
            ("command", state.command),
            ("description", state.description),
            ("started_at", state.started_at),
            ("completed_at", state.completed_at or ""),
            ("exit_code", str(state.exit_code) if state.exit_code is not None else ""),
        ]:
            table.add_row(k, v)
        console.print(table)
        lines = list(state.output_lines)[-20:]
        if lines:
            console.print("[dim]--- Output (last 20 lines) ---[/dim]")
            for line in lines:
                console.print(line)
        return None

    from co_cli.bootstrap.render_status import (
        check_security,
        get_status,
        render_security_findings,
        render_status_table,
    )

    info = get_status(ctx.deps.config, tool_count=len(ctx.deps.tool_index))
    console.print(render_status_table(info))
    findings = check_security()
    render_security_findings(findings)
    return None


async def _cmd_tools(ctx: CommandContext, args: str) -> None:
    """List registered agent tools."""
    tools = sorted(ctx.deps.tool_index.keys())
    lines = [f"  [accent]{i + 1}.[/accent] {name}" for i, name in enumerate(tools)]
    console.print(f"[info]Registered tools ({len(tools)}):[/info]")
    console.print("\n".join(lines))
    return None


async def _cmd_history(ctx: CommandContext, args: str) -> None:
    """Show conversation delegation history (run_id, role, requests, scope)."""
    from pydantic_ai.messages import ToolReturnPart

    _DELEGATION_TOOLS = frozenset(
        {
            "web_research",
            "knowledge_analyze",
            "reason",
            "task_start",
        }
    )

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

    from rich.table import Table

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


def _cmd_skills_list(ctx: CommandContext) -> None:
    from rich.table import Table

    if not ctx.deps.skill_commands:
        console.print("[dim]No skills loaded.[/dim]")
        return
    table = Table(title="Loaded Skills", border_style="accent", expand=False)
    table.add_column("Name", style="accent")
    table.add_column("Description")
    table.add_column("Requires")
    table.add_column("User-Invocable")
    for skill in ctx.deps.skill_commands.values():
        req_keys = ", ".join(skill.requires.keys()) if skill.requires else ""
        table.add_row(
            skill.name,
            skill.description or "",
            req_keys,
            "✓" if skill.user_invocable else "✗",
        )
    console.print(table)


def _cmd_skills_check(ctx: CommandContext) -> None:
    from rich.table import Table

    from co_cli.config._core import settings as _settings

    all_paths = discover_skill_files(ctx.deps.skills_dir, ctx.deps.user_skills_dir)

    if not all_paths:
        console.print("[dim]No skill files found.[/dim]")
        return

    table = Table(title="Skills Check", border_style="accent", expand=False)
    table.add_column("File", style="accent")
    table.add_column("Status")
    table.add_column("Reason")

    for path in all_paths:
        name = path.stem
        if name in ctx.deps.skill_commands:
            table.add_row(path.name, "[success]✓ Loaded[/success]", "")
        else:
            try:
                meta = read_skill_meta(path)
                requires = (
                    meta.get("requires", {}) if isinstance(meta.get("requires"), dict) else {}
                )
                failures = _diagnose_requires_failures(requires, _settings)
                reason = "; ".join(failures) if failures else "name conflict with built-in"
                table.add_row(path.name, "[bold red]✗ Skipped[/bold red]", reason)
            except Exception as e:
                table.add_row(path.name, "[bold red]✗ Error[/bold red]", str(e))

    console.print(table)


def _cmd_skills_reload(ctx: CommandContext) -> None:
    from co_cli.config._core import settings as _settings

    # handler (not a tool) — direct settings import acceptable, matches _install_skill pattern
    user_skills_dir = ctx.deps.user_skills_dir
    errors: list[str] = []
    new_skills = load_skills(
        ctx.deps.skills_dir, _settings, user_skills_dir=user_skills_dir, errors=errors
    )
    new_skills = filter_namespace_conflicts(new_skills, set(BUILTIN_COMMANDS.keys()), errors)
    for msg in errors:
        console.print(f"[warning]{msg}[/warning]")
    for name in new_skills:
        p = user_skills_dir / f"{name}.md"
        if p.exists():
            try:
                for w in _scan_skill_content(p.read_text(encoding="utf-8")):
                    console.print(f"[yellow]Security warning in {p.name}: {w}[/yellow]")
            except Exception:
                pass
    old_names = set(ctx.deps.skill_commands.keys())
    set_skill_commands(new_skills, ctx.deps)
    _refresh_completer(ctx)
    added = set(new_skills.keys()) - old_names
    removed = old_names - set(new_skills.keys())
    if added:
        if len(added) <= 5:
            console.print(f"[success]+ Added ({len(added)}): {', '.join(sorted(added))}[/success]")
        else:
            console.print(f"[success]+ Added: {len(added)} skill(s)[/success]")
    if removed:
        if len(removed) <= 5:
            console.print(f"[dim]- Removed ({len(removed)}): {', '.join(sorted(removed))}[/dim]")
        else:
            console.print(f"[dim]- Removed: {len(removed)} skill(s)[/dim]")
    if not added and not removed:
        console.print("[dim]No skill changes.[/dim]")
    console.print(f"[success]✓ Reloaded {len(ctx.deps.skill_commands)} skill(s)[/success]")


async def _cmd_skills(ctx: CommandContext, args: str) -> None:
    """List and inspect loaded skills, or install a new one."""
    sub = args.strip().split(maxsplit=1)
    subcmd = sub[0].lower() if sub else "list"
    subargs = sub[1] if len(sub) > 1 else ""

    if subcmd in ("", "list"):
        _cmd_skills_list(ctx)
    elif subcmd == "check":
        _cmd_skills_check(ctx)
    elif subcmd == "install":
        await _install_skill(ctx, subargs)
    elif subcmd == "reload":
        _cmd_skills_reload(ctx)
    elif subcmd == "upgrade":
        await _upgrade_skill(ctx, subargs)
    else:
        console.print(f"[bold red]Unknown /skills subcommand:[/bold red] {subcmd}")
        console.print(
            "[dim]Usage: /skills [list|check|install <path|url>|reload|upgrade <name>][/dim]"
        )

    return None


async def _install_skill(ctx: CommandContext, target: str, force: bool = False) -> None:
    """Copy a skill .md file from a local path or URL into skills_dir and reload."""
    from co_cli.config._core import settings as _settings

    target = target.strip()
    if not target:
        console.print("[bold red]Usage:[/bold red] /skills install <path|url>")
        return

    try:
        content, filename = fetch_skill_content(target)
    except SkillFetchError as e:
        console.print(f"[bold red]Failed to fetch skill:[/bold red] {e}")
        return

    warnings = _scan_skill_content(content)
    if warnings:
        console.print("[bold yellow]Security scan warnings:[/bold yellow]")
        for w in warnings:
            console.print(f"  [yellow]{w}[/yellow]")
        if not _confirm(ctx, "Install anyway? [y/N] "):
            console.print("[dim]Install cancelled.[/dim]")
            return

    dest = ctx.deps.user_skills_dir / filename
    if (
        dest.exists()
        and not force
        and not _confirm(ctx, f"Overwrite existing skill '{filename}'? [y/N] ")
    ):
        console.print("[dim]Install cancelled.[/dim]")
        return

    write_skill_file(content, filename, ctx.deps.user_skills_dir)

    errors: list[str] = []
    new_skills = load_skills(
        ctx.deps.skills_dir, _settings, user_skills_dir=ctx.deps.user_skills_dir, errors=errors
    )
    new_skills = filter_namespace_conflicts(new_skills, set(BUILTIN_COMMANDS.keys()), errors)
    for msg in errors:
        console.print(f"[warning]{msg}[/warning]")
    set_skill_commands(new_skills, ctx.deps)
    _refresh_completer(ctx)

    console.print(f"[success]✓ Installed skill: {filename.removesuffix('.md')}[/success]")


async def _upgrade_skill(ctx: CommandContext, args: str) -> None:
    """Re-fetch and reinstall a skill that was installed from a URL."""
    name = args.strip()
    if not name:
        console.print("[bold red]Usage:[/bold red] /skills upgrade <name>")
        return
    if name not in ctx.deps.skill_commands:
        console.print(f"[bold red]Skill '{name}' not found.[/bold red]")
        return
    skill_file = ctx.deps.user_skills_dir / f"{name}.md"
    if not skill_file.exists():
        console.print(f"[bold red]Skill '{name}' not found in user skills dir.[/bold red]")
        return
    source_url = find_skill_source_url(skill_file)
    if not source_url:
        console.print(
            f"[bold red]Skill '{name}' has no source-url — not installed from a URL.[/bold red]"
        )
        return
    await _install_skill(ctx, source_url, force=True)


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
        from rich.table import Table

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


async def _cmd_background(ctx: CommandContext, args: str) -> None:
    """Run a command in the background. Usage: /background <cmd>"""

    from co_cli.tools.background import BackgroundTaskState, _make_task_id, spawn_task

    cmd = args.strip()
    if not cmd:
        console.print("[bold red]Usage:[/bold red] /background <command>")
        console.print("[dim]Example: /background uv run pytest[/dim]")
        return None

    task_id = _make_task_id()
    state = BackgroundTaskState(
        task_id=task_id,
        command=cmd,
        cwd=str(Path.cwd()),
        description=cmd,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    ctx.deps.session.background_tasks[task_id] = state
    try:
        await spawn_task(state, ctx.deps.session)
        console.print(f"[success][{task_id}] started[/success]")
        console.print(f"[dim]Use /status {task_id} to check progress.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to start background task:[/bold red] {e}")
    return None


async def _cmd_tasks(ctx: CommandContext, args: str) -> None:
    """List background tasks. Usage: /tasks [status]"""
    status_filter = args.strip() or None
    tasks_dict = ctx.deps.session.background_tasks
    if status_filter:
        tasks = [s for s in tasks_dict.values() if s.status == status_filter]
    else:
        tasks = list(tasks_dict.values())

    if not tasks:
        filter_note = f" with status={status_filter}" if status_filter else ""
        console.print(f"[dim]No background tasks{filter_note}.[/dim]")
        return None

    from rich.table import Table

    label = f"Background Tasks ({status_filter or 'all'})"
    table = Table(title=label, border_style="accent", expand=False)
    table.add_column("Task ID", style="accent")
    table.add_column("Status")
    table.add_column("Command")
    table.add_column("Started")
    for s in tasks:
        started = (s.started_at or "")[:19]
        table.add_row(s.task_id, s.status, s.command, started)
    console.print(table)
    return None


async def _cmd_cancel(ctx: CommandContext, args: str) -> None:
    """Cancel a running background task. Usage: /cancel <task_id>"""
    from co_cli.tools.background import BackgroundCleanupError, kill_task

    task_id = args.strip()
    if not task_id:
        console.print("[bold red]Usage:[/bold red] /cancel <task_id>")
        return None

    state = ctx.deps.session.background_tasks.get(task_id)
    if state is None:
        console.print(f"[bold red]Task not found:[/bold red] {task_id}")
        return None

    if state.status != "running":
        console.print(f"[dim]Task {task_id} is not running (status={state.status}).[/dim]")
        return None

    try:
        await kill_task(state)
    except BackgroundCleanupError as e:
        console.print(f"[bold red]Cancel cleanup failed:[/bold red] {e}")
        return None
    console.print(f"[success]✓ Cancelled task {task_id}[/success]")
    return None


# -- /memory ---------------------------------------------------------------

_MEMORY_USAGE = (
    "[bold]Usage:[/bold] /memory list|count|forget [query] "
    "[--older-than N] [--kind preference|feedback|rule|decision|article|reference|note]"
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


def _apply_memory_filters(
    entries: list[KnowledgeArtifact], filters: dict[str, Any]
) -> list[KnowledgeArtifact]:
    """Apply older_than_days filter to a loaded artifact list.

    ``kind`` is applied upstream via ``load_knowledge_artifacts(artifact_kind=...)``
    and is not re-applied here.
    """
    result = entries
    if "older_than_days" in filters:
        cutoff_days = filters["older_than_days"]
        now = datetime.now(UTC)
        result = [
            m
            for m in result
            if (now - datetime.fromisoformat(m.created.replace("Z", "+00:00"))).days > cutoff_days
        ]
    return result


def _format_memory_row(m: KnowledgeArtifact) -> str:
    id_prefix = m.id[:8]
    created = m.created[:10]
    snippet = m.content[:80]
    return f"{id_prefix}  {created}  [{m.artifact_kind}]  {snippet}"


async def _subcmd_memory_list(
    ctx: CommandContext, query: str | None, filters: dict[str, Any]
) -> None:
    """Display matching knowledge artifacts — one line each, with a count footer."""
    kind_filter = filters.get("kind")
    entries = load_knowledge_artifacts(ctx.deps.knowledge_dir, artifact_kind=kind_filter)
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
    entries = load_knowledge_artifacts(ctx.deps.knowledge_dir, artifact_kind=kind_filter)
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
    entries = load_knowledge_artifacts(ctx.deps.knowledge_dir, artifact_kind=kind_filter)
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
        if ctx.deps.knowledge_store is not None:
            ctx.deps.knowledge_store.remove("knowledge", str(m.path))

    console.print(f"[success]✓ Deleted {len(entries)} memories.[/success]")
    return None


_KNOWLEDGE_USAGE = (
    "[bold]Usage:[/bold] /knowledge list|count|forget|dream|restore|decay-review|stats "
    "[query] [--older-than N] "
    "[--kind preference|feedback|rule|decision|article|reference|note] [--dry]"
)


async def _subcmd_knowledge_dream(ctx: CommandContext, rest: str) -> None:
    """Manually trigger a dream cycle; honour ``--dry`` for a non-destructive preview."""
    from co_cli.knowledge._dream import run_dream_cycle

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
    from co_cli.knowledge._archive import restore_artifact
    from co_cli.knowledge._artifact import load_knowledge_artifact

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

    restored = restore_artifact(slug, ctx.deps.knowledge_dir, ctx.deps.knowledge_store)
    if restored:
        console.print(f"[success]✓ Restored {slug}[/success]")
    else:
        console.print(
            f"[bold red]Restore failed:[/bold red] no unambiguous archive match for {slug!r}"
        )


async def _subcmd_knowledge_decay_review(ctx: CommandContext, rest: str) -> None:
    """Preview decay candidates and, with confirmation, archive them."""
    from co_cli.knowledge._archive import archive_artifacts
    from co_cli.knowledge._decay import find_decay_candidates

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

    archived = archive_artifacts(candidates, ctx.deps.knowledge_dir, ctx.deps.knowledge_store)
    console.print(f"[success]✓ Archived {archived}.[/success]")


async def _subcmd_knowledge_stats(ctx: CommandContext) -> None:
    """Display knowledge health dashboard: artifact counts, archive size, dream state, decay."""
    from co_cli.knowledge._decay import find_decay_candidates
    from co_cli.knowledge._dream import load_dream_state

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


# -- Reasoning display command ---------------------------------------------

_REASONING_CYCLE = ["off", "summary", "full"]


async def _cmd_reasoning(ctx: CommandContext, args: str) -> None:
    """Show or set the reasoning display mode for this session."""
    token = args.strip().lower()
    if not token:
        console.print(
            f"Reasoning display: [highlight]{ctx.deps.session.reasoning_display}[/highlight]"
        )
        return None
    if token in ("next", "cycle"):
        current = ctx.deps.session.reasoning_display
        idx = _REASONING_CYCLE.index(current) if current in _REASONING_CYCLE else 0
        ctx.deps.session.reasoning_display = _REASONING_CYCLE[(idx + 1) % len(_REASONING_CYCLE)]
    elif token in VALID_REASONING_DISPLAY_MODES:
        ctx.deps.session.reasoning_display = token
    else:
        console.print(
            f"[error]Unknown reasoning mode: {token!r}. Valid: off, summary, full, next[/error]"
        )
        return None
    console.print(
        f"Reasoning display: [highlight]{ctx.deps.session.reasoning_display}[/highlight]"
    )
    return None


# -- Registry --------------------------------------------------------------

BUILTIN_COMMANDS["help"] = SlashCommand("help", "List available slash commands", _cmd_help)
BUILTIN_COMMANDS["clear"] = SlashCommand("clear", "Clear conversation history", _cmd_clear)
BUILTIN_COMMANDS["new"] = SlashCommand("new", "Start a fresh session", _cmd_new)
BUILTIN_COMMANDS["status"] = SlashCommand(
    "status", "Show system health or /status <task-id>", _cmd_status
)
BUILTIN_COMMANDS["tools"] = SlashCommand("tools", "List registered agent tools", _cmd_tools)
BUILTIN_COMMANDS["history"] = SlashCommand(
    "history", "Show delegation history (delegation agents + background tasks)", _cmd_history
)
BUILTIN_COMMANDS["compact"] = SlashCommand(
    "compact", "Summarize conversation via LLM to reduce context", _cmd_compact
)
BUILTIN_COMMANDS["knowledge"] = SlashCommand(
    "knowledge",
    "Manage knowledge artifacts — /knowledge list|count|forget|dream|restore|decay-review|stats [args]",
    _cmd_knowledge,
)
BUILTIN_COMMANDS["memory"] = SlashCommand(
    "memory",
    "[Deprecated] Use /knowledge — /memory list|count|forget [query] [flags]",
    _cmd_memory,
)
BUILTIN_COMMANDS["approvals"] = SlashCommand(
    "approvals", "Manage session approval rules", _cmd_approvals
)
BUILTIN_COMMANDS["skills"] = SlashCommand("skills", "List and inspect loaded skills", _cmd_skills)
BUILTIN_COMMANDS["background"] = SlashCommand(
    "background", "Run a command in the background", _cmd_background
)
BUILTIN_COMMANDS["tasks"] = SlashCommand("tasks", "List background tasks", _cmd_tasks)
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
