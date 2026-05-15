"""Slash command handlers for /skills and subcommands."""

from __future__ import annotations

from co_cli.commands.registry import (
    BUILTIN_COMMANDS,
    filter_namespace_conflicts,
    refresh_completer,
)
from co_cli.commands.types import CommandContext
from co_cli.config.core import settings
from co_cli.display.core import console, make_table
from co_cli.skills.lifecycle import discover_skill_files, read_skill_meta
from co_cli.skills.lint import lint_skill
from co_cli.skills.loader import (
    diagnose_requires_failures,
    load_skills,
    scan_skill_content,
)
from co_cli.skills.registry import set_skill_registry


def _cmd_skills_list(ctx: CommandContext) -> None:
    if not ctx.deps.skill_registry:
        console.print("[dim]No skills loaded.[/dim]")
        return
    table = make_table("Name", "Description", "Requires", "User-Invocable")
    for skill in ctx.deps.skill_registry.values():
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
        if name in ctx.deps.skill_registry:
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
    old_names = set(ctx.deps.skill_registry.keys())
    set_skill_registry(new_skills, ctx.deps)
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
    console.print(f"[success]✓ Reloaded {len(ctx.deps.skill_registry)} skill(s)[/success]")


def _cmd_skills_lint(ctx: CommandContext, args: str) -> None:
    """Lint one or all loaded skills against R1-R10."""
    args = args.strip()

    if args == "--all":
        skills_to_lint = list(ctx.deps.skill_registry.keys())
    elif args:
        name = args
        if name not in ctx.deps.skill_registry:
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
        skill = ctx.deps.skill_registry[name]
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
    elif subcmd == "review":
        await _cmd_skills_review(ctx, subargs)
    else:
        console.print(f"[bold red]Unknown /skills subcommand:[/bold red] {subcmd}")
        console.print(
            "[dim]Usage: /skills [list|check|lint [<name>|--all]|reload|review run][/dim]"
        )

    return None


async def _cmd_skills_review(ctx: CommandContext, args: str) -> None:
    """Trigger a session review run manually."""
    sub = args.strip().lower()
    if sub not in ("run", ""):
        console.print("[bold red]Usage:[/bold red] /skills review run")
        return

    if ctx.deps.model is None:
        console.print("[bold red]No model configured — cannot run session review.[/bold red]")
        return

    from co_cli.agents.session_review import run_session_review

    console.print("[dim]Running session review…[/dim]")
    try:
        result = await run_session_review(ctx.deps, ctx.message_history)
        summary = result.summary or "(no changes)"
        console.print(f"[success]✓ Review done:[/success] {summary}")
    except Exception as exc:
        console.print(f"[bold red]Review failed:[/bold red] {exc}")
