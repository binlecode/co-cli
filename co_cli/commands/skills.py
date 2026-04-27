"""Slash command handlers for /skills and subcommands, plus get_skill_registry utility."""

from __future__ import annotations

from rich.table import Table

from co_cli.commands._registry import (
    BUILTIN_COMMANDS,
    _refresh_completer,
    filter_namespace_conflicts,
)
from co_cli.commands._types import CommandContext, _confirm
from co_cli.config._core import settings
from co_cli.display._core import console
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


def get_skill_registry(skill_commands: dict[str, SkillConfig]) -> list[dict]:
    """Derive model-facing skill registry from skill_commands."""
    return [
        {"name": s.name, "description": s.description}
        for s in skill_commands.values()
        if s.description and not s.disable_model_invocation
    ]


def _cmd_skills_list(ctx: CommandContext) -> None:
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
                failures = _diagnose_requires_failures(requires, settings)
                reason = "; ".join(failures) if failures else "name conflict with built-in"
                table.add_row(path.name, "[bold red]✗ Skipped[/bold red]", reason)
            except Exception as e:
                table.add_row(path.name, "[bold red]✗ Error[/bold red]", str(e))

    console.print(table)


def _cmd_skills_reload(ctx: CommandContext) -> None:
    user_skills_dir = ctx.deps.user_skills_dir
    errors: list[str] = []
    new_skills = load_skills(
        ctx.deps.skills_dir, settings, user_skills_dir=user_skills_dir, errors=errors
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
        ctx.deps.skills_dir, settings, user_skills_dir=ctx.deps.user_skills_dir, errors=errors
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
