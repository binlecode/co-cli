"""Slash command registry, handlers, and dispatch for the REPL."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest

from co_cli.commands._skill_types import SkillConfig
from co_cli.config import ROLE_SUMMARIZATION
from co_cli.display._core import console
from co_cli.knowledge._frontmatter import parse_frontmatter
from co_cli.deps import ApprovalKindEnum, CoDeps, CoCapabilityState

logger = logging.getLogger(__name__)


# -- Types -----------------------------------------------------------------

@dataclass
class CommandContext:
    """Input bag passed to every slash-command handler."""

    message_history: list[Any]
    deps: CoDeps
    agent: Agent
    tool_names: list[str]
    # Holds the live WordCompleter from chat_loop() — typed Any to keep _commands.py
    # free of prompt_toolkit imports (design boundary). None outside REPL context.
    completer: Any = None


@dataclass(frozen=True)
class LocalOnly:
    """Built-in or unknown slash command ran locally; return to prompt."""


@dataclass(frozen=True)
class ReplaceTranscript:
    """Transcript-management command replaced message history."""

    history: list[Any]
    compaction_applied: bool = False


@dataclass(frozen=True)
class DelegateToAgent:
    """Skill command delegated into an agent turn."""

    delegated_input: str
    skill_env: dict[str, str]
    skill_name: str | None


SlashOutcome: TypeAlias = LocalOnly | ReplaceTranscript | DelegateToAgent


@dataclass(frozen=True)
class SlashCommand:
    """A registered slash command."""

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[list[Any] | ReplaceTranscript | None]]


def set_skill_commands(new_skills: dict[str, SkillConfig], capabilities: CoCapabilityState) -> None:
    """Set capabilities.skill_commands and update capabilities.skill_registry."""
    capabilities.skill_commands = new_skills
    capabilities.skill_registry = [
        {"name": s.name, "description": s.description}
        for s in new_skills.values()
        if s.description and not s.disable_model_invocation
    ]
    capabilities.slash_command_count = len([s for s in new_skills.values() if s.user_invocable])


# Env vars that skill-env may never override — security boundary.
_SKILL_ENV_BLOCKED: frozenset[str] = frozenset({
    "PATH", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "HOME", "USER", "SHELL", "SUDO_UID",
})

# Static security patterns for skill content scanning (TASK-4).
_SKILL_SCAN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("credential_exfil", re.compile(
        r'(curl|wget|nc)\s[^\n]*\$\{?[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD|API)[A-Z_]*\}?',
        re.IGNORECASE,
    )),
    ("pipe_to_shell", re.compile(r'(curl|wget)\s[^|\n]+\|\s*(ba)?sh', re.IGNORECASE)),
    ("destructive_shell", re.compile(
        r'rm\s+-rf\s*/|dd\s+if=/dev/(zero|random|urandom)|:\(\)\s*\{',
        re.IGNORECASE,
    )),
    ("prompt_injection", re.compile(
        r'ignore\s+(all\s+)?previous\s+instructions|you\s+are\s+now\s+(a|an)\s',
        re.IGNORECASE,
    )),
]


def _scan_skill_content(content: str) -> list[str]:
    """Scan skill content for security patterns.

    Returns a list of tagged warning strings. Empty list = content is clean.
    Each entry has the form '[tag] line N: <line>'.
    """
    warnings: list[str] = []
    for i, line in enumerate(content.splitlines(), 1):
        for tag, pattern in _SKILL_SCAN_PATTERNS:
            if pattern.search(line):
                warnings.append(f"[{tag}] line {i}: {line}")
    return warnings


def _build_completer_words(skill_commands: dict) -> list[str]:
    """Single source of truth for the REPL tab-completer word list."""
    return [f"/{name}" for name in BUILTIN_COMMANDS] + [
        f"/{name}" for name, s in skill_commands.items() if s.user_invocable
    ]


def _refresh_completer(ctx: CommandContext) -> None:
    """Refresh the REPL completer words after a skill_commands mutation."""
    if ctx.completer is None:
        return
    ctx.completer.words = _build_completer_words(ctx.deps.capabilities.skill_commands)



def _inject_source_url(content: str, url: str) -> str:
    """Inject or update source-url field in skill frontmatter."""
    if not content.startswith("---\n"):
        return f"---\nsource-url: {url}\n---\n{content}"
    rest = content[4:]
    close_match = re.search(r'\n---(\n|$)', rest)
    if close_match is None:
        return f"---\nsource-url: {url}\n---\n{content}"
    close_start = close_match.start()
    fm_block = rest[:close_start]
    after_close = rest[close_match.end():]
    lines = fm_block.splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        if line.startswith("source-url:"):
            new_lines.append(f"source-url: {url}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"source-url: {url}")
    new_fm = "\n".join(new_lines)
    return f"---\n{new_fm}\n---\n{after_close}"


# -- Handlers --------------------------------------------------------------


async def _cmd_help(ctx: CommandContext, args: str) -> None:
    """List available slash commands."""
    from rich.table import Table

    table = Table(title="Slash Commands", border_style="accent", expand=False)
    table.add_column("Command", style="accent")
    table.add_column("Description")
    for cmd in BUILTIN_COMMANDS.values():
        table.add_row(f"/{cmd.name}", cmd.description)
    if ctx.deps.capabilities.skill_commands:
        for skill in ctx.deps.capabilities.skill_commands.values():
            if skill.user_invocable:
                hint = f"  [{skill.argument_hint}]" if skill.argument_hint else ""
                table.add_row(f"/{skill.name}{hint}", skill.description or "(skill)")
    console.print(table)
    console.print("[dim]Usage: /status shows system health; /status <task-id> shows a background task.[/dim]")
    return None


async def _cmd_clear(ctx: CommandContext, args: str) -> list[Any]:
    """Clear conversation history."""
    console.print("[info]Conversation history cleared.[/info]")
    return []


async def _cmd_status(ctx: CommandContext, args: str) -> None:
    """Show system health, or task status when <id> is given."""
    task_id = args.strip()
    if task_id:
        # Route to task lookup
        runner = ctx.deps.services.task_runner
        if runner is None:
            console.print("[bold red]Task runner not available.[/bold red]")
            return None
        meta = runner.get_task(task_id)
        if meta is None:
            console.print(f"[bold red]Task not found:[/bold red] {task_id}")
            return None
        from rich.table import Table
        table = Table(title=f"Task: {task_id}", border_style="accent", expand=False)
        table.add_column("Field", style="accent")
        table.add_column("Value")
        for k, v in meta.items():
            table.add_row(str(k), str(v) if v is not None else "")
        console.print(table)
        # Show last 20 lines of output
        lines = runner._storage.tail_output(task_id, n=20)
        if lines:
            console.print("[dim]--- Output (last 20 lines) ---[/dim]")
            for line in lines:
                console.print(line)
        return None

    from co_cli.bootstrap._render_status import get_status, render_status_table, check_security, render_security_findings

    info = get_status(ctx.deps.config, tool_count=len(ctx.tool_names))
    console.print(render_status_table(info))
    findings = check_security()
    render_security_findings(findings)
    return None


async def _cmd_tools(ctx: CommandContext, args: str) -> None:
    """List registered agent tools."""
    tools = sorted(ctx.tool_names)
    lines = [f"  [accent]{i + 1}.[/accent] {name}" for i, name in enumerate(tools)]
    console.print(f"[info]Registered tools ({len(tools)}):[/info]")
    console.print("\n".join(lines))
    return None


async def _cmd_history(ctx: CommandContext, args: str) -> None:
    """Show conversation turn count."""
    turns = sum(
        1 for msg in ctx.message_history
        if isinstance(msg, ModelRequest)
    )
    console.print(f"[info]Conversation: {turns} user turn(s), {len(ctx.message_history)} total message(s).[/info]")
    return None


async def _cmd_compact(ctx: CommandContext, args: str) -> ReplaceTranscript | None:
    """Summarize conversation via LLM to reduce context."""
    from pydantic_ai.messages import ModelResponse, TextPart as _TextPart, UserPromptPart

    from co_cli.context._history import _run_summarization_with_policy
    from co_cli._model_factory import ResolvedModel

    if not ctx.message_history:
        console.print("[dim]Nothing to compact — history is empty.[/dim]")
        return None

    console.print("[dim]Compacting conversation...[/dim]")
    _none = ResolvedModel(model=None, settings=None)
    resolved = (
        ctx.deps.services.model_registry.get(ROLE_SUMMARIZATION, _none)
        if ctx.deps.services.model_registry else _none
    )
    summary = await _run_summarization_with_policy(
        ctx.message_history, resolved,
        max_retries=ctx.deps.config.model_http_retries,
    )

    if summary is None:
        console.print("[bold red]Compact failed:[/bold red] provider error (see logs)")
        return None

    # Build a minimal 2-message history: summary request + ack response
    new_history: list[Any] = [
        ModelRequest(parts=[
            UserPromptPart(content=f"[Compacted conversation summary]\n{summary}"),
        ]),
        ModelResponse(parts=[
            _TextPart(content="Understood. I have the conversation context."),
        ]),
    ]
    old_len = len(ctx.message_history)
    console.print(
        f"[info]Compacted: {old_len} messages → {len(new_history)} messages.[/info]"
    )
    return ReplaceTranscript(history=new_history, compaction_applied=True)


async def _cmd_forget(ctx: CommandContext, args: str) -> None:
    """Delete a memory by ID."""
    from pathlib import Path

    if not args.strip():
        console.print("[bold red]Usage:[/bold red] /forget <memory_id>")
        console.print("[dim]Example: /forget 5[/dim]")
        return None

    try:
        memory_id = int(args.strip())
    except ValueError:
        console.print(f"[bold red]Invalid memory ID:[/bold red] {args}")
        console.print("[dim]Memory ID must be a number.[/dim]")
        return None

    memory_dir = ctx.deps.config.memory_dir
    if not memory_dir.exists():
        console.print("[dim]No memory directory found.[/dim]")
        return None

    # Find file with this ID
    matching_files = list(memory_dir.glob(f"{memory_id:03d}-*.md"))
    if not matching_files:
        console.print(f"[bold red]Memory {memory_id} not found[/bold red]")
        console.print("[dim]Use /list_memories to see available IDs.[/dim]")
        return None

    # Delete file
    file_to_delete = matching_files[0]
    file_to_delete.unlink()
    if ctx.deps.services.knowledge_index is not None:
        ctx.deps.services.knowledge_index.remove("memory", str(file_to_delete))
    console.print(f"[success]✓ Deleted memory {memory_id}: {file_to_delete.name}[/success]")
    return None


async def _cmd_new(ctx: CommandContext, _args: str) -> list[Any] | None:
    """Checkpoint current session to knowledge and start fresh."""
    from co_cli.context._history import _index_session_summary
    from co_cli.knowledge._frontmatter import ArtifactTypeEnum
    from co_cli.memory._lifecycle import persist_memory as _save_memory_impl
    from co_cli._model_factory import ResolvedModel

    if not ctx.message_history:
        console.print("[dim]Nothing to checkpoint — history is empty.[/dim]")
        return None

    _none = ResolvedModel(model=None, settings=None)
    resolved = (
        ctx.deps.services.model_registry.get(ROLE_SUMMARIZATION, _none)
        if ctx.deps.services.model_registry else _none
    )
    summary = await _index_session_summary(
        ctx.message_history,
        resolved,
        personality_active=bool(ctx.deps.config.personality),
        max_retries=ctx.deps.config.model_http_retries,
    )

    if summary is None:
        console.print("[yellow]Could not summarize session — history not cleared.[/yellow]")
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    await _save_memory_impl(
        ctx.deps,
        content=summary,
        tags=[],
        related=[],
        provenance="session",
        title=f"session-{timestamp}",
        artifact_type=ArtifactTypeEnum.SESSION_SUMMARY,
    )

    console.print(f"[dim]Session checkpointed as session-{timestamp}.md. Starting fresh.[/dim]")
    return []


async def _cmd_skills(ctx: CommandContext, args: str) -> None:
    """List and inspect loaded skills, or install a new one."""
    from rich.table import Table

    sub = args.strip().split(maxsplit=1)
    subcmd = sub[0].lower() if sub else "list"
    subargs = sub[1] if len(sub) > 1 else ""

    if subcmd in ("", "list"):
        if not ctx.deps.capabilities.skill_commands:
            console.print("[dim]No skills loaded.[/dim]")
            return None
        table = Table(title="Loaded Skills", border_style="accent", expand=False)
        table.add_column("Name", style="accent")
        table.add_column("Description")
        table.add_column("Requires")
        table.add_column("User-Invocable")
        for skill in ctx.deps.capabilities.skill_commands.values():
            req_keys = ", ".join(skill.requires.keys()) if skill.requires else ""
            table.add_row(
                skill.name,
                skill.description or "",
                req_keys,
                "✓" if skill.user_invocable else "✗",
            )
        console.print(table)

    elif subcmd == "check":
        from co_cli.config import settings as _settings

        default_dir = Path(__file__).parent.parent / "skills"
        user_dir = ctx.deps.config.user_skills_dir
        project_dir = ctx.deps.config.skills_dir

        all_paths: list[Path] = []
        if default_dir.exists():
            all_paths.extend(sorted(default_dir.glob("*.md")))
        if user_dir.exists():
            all_paths.extend(sorted(user_dir.glob("*.md")))
        if project_dir.exists():
            all_paths.extend(sorted(project_dir.glob("*.md")))

        if not all_paths:
            console.print("[dim]No skill files found.[/dim]")
            return None

        table = Table(title="Skills Check", border_style="accent", expand=False)
        table.add_column("File", style="accent")
        table.add_column("Status")
        table.add_column("Reason")

        for path in all_paths:
            name = path.stem
            if name in ctx.deps.capabilities.skill_commands:
                table.add_row(path.name, "[success]✓ Loaded[/success]", "")
            else:
                try:
                    text = path.read_text(encoding="utf-8")
                    meta, _ = parse_frontmatter(text)
                    requires = meta.get("requires", {}) if isinstance(meta.get("requires"), dict) else {}
                    failures = _diagnose_requires_failures(requires, _settings)
                    reason = "; ".join(failures) if failures else "name conflict with built-in"
                    table.add_row(path.name, "[bold red]✗ Skipped[/bold red]", reason)
                except Exception as e:
                    table.add_row(path.name, "[bold red]✗ Error[/bold red]", str(e))

        console.print(table)

    elif subcmd == "install":
        await _install_skill(ctx, subargs)

    elif subcmd == "reload":
        from co_cli.config import settings as _settings
        # handler (not a tool) — direct settings import acceptable, matches _install_skill pattern
        user_skills_dir = ctx.deps.config.user_skills_dir
        new_skills = _load_skills(ctx.deps.config.skills_dir, _settings, user_skills_dir=user_skills_dir)
        # Scan user-global and project-local files only — bundled skills are version-controlled
        project_dir = ctx.deps.config.skills_dir
        all_paths = (sorted(user_skills_dir.glob("*.md")) if user_skills_dir.exists() else []) + \
                    (sorted(project_dir.glob("*.md")) if project_dir.exists() else [])
        for p in all_paths:
            if p.stem in new_skills:
                try:
                    for w in _scan_skill_content(p.read_text(encoding="utf-8")):
                        console.print(f"[yellow]Security warning in {p.name}: {w}[/yellow]")
                except Exception:
                    pass
        old_names = set(ctx.deps.capabilities.skill_commands.keys())
        set_skill_commands(new_skills, ctx.deps.capabilities)
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
        console.print(f"[success]✓ Reloaded {len(ctx.deps.capabilities.skill_commands)} skill(s)[/success]")

    elif subcmd == "upgrade":
        await _upgrade_skill(ctx, subargs)

    else:
        console.print(f"[bold red]Unknown /skills subcommand:[/bold red] {subcmd}")
        console.print("[dim]Usage: /skills [list|check|install <path|url>|reload|upgrade <name>][/dim]")

    return None


async def _install_skill(ctx: CommandContext, target: str, force: bool = False) -> None:
    """Copy a skill .md file from a local path or URL into skills_dir and reload."""
    from co_cli.config import settings as _settings

    target = target.strip()
    if not target:
        console.print("[bold red]Usage:[/bold red] /skills install <path|url>")
        return

    # Fetch content
    if target.startswith("http://") or target.startswith("https://"):
        try:
            import httpx
            import urllib.parse
            resp = httpx.get(target, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            console.print(f"[bold red]Failed to fetch skill:[/bold red] {e}")
            return
        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith("text/"):
            console.print(f"[bold red]Unexpected content-type (expected text/*):[/bold red] {content_type}")
            return
        content = resp.text
        content = _inject_source_url(content, target)
        filename = Path(urllib.parse.urlparse(target).path).name
    else:
        try:
            p = Path(target)
            content = p.read_text(encoding="utf-8")
            filename = p.name
        except Exception as e:
            console.print(f"[bold red]Failed to read skill:[/bold red] {e}")
            return

    if not filename.endswith(".md"):
        console.print(f"[bold red]Skill file must end with .md:[/bold red] {filename}")
        return

    # Security scan — blocking before install (user must confirm)
    warnings = _scan_skill_content(content)
    if warnings:
        console.print("[bold yellow]Security scan warnings:[/bold yellow]")
        for w in warnings:
            console.print(f"  [yellow]{w}[/yellow]")
        answer = console.input("Install anyway? [y/N] ")
        if answer.strip().lower() != "y":
            console.print("[dim]Install cancelled.[/dim]")
            return

    # Confirm overwrite if file already exists (skip when force=True)
    dest = ctx.deps.config.skills_dir / filename
    if dest.exists() and not force:
        answer = console.input(f"Overwrite existing skill '{filename}'? [y/N] ")
        if answer.strip().lower() != "y":
            console.print("[dim]Install cancelled.[/dim]")
            return

    # Write to skills_dir
    ctx.deps.config.skills_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")

    # Reload in-session: package-default + user-global + updated project dir
    new_skills = _load_skills(ctx.deps.config.skills_dir, _settings, user_skills_dir=ctx.deps.config.user_skills_dir)
    set_skill_commands(new_skills, ctx.deps.capabilities)
    _refresh_completer(ctx)

    console.print(f"[success]✓ Installed skill: {filename.removesuffix('.md')}[/success]")


async def _upgrade_skill(ctx: CommandContext, args: str) -> None:
    """Re-fetch and reinstall a skill that was installed from a URL."""
    name = args.strip()
    if not name:
        console.print("[bold red]Usage:[/bold red] /skills upgrade <name>")
        return
    if name not in ctx.deps.capabilities.skill_commands:
        console.print(f"[bold red]Skill '{name}' not found.[/bold red]")
        return
    skill_file = ctx.deps.config.skills_dir / f"{name}.md"
    if not skill_file.exists():
        console.print(f"[bold red]Skill '{name}' not found in project skills dir.[/bold red]")
        return
    text = skill_file.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    source_url = meta.get("source-url", "").strip() if isinstance(meta, dict) else ""
    if not source_url:
        console.print(f"[bold red]Skill '{name}' has no source-url — not installed from a URL.[/bold red]")
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
    cmd = args.strip()
    if not cmd:
        console.print("[bold red]Usage:[/bold red] /background <command>")
        console.print("[dim]Example: /background uv run pytest[/dim]")
        return None

    runner = ctx.deps.services.task_runner
    if runner is None:
        console.print("[bold red]Task runner not available.[/bold red]")
        return None

    try:
        task_id = await runner.start_task(cmd, str(Path.cwd()))
        console.print(f"[success][{task_id}] started[/success]")
        console.print(f"[dim]Use /status {task_id} to check progress.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to start background task:[/bold red] {e}")
    return None


async def _cmd_tasks(ctx: CommandContext, args: str) -> None:
    """List background tasks. Usage: /tasks [status]"""
    runner = ctx.deps.services.task_runner
    if runner is None:
        console.print("[bold red]Task runner not available.[/bold red]")
        return None

    status_filter = args.strip() or None
    tasks = runner.list_tasks(status_filter)

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
    for t in tasks:
        started = (t.get("started_at") or "queued")[:19]
        table.add_row(t.get("task_id", ""), t.get("status", ""), t.get("command", ""), started)
    console.print(table)
    return None


async def _cmd_cancel(ctx: CommandContext, args: str) -> None:
    """Cancel a running background task. Usage: /cancel <task_id>"""
    task_id = args.strip()
    if not task_id:
        console.print("[bold red]Usage:[/bold red] /cancel <task_id>")
        return None

    runner = ctx.deps.services.task_runner
    if runner is None:
        console.print("[bold red]Task runner not available.[/bold red]")
        return None

    meta = runner.get_task(task_id)
    if meta is None:
        console.print(f"[bold red]Task not found:[/bold red] {task_id}")
        return None

    from co_cli.tools._background import TaskStatusEnum
    if meta.get("status") != TaskStatusEnum.running.value:
        console.print(f"[dim]Task {task_id} is not running (status={meta.get('status')}).[/dim]")
        return None

    cancelled = await runner.cancel_task(task_id)
    if cancelled:
        console.print(f"[success]✓ Cancelled task {task_id}[/success]")
    else:
        console.print(f"[dim]Task {task_id} was not running.[/dim]")
    return None


# -- Skills loader ---------------------------------------------------------


def _diagnose_requires_failures(requires: dict, settings: Any = None) -> list[str]:
    """Evaluate the requires block and return human-readable failure strings.

    Empty list means all requirements are met.
    """
    failures: list[str] = []

    bins = requires.get("bins", [])
    if bins:
        missing = [b for b in bins if not shutil.which(b)]
        if missing:
            failures.append(f"missing bins: {', '.join(missing)}")

    any_bins = requires.get("anyBins", [])
    if any_bins and not any(shutil.which(b) for b in any_bins):
        failures.append(f"none of anyBins found: {', '.join(any_bins)}")

    env_vars = requires.get("env", [])
    if env_vars:
        missing_env = [e for e in env_vars if not os.getenv(e)]
        if missing_env:
            failures.append(f"missing env vars: {', '.join(missing_env)}")

    platforms = requires.get("os", [])
    if platforms and not sys.platform.startswith(tuple(platforms)):
        failures.append(f"os not satisfied: need {platforms}, got {sys.platform}")

    settings_fields = requires.get("settings", [])
    if settings_fields:
        if settings is None:
            failures.append(f"missing settings: {', '.join(settings_fields)}")
        else:
            missing_settings = [f for f in settings_fields if not getattr(settings, f, None)]
            if missing_settings:
                failures.append(f"missing settings: {', '.join(missing_settings)}")

    return failures


def _check_requires(name: str, requires: dict, settings: Any = None) -> bool:
    """Evaluate the requires block. Returns True when all conditions are met."""
    # bins: all listed binaries must exist on PATH
    bins = requires.get("bins", [])
    if bins and not all(shutil.which(b) for b in bins):
        logger.info(f"Skipping skill {name}: requires bins not satisfied: {bins}")
        return False

    # anyBins: at least one binary must exist on PATH (only checked when non-empty)
    any_bins = requires.get("anyBins", [])
    if any_bins and not any(shutil.which(b) for b in any_bins):
        logger.info(f"Skipping skill {name}: requires anyBins not satisfied: {any_bins}")
        return False

    # env: all listed environment variables must be set (non-empty)
    env_vars = requires.get("env", [])
    if env_vars and not all(os.getenv(e) for e in env_vars):
        logger.info(f"Skipping skill {name}: requires env not satisfied: {env_vars}")
        return False

    # os: sys.platform must start with one of the listed platform prefixes
    platforms = requires.get("os", [])
    if platforms and not sys.platform.startswith(tuple(platforms)):
        logger.info(f"Skipping skill {name}: requires os not satisfied: {platforms}")
        return False

    # settings: named Settings fields must be non-None and non-empty.
    # Fail closed: if settings gate is required but settings object is unavailable, skip the skill.
    settings_fields = requires.get("settings", [])
    if settings_fields:
        if settings is None or not all(getattr(settings, f, None) for f in settings_fields):
            logger.info(f"Skipping skill {name}: requires settings not satisfied: {settings_fields}")
            return False

    return True


def _is_safe_skill_path(path: Path, root: Path) -> bool:
    """Return True when path is safe to load (not a symlink pointing outside root)."""
    if not path.is_symlink():
        return True
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _load_skill_file(
    path: Path,
    result: dict[str, SkillConfig],
    reserved: set[str],
    settings: Any = None,
    *,
    root: Path,
    scan: bool = True,
) -> None:
    """Parse a single skill .md file and add to result dict if valid."""
    if not _is_safe_skill_path(path, root):
        logger.warning(f"Skill path containment violation — skipping {path} (expected root: {root})")
        return
    name = path.stem
    try:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)

        if name in reserved:
            logger.warning(f"Skill '{name}' conflicts with built-in command, skipping")
            return

        requires = meta.get("requires", {}) if isinstance(meta.get("requires"), dict) else {}
        if not _check_requires(name, requires, settings):
            return

        # Security scan — warning-only for existing/developer-owned assets
        if scan:
            for w in _scan_skill_content(text):
                logger.warning(f"Security scan warning in {path}: {w}")

        # Extract skill-env with type guard; filter blocked and non-string values
        raw_env = meta.get("skill-env", {})
        skill_env: dict[str, str] = {}
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                if isinstance(k, str) and isinstance(v, str) and k not in _SKILL_ENV_BLOCKED:
                    skill_env[k] = v

        result[name] = SkillConfig(
            name=name,
            description=meta.get("description", ""),
            body=body.strip(),
            argument_hint=meta.get("argument-hint", ""),
            user_invocable=meta.get("user-invocable", True),
            disable_model_invocation=meta.get("disable-model-invocation", False),
            requires=requires,
            skill_env=skill_env,
        )
    except Exception as e:
        logger.warning(f"Failed to load skill {path}: {e}")


def _load_skills(
    skills_dir: Path,
    settings: Any = None,
    *,
    user_skills_dir: Path | None = None,
) -> dict[str, SkillConfig]:
    """Scan skills directories and return a dict of SkillConfig objects.

    Load order (lowest to highest precedence):
      1. Package-default skills (co_cli/skills/) — bundled, not scanned at runtime
      2. User-global skills (user_skills_dir, if provided and exists)
      3. Project-local skills (skills_dir) — highest precedence

    Reserved names are derived from BUILTIN_COMMANDS.keys() at call time so newly
    added built-in commands are automatically protected without touching this
    function.
    """
    result: dict[str, SkillConfig] = {}
    reserved = set(BUILTIN_COMMANDS.keys())

    # Pass 1: Package-default skills (bundled — version-controlled, skip runtime scan)
    default_dir = Path(__file__).parent.parent / "skills"
    if default_dir.exists():
        for path in sorted(default_dir.glob("*.md")):
            _load_skill_file(path, result, reserved, settings, root=default_dir, scan=False)

    # Pass 2: User-global skills (override bundled on name collision)
    if user_skills_dir is not None and user_skills_dir.exists():
        for path in sorted(user_skills_dir.glob("*.md")):
            _load_skill_file(path, result, reserved, settings, root=user_skills_dir)

    # Pass 3: Project-local skills (highest precedence — override everything)
    if skills_dir.exists():
        for path in sorted(skills_dir.glob("*.md")):
            _load_skill_file(path, result, reserved, settings, root=skills_dir)

    return result


# -- Registry --------------------------------------------------------------

BUILTIN_COMMANDS: dict[str, SlashCommand] = {
    "help": SlashCommand("help", "List available slash commands", _cmd_help),
    "clear": SlashCommand("clear", "Clear conversation history", _cmd_clear),
    "new": SlashCommand("new", "Checkpoint session to memory and start fresh", _cmd_new),
    "status": SlashCommand("status", "Show system health or /status <task-id>", _cmd_status),
    "tools": SlashCommand("tools", "List registered agent tools", _cmd_tools),
    "history": SlashCommand("history", "Show conversation turn count", _cmd_history),
    "compact": SlashCommand("compact", "Summarize conversation via LLM to reduce context", _cmd_compact),
    "forget": SlashCommand("forget", "Delete a memory by ID", _cmd_forget),
    "approvals": SlashCommand("approvals", "Manage session approval rules", _cmd_approvals),
    "skills": SlashCommand("skills", "List and inspect loaded skills", _cmd_skills),
    "background": SlashCommand("background", "Run a command in the background", _cmd_background),
    "tasks": SlashCommand("tasks", "List background tasks", _cmd_tasks),
    "cancel": SlashCommand("cancel", "Cancel a running background task", _cmd_cancel),
}


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
    skill = ctx.deps.capabilities.skill_commands.get(name)
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
