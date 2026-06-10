"""Slash command handlers for /skills and subcommands."""

from __future__ import annotations

from co_cli.commands.registry import (
    BUILTIN_COMMANDS,
    filter_namespace_conflicts,
    refresh_completer,
)
from co_cli.commands.types import CommandContext
from co_cli.display.core import console, make_table
from co_cli.skills import usage as skill_usage
from co_cli.skills.index import set_skill_catalog
from co_cli.skills.lifecycle import discover_skill_files, read_skill_meta
from co_cli.skills.lint import lint_skill
from co_cli.skills.loader import load_skills, scan_skill_content


def _cmd_skills_list(ctx: CommandContext) -> None:
    if not ctx.deps.skill_catalog:
        console.print("[dim]No skills loaded.[/dim]")
        return
    table = make_table("Name", "Description", "User-Invocable")
    for skill in ctx.deps.skill_catalog.values():
        table.add_row(
            skill.name,
            skill.description or "",
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
        name = path.parent.name
        if name in ctx.deps.skill_catalog:
            table.add_row(name, "[success]✓ Loaded[/success]", "")
        else:
            try:
                read_skill_meta(path)
                table.add_row(
                    name, "[bold red]✗ Skipped[/bold red]", "name conflict with built-in"
                )
            except Exception as e:
                table.add_row(name, "[bold red]✗ Error[/bold red]", str(e))

    console.print(table)


def _cmd_skills_reload(ctx: CommandContext) -> None:
    user_skills_dir = ctx.deps.user_skills_dir
    errors: list[str] = []
    new_skills = load_skills(ctx.deps.skills_dir, user_skills_dir=user_skills_dir, errors=errors)
    new_skills = filter_namespace_conflicts(new_skills, set(BUILTIN_COMMANDS.keys()), errors)
    for msg in errors:
        console.print(f"[warning]{msg}[/warning]")
    for name in new_skills:
        p = user_skills_dir / name / "SKILL.md"
        if p.exists():
            try:
                for w in scan_skill_content(p.read_text(encoding="utf-8")):
                    console.print(f"[yellow]Security warning in {name}: {w}[/yellow]")
            except Exception:
                pass
    old_names = set(ctx.deps.skill_catalog.keys())
    set_skill_catalog(new_skills, ctx.deps)
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
    console.print(f"[success]✓ Reloaded {len(ctx.deps.skill_catalog)} skill(s)[/success]")


def _cmd_skills_lint(ctx: CommandContext, args: str) -> None:
    """Lint one or all loaded skills against R1-R4."""
    args = args.strip()

    if args == "--all":
        skills_to_lint = list(ctx.deps.skill_catalog.keys())
    elif args:
        name = args
        if name not in ctx.deps.skill_catalog:
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
        skill = ctx.deps.skill_catalog[name]
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
    """List and inspect loaded skills."""
    sub = args.strip().split(maxsplit=1)
    subcmd = sub[0].lower() if sub else "list"
    subargs = sub[1] if len(sub) > 1 else ""

    if subcmd in ("", "list"):
        _cmd_skills_list(ctx)
    elif subcmd == "check":
        _cmd_skills_check(ctx)
    elif subcmd == "lint":
        _cmd_skills_lint(ctx, subargs)
    elif subcmd == "reload":
        _cmd_skills_reload(ctx)
    elif subcmd == "usage":
        _cmd_skills_usage(ctx, subargs)
    elif subcmd == "pin":
        _cmd_skills_pin(ctx, subargs, pinned=True)
    elif subcmd == "unpin":
        _cmd_skills_pin(ctx, subargs, pinned=False)
    else:
        console.print(f"[bold red]Unknown /skills subcommand:[/bold red] {subcmd}")
        console.print(
            "[dim]Usage: /skills [list|check|lint [<name>|--all]|reload|"
            "usage [<name>]|pin <name>|unpin <name>][/dim]"
        )

    return None


def _cmd_skills_usage(ctx: CommandContext, args: str) -> None:
    """Print the usage sidecars — table for all skills, full record for one."""
    name = args.strip()

    if name:
        record = skill_usage.read_record(ctx.deps, name)
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
            table.add_row(key, str(record[key]))
        console.print(table)
        return

    records = dict(skill_usage.iter_records(ctx.deps))
    if not records:
        console.print("[dim]No skill usage records yet.[/dim]")
        return

    table = make_table("Name", "use", "view", "patch", "last_used_at", "state", "pinned")
    for skill_name in sorted(records.keys()):
        rec = records[skill_name]
        table.add_row(
            skill_name,
            str(rec["use_count"]),
            str(rec["view_count"]),
            str(rec["patch_count"]),
            str(rec["last_used_at"] or "-"),
            str(rec["state"]),
            "✓" if rec["pinned"] else "",
        )
    console.print(table)


def _classify_skill(ctx: CommandContext, name: str) -> str:
    """Return one of: 'agent-created', 'bundled', 'unknown'."""
    user_path = ctx.deps.user_skills_dir / name / "SKILL.md"
    bundled_path = ctx.deps.skills_dir / name / "SKILL.md"
    if user_path.exists():
        return "agent-created"
    if bundled_path.exists():
        return "bundled"
    return "unknown"


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
    if classification == "unknown":
        console.print(f"[bold red]Skill '{name}' not found.[/bold red]")
        return

    skill_usage.set_pinned(ctx.deps, name, pinned)
    state = "pinned" if pinned else "unpinned"
    console.print(f"[success]✓ Skill '{name}' {state}.[/success]")
