import asyncio
import logging
import os
import subprocess
import time
import tomllib
from contextlib import AsyncExitStack
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.agent import InstrumentationSettings
from pydantic_ai.messages import ModelMessage

from co_cli.agent._core import build_agent
from co_cli.bootstrap.banner import display_welcome_banner
from co_cli.bootstrap.core import _init_memory_index, create_deps, restore_session
from co_cli.bootstrap.render_status import (
    check_security,
    get_status,
    render_security_findings,
    render_status_table,
)
from co_cli.commands._commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    DelegateToAgent,
    ReplaceTranscript,
    _build_completer_words,
)
from co_cli.commands._commands import (
    dispatch as dispatch_command,
)
from co_cli.config._core import (
    DEFAULT_REASONING_DISPLAY,
    LOGS_DB,
    LOGS_DIR,
    REASONING_DISPLAY_FULL,
    USER_DIR,
    VALID_REASONING_DISPLAY_MODES,
    settings,
)
from co_cli.context.orchestrate import TurnResult, run_turn
from co_cli.context.skill_env import cleanup_skill_run_state
from co_cli.context.transcript import persist_session_history
from co_cli.deps import CoDeps
from co_cli.display._core import PROMPT_CHAR, Frontend, TerminalFrontend, console, set_theme
from co_cli.observability._file_logging import setup_file_logging
from co_cli.observability._telemetry import setup_tracer_provider

_VERSION = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())[
    "project"
]["version"]

# Python logging + OTel spans → co-cli.jsonl (single rotating JSONL file)
setup_file_logging(
    log_dir=LOGS_DIR,
    level=settings.observability.log_level,
    max_size_mb=settings.observability.log_max_size_mb,
    backup_count=settings.observability.log_backup_count,
)

# OTel spans → co-cli-logs.db (SQLite) + co-cli.jsonl (via JsonSpanExporter propagation)
_tracer_provider = setup_tracer_provider(
    service_name="co-cli",
    service_version=_VERSION,
    redact_patterns=settings.observability.redact_patterns,
)

# Enable pydantic-ai instrumentation for all agents
# Using version=3 for latest OTel GenAI semantic conventions (spec compliant)
Agent.instrument_all(InstrumentationSettings(tracer_provider=_tracer_provider, version=3))

# Suppress noisy third-party loggers to WARNING; co_cli.* loggers are not affected
_SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]
for _logger_name in _SUPPRESS_LOGGERS:
    logging.getLogger(_logger_name).setLevel(logging.WARNING)

app = typer.Typer(
    help="Co — personal AI operator · local-first · approval-first",
    context_settings={"help_option_names": ["--help", "-h"]},
    invoke_without_command=True,
)


@app.callback()
def _default(ctx: typer.Context):
    """Start an interactive chat session (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        _start_chat(theme=None, verbose=False, reasoning_display=None)


async def _finalize_turn(
    turn_result: TurnResult,
    message_history: list[ModelMessage],
    deps: CoDeps,
    frontend: Frontend,
) -> list[ModelMessage]:
    """Consolidate post-turn lifecycle: history, signals, transcript, errors.

    Returns next_message_history.
    Does NOT handle skill-run cleanup — that is done by cleanup_skill_run_state() in finally.
    Does NOT handle /compact or built-in slash-command persistence.
    """
    from co_cli.knowledge._extractor import fire_and_forget_extraction

    next_history = turn_result.messages

    # Memory extraction — cadence-gated, fire-and-forget on clean turns
    if not turn_result.interrupted and turn_result.outcome != "error":
        if deps.runtime.history_compaction_applied:
            # Summarizer already processed the compacted content.
            # Reset cursor to end of compacted history — skip extraction this turn.
            deps.session.last_extracted_message_idx = len(next_history)
        else:
            n = deps.config.memory.extract_every_n_turns
            if n > 0:
                deps.session.last_extracted_turn_idx += 1
                if deps.session.last_extracted_turn_idx % n == 0:
                    cursor = deps.session.last_extracted_message_idx
                    delta = (
                        next_history[cursor:]
                        if 0 <= cursor <= len(next_history)
                        else next_history[-20:]
                    )
                    fire_and_forget_extraction(
                        delta, deps=deps, frontend=frontend, cursor_start=cursor
                    )

    try:
        deps.session.session_path = persist_session_history(
            session_path=deps.session.session_path,
            sessions_dir=deps.sessions_dir,
            messages=turn_result.messages,
            persisted_message_count=deps.session.persisted_message_count,
            history_compacted=deps.runtime.history_compaction_applied,
        )
    except OSError as e:
        frontend.on_status(
            f"Session write failed — conversation may not be saved. Check disk space. ({e})"
        )
    deps.session.persisted_message_count = len(turn_result.messages)

    # Emit error banner when outcome is error
    if turn_result.outcome == "error":
        console.print("[error]An error occurred during this turn.[/error]")

    return next_history


async def _run_foreground_turn(
    *,
    message_history: list[ModelMessage],
    agent: Agent[CoDeps, str | DeferredToolRequests],
    user_input: str,
    saved_env: dict[str, str | None],
    deps: CoDeps,
    frontend: Frontend,
) -> list[ModelMessage]:
    """Execute one foreground turn: run turn, cleanup, finalize.

    cleanup_skill_run_state is guaranteed via finally.
    Returns next_message_history.
    """
    try:
        turn_result = await run_turn(
            agent=agent,
            user_input=user_input,
            deps=deps,
            message_history=message_history,
            frontend=frontend,
        )
    finally:
        cleanup_skill_run_state(saved_env, deps)
    return await _finalize_turn(turn_result, message_history, deps, frontend)


async def _drain_and_cleanup(deps: CoDeps | None, stack: AsyncExitStack) -> None:
    """Drain pending extractions, run the dream cycle if enabled, release resources."""
    from co_cli.knowledge._extractor import drain_pending_extraction

    await drain_pending_extraction()
    if deps is not None:
        await _maybe_run_dream_cycle(deps)

        from co_cli.tools.background import kill_task

        for task_state in deps.session.background_tasks.values():
            if task_state.status == "running":
                try:
                    await kill_task(task_state)
                except Exception:
                    pass
        deps.shell.cleanup()
    await stack.aclose()


async def _maybe_run_dream_cycle(deps: CoDeps) -> None:
    """Run the dream cycle on session end when enabled via knowledge config.

    Bounded by a 60-second timeout. Errors are logged and never propagated —
    session shutdown must not fail because consolidation hit a snag.
    """
    knowledge_config = deps.config.knowledge
    if not knowledge_config.consolidation_enabled:
        return
    if knowledge_config.consolidation_trigger != "session_end":
        return

    from co_cli.knowledge._dream import run_dream_cycle

    logger = logging.getLogger(__name__)
    try:
        async with asyncio.timeout(60):
            result = await run_dream_cycle(deps)
        if result.any_changes:
            logger.info(
                "Dream cycle: %d extracted, %d merged, %d archived",
                result.extracted,
                result.merged,
                result.decayed,
            )
    except TimeoutError:
        logger.warning("Dream cycle timed out after 60s")
    except Exception:
        logger.warning("Dream cycle failed", exc_info=True)


def _apply_command_outcome(
    outcome: object,
    message_history: list[ModelMessage],
    deps: CoDeps,
) -> tuple[bool, list[ModelMessage], str, dict[str, str | None]]:
    """Map a command outcome to loop control signals.

    Returns (should_continue, new_history, new_input, saved_env).
    should_continue=True means `continue` the while loop; False means proceed to agent turn.
    """
    if isinstance(outcome, ReplaceTranscript):
        if outcome.compaction_applied:
            deps.session.session_path = persist_session_history(
                session_path=deps.session.session_path,
                sessions_dir=deps.sessions_dir,
                messages=outcome.history,
                persisted_message_count=deps.session.persisted_message_count,
                history_compacted=True,
            )
            deps.session.persisted_message_count = len(outcome.history)
        else:
            deps.session.persisted_message_count = len(outcome.history)
        return True, outcome.history, "", {}
    if isinstance(outcome, DelegateToAgent):
        saved_env: dict[str, str | None] = {k: os.environ.get(k) for k in outcome.skill_env}
        os.environ.update(outcome.skill_env)
        deps.runtime.active_skill_name = outcome.skill_name
        return False, message_history, outcome.delegated_input, saved_env
    # LocalOnly
    return True, message_history, "", {}


async def _chat_loop(reasoning_display: str = DEFAULT_REASONING_DISPLAY):
    frontend = TerminalFrontend()

    completer = WordCompleter([f"/{name}" for name in BUILTIN_COMMANDS], sentence=True)
    session = PromptSession(
        history=FileHistory(str(USER_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=False,
    )
    stack = AsyncExitStack()
    deps: CoDeps | None = None
    try:
        try:
            deps = await create_deps(frontend, stack)
        except ValueError as e:
            console.print(f"[bold red]Startup error:[/bold red] {e}")
            raise SystemExit(1) from e
        deps.session.reasoning_display = reasoning_display

        completer.words = _build_completer_words(deps.skill_commands)
        agent = build_agent(config=deps.config, model=deps.model, tool_registry=deps.tool_registry)

        current_session_path = restore_session(deps, frontend)
        _init_memory_index(deps, current_session_path, frontend)
        from co_cli.commands._commands import get_skill_registry

        frontend.on_status(f"  {len(get_skill_registry(deps.skill_commands))} skill(s) loaded")

        if deps.session.session_path.exists():
            console.print("[dim]Previous session available — /resume to continue[/dim]")

        display_welcome_banner(deps)
        frontend.clear_status()

        message_history: list[ModelMessage] = []
        last_interrupt_time = 0.0

        while True:
            _saved_env: dict[str, str | None] = {}
            try:
                frontend.set_input_active(True)
                user_input = await session.prompt_async(f"Co {PROMPT_CHAR} ")
                frontend.set_input_active(False)
                last_interrupt_time = 0.0
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input.strip():
                    continue

                if user_input.startswith("/"):
                    cmd_ctx = CommandContext(
                        message_history=message_history,
                        deps=deps,
                        agent=agent,
                        frontend=frontend,
                        completer=completer,
                    )
                    outcome = await dispatch_command(user_input, cmd_ctx)
                    should_continue, message_history, user_input, _saved_env = (
                        _apply_command_outcome(outcome, message_history, deps)
                    )
                    if should_continue:
                        continue

                message_history = await _run_foreground_turn(
                    message_history=message_history,
                    agent=agent,
                    user_input=user_input,
                    saved_env=_saved_env,
                    deps=deps,
                    frontend=frontend,
                )

            except EOFError:
                frontend.set_input_active(False)
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
                frontend.set_input_active(False)
                now = time.monotonic()
                if now - last_interrupt_time <= 2.0:
                    break
                last_interrupt_time = now
                console.print("\n[dim]Press Ctrl+C again to exit[/dim]")
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
    finally:
        await _drain_and_cleanup(deps, stack)


def _start_chat(theme: str | None, verbose: bool, reasoning_display: str | None) -> None:
    """Resolve startup options and enter the interactive chat loop."""
    if theme:
        settings.theme = theme
        set_theme(theme)
    # Resolve effective mode: explicit flag > --verbose alias > persistent config default
    if reasoning_display is not None:
        if reasoning_display not in VALID_REASONING_DISPLAY_MODES:
            console.print(
                f"[bold red]Error:[/bold red] --reasoning-display must be one of: {', '.join(sorted(VALID_REASONING_DISPLAY_MODES))}"
            )
            raise SystemExit(1)
        effective_mode = reasoning_display
    elif verbose:
        effective_mode = REASONING_DISPLAY_FULL
    else:
        effective_mode = settings.reasoning_display
    try:
        asyncio.run(_chat_loop(reasoning_display=effective_mode))
    except KeyboardInterrupt:
        pass  # Safety net: asyncio.run() may re-raise after task cancellation


@app.command()
def chat(
    theme: str = typer.Option(None, "--theme", "-t", help="Color theme: dark or light"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Alias for --reasoning-display full"
    ),
    reasoning_display: str = typer.Option(
        None, "--reasoning-display", help="Reasoning display mode: off, summary, full"
    ),
):
    """Start an interactive chat session with Co."""
    _start_chat(theme=theme, verbose=verbose, reasoning_display=reasoning_display)


@app.command()
def config():
    """Show system configuration and integration health (pre-agent check)."""
    sys_status = get_status(settings)
    console.print(render_status_table(sys_status))
    findings = check_security()
    render_security_findings(findings)


@app.command()
def logs():
    """Launch a local dashboard (Datasette) to inspect agent traces."""
    import webbrowser

    db_path = LOGS_DB
    if not db_path.exists():
        console.print("[yellow]No logs found yet.[/yellow]")
        return

    # Metadata file for better display
    metadata_path = Path(__file__).parent / "datasette_metadata.json"

    url = "http://127.0.0.1:8001"
    console.print("[bold green]Opening Datasette dashboard...[/bold green]")
    console.print(f"[cyan]URL: {url}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    # Auto-open browser after a short delay
    import threading

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    cmd = ["datasette", str(db_path), "--port", "8001"]
    if metadata_path.exists():
        cmd.extend(["--metadata", str(metadata_path)])

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


@app.command()
def traces():
    """Open a visual trace viewer with nested spans (like Logfire)."""
    import webbrowser

    from co_cli.observability._viewer import write_trace_html

    db_path = LOGS_DB
    if not db_path.exists():
        console.print("[yellow]No traces found yet. Run 'co chat' first.[/yellow]")
        return

    html_path = write_trace_html()
    console.print(f"[bold green]Generated trace viewer:[/bold green] {html_path}")
    webbrowser.open(f"file://{html_path}")


@app.command()
def tail(
    trace_id: str = typer.Option(None, "--trace", "-i", help="Filter to a specific trace ID"),
    tools_only: bool = typer.Option(False, "--tools-only", "-T", help="Only show tool spans"),
    models_only: bool = typer.Option(
        False, "--models-only", "-m", help="Only show model/chat spans"
    ),
    poll: float = typer.Option(1.0, "--poll", "-p", help="Poll interval in seconds"),
    no_follow: bool = typer.Option(False, "--no-follow", "-n", help="Print recent spans and exit"),
    last: int = typer.Option(20, "--last", "-l", help="Number of recent spans to show on startup"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show LLM input/output content for model spans"
    ),
):
    """Tail agent spans in real time (like tail -f for OTel traces)."""
    from co_cli.observability._tail import run_tail

    run_tail(
        trace_id=trace_id,
        tools_only=tools_only,
        models_only=models_only,
        poll_interval=poll,
        no_follow=no_follow,
        last=last,
        verbose=verbose,
    )


if __name__ == "__main__":
    app()
