"""Slash command handlers for /skills and subcommands."""

from __future__ import annotations

from co_cli.commands._utils import _confirm
from co_cli.commands.registry import (
    BUILTIN_COMMANDS,
    filter_namespace_conflicts,
    refresh_completer,
)
from co_cli.commands.types import CommandContext
from co_cli.config.core import settings
from co_cli.display.core import console, make_table
from co_cli.skills import usage as skill_usage
from co_cli.skills._lint import lint_skill
from co_cli.skills.installer import (
    SkillFetchError,
    discover_skill_files,
    fetch_skill_content,
    find_skill_source_url,
    read_skill_meta,
    write_skill_file,
)
from co_cli.skills.loader import (
    diagnose_requires_failures,
    load_skills,
    scan_skill_content,
)
from co_cli.skills.registry import set_skill_commands


def _cmd_skills_list(ctx: CommandContext) -> None:
    if not ctx.deps.skill_commands:
        console.print("[dim]No skills loaded.[/dim]")
        return
    table = make_table("Name", "Description", "Requires", "User-Invocable")
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

    table = make_table("File", "Status", "Reason")

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
                failures = diagnose_requires_failures(requires, settings)
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
                for w in scan_skill_content(p.read_text(encoding="utf-8")):
                    console.print(f"[yellow]Security warning in {p.name}: {w}[/yellow]")
            except Exception:
                pass
    old_names = set(ctx.deps.skill_commands.keys())
    set_skill_commands(new_skills, ctx.deps)
    refresh_completer(ctx)
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


def _cmd_skills_lint(ctx: CommandContext, args: str) -> None:
    """Lint one or all loaded skills against R1-R10."""
    args = args.strip()

    if args == "--all":
        skills_to_lint = list(ctx.deps.skill_commands.keys())
    elif args:
        name = args
        if name not in ctx.deps.skill_commands:
            console.print(f"[bold red]Unknown skill:[/bold red] {name!r}")
            return
        skills_to_lint = [name]
    else:
        console.print("[bold red]Usage:[/bold red] /skills lint <name> | --all")
        return

    total = len(skills_to_lint)
    clean_count = 0
    any_findings = False

    for name in skills_to_lint:
        skill = ctx.deps.skill_commands[name]
        path = skill.path
        if path is None:
            console.print(f"[bold red]Skill file path unknown for:[/bold red] {name!r}")
            continue

        content = path.read_text(encoding="utf-8")
        findings = lint_skill(content, path)

        console.print(f"{name} ({path}):")
        if findings:
            any_findings = True
            for finding in findings:
                console.print(f"  {finding.rule} at line {finding.line}: {finding.message}")
        else:
            clean_count += 1
            console.print("  (clean)")

    if total > 1:
        console.print(
            f"\n{clean_count} of {total} skills clean. Exit code {'0' if not any_findings else '1'}."
        )

    if any_findings:
        raise SystemExit(1)


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
    elif subcmd == "lint":
        _cmd_skills_lint(ctx, subargs)
    elif subcmd == "reload":
        _cmd_skills_reload(ctx)
    elif subcmd == "upgrade":
        await _upgrade_skill(ctx, subargs)
    elif subcmd == "usage":
        _cmd_skills_usage(ctx, subargs)
    elif subcmd == "pin":
        _cmd_skills_pin(ctx, subargs, pinned=True)
    elif subcmd == "unpin":
        _cmd_skills_pin(ctx, subargs, pinned=False)
    else:
        console.print(f"[bold red]Unknown /skills subcommand:[/bold red] {subcmd}")
        console.print(
            "[dim]Usage: /skills [list|check|install <path|url>|lint [<name>|--all]|reload|"
            "upgrade <name>|usage [<name>]|pin <name>|unpin <name>][/dim]"
        )

    return None


def _classify_skill(ctx: CommandContext, name: str) -> str:
    """Return one of: 'agent-created', 'url-installed', 'bundled', 'unknown'."""
    user_path = ctx.deps.user_skills_dir / f"{name}.md"
    bundled_path = ctx.deps.skills_dir / f"{name}.md"
    if user_path.exists():
        if find_skill_source_url(user_path) is not None:
            return "url-installed"
        return "agent-created"
    if bundled_path.exists():
        return "bundled"
    return "unknown"


def _cmd_skills_usage(ctx: CommandContext, args: str) -> None:
    """Print the usage sidecar — table for all skills, full record for one."""
    name = args.strip()
    records = skill_usage.read_records(ctx.deps).get("skills", {})

    if name:
        record = records.get(name)
        if record is None:
            console.print(f"[dim]No usage record for skill '{name}'.[/dim]")
            return
        table = make_table("Field", "Value")
        for key in (
            "use_count",
            "view_count",
            "patch_count",
            "created_at",
            "last_used_at",
            "last_viewed_at",
            "last_patched_at",
            "state",
            "pinned",
        ):
            table.add_row(key, str(record.get(key)))
        console.print(table)
        return

    if not records:
        console.print("[dim]No skill usage records yet.[/dim]")
        return

    table = make_table("Name", "use", "view", "patch", "last_used_at", "state", "pinned")
    for skill_name in sorted(records.keys()):
        rec = records[skill_name]
        table.add_row(
            skill_name,
            str(rec.get("use_count", 0)),
            str(rec.get("view_count", 0)),
            str(rec.get("patch_count", 0)),
            str(rec.get("last_used_at") or "-"),
            str(rec.get("state", "active")),
            "✓" if rec.get("pinned") else "",
        )
    console.print(table)


def _cmd_skills_pin(ctx: CommandContext, args: str, *, pinned: bool) -> None:
    """Toggle the pinned flag on an agent-created skill."""
    verb = "pin" if pinned else "unpin"
    name = args.strip()
    if not name:
        console.print(f"[bold red]Usage:[/bold red] /skills {verb} <name>")
        return

    classification = _classify_skill(ctx, name)
    if classification == "bundled":
        console.print(
            f"[bold red]Cannot {verb} '{name}': bundled skill (upstream-managed).[/bold red]"
        )
        return
    if classification == "url-installed":
        console.print(
            f"[bold red]Cannot {verb} '{name}': URL-installed skill (upstream-managed).[/bold red]"
        )
        return
    if classification == "unknown":
        console.print(f"[bold red]Skill '{name}' not found.[/bold red]")
        return

    skill_usage.set_pinned(ctx.deps, name, pinned)
    state = "pinned" if pinned else "unpinned"
    console.print(f"[success]✓ Skill '{name}' {state}.[/success]")


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

    warnings = scan_skill_content(content)
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
    refresh_completer(ctx)

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
