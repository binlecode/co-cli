import asyncio
import os
import subprocess
import time
import tomllib
from contextlib import AsyncExitStack
from pathlib import Path

import typer
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.agent import InstrumentationSettings
from pydantic_ai.messages import ModelMessage

from co_cli.agent import build_agent
from co_cli.bootstrap.banner import display_welcome_banner
from co_cli.bootstrap.core import create_deps, restore_session
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
    REASONING_DISPLAY_FULL,
    USER_DIR,
    VALID_REASONING_DISPLAY_MODES,
    settings,
)
from co_cli.context.orchestrate import TurnResult, run_turn
from co_cli.context.session import increment_compaction, load_session, save_session, touch_session
from co_cli.context.skill_env import cleanup_skill_run_state
from co_cli.context.transcript import append_messages as append_transcript
from co_cli.context.transcript import write_compact_boundary
from co_cli.deps import CoDeps
from co_cli.display._core import PROMPT_CHAR, Frontend, TerminalFrontend, console, set_theme
from co_cli.observability._telemetry import SQLiteSpanExporter

exporter = SQLiteSpanExporter()

_VERSION = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())[
    "project"
]["version"]

resource = Resource.create(
    {
        "service.name": "co-cli",
        "service.version": _VERSION,
    }
)
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(tracer_provider)

# Enable pydantic-ai instrumentation for all agents
# Using version=3 for latest OTel GenAI semantic conventions (spec compliant)
Agent.instrument_all(
    InstrumentationSettings(
        tracer_provider=tracer_provider,
        version=3,
    )
)

app = typer.Typer(
    help="Co — personal AI operator · local-first · approval-first",
    context_settings={"help_option_names": ["--help", "-h"]},
    invoke_without_command=True,
)


@app.callback()
def _default(ctx: typer.Context):
    """Start an interactive chat session (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        chat()


async def _finalize_turn(
    turn_result: TurnResult,
    message_history: list[ModelMessage],
    session_data: dict,
    deps: CoDeps,
    frontend: Frontend,
) -> tuple[list[ModelMessage], dict]:
    """Consolidate post-turn lifecycle: history, signals, session, errors.

    Returns (next_message_history, next_session_data).
    Does NOT handle skill-run cleanup — that is done by cleanup_skill_run_state() in finally.
    Does NOT handle /compact or built-in slash-command persistence.
    """
    from co_cli.memory._extractor import fire_and_forget_extraction

    next_history = turn_result.messages

    # Memory extraction — fire-and-forget on clean (non-interrupted, non-error) turns
    if not turn_result.interrupted and turn_result.outcome != "error":
        fire_and_forget_extraction(next_history, deps=deps, frontend=frontend)

    # Touch session and persist
    next_session = touch_session(session_data)
    save_session(deps.sessions_dir, next_session)

    # Append new messages to transcript (positional tail slice)
    new_messages = turn_result.messages[len(message_history) :]
    append_transcript(deps.sessions_dir, deps.session.session_id, new_messages)

    # Emit error banner when outcome is error
    if turn_result.outcome == "error":
        console.print("[error]An error occurred during this turn.[/error]")

    return next_history, next_session


async def _run_foreground_turn(
    *,
    message_history: list[ModelMessage],
    session_data: dict,
    agent: Agent[CoDeps, str | DeferredToolRequests],
    user_input: str,
    saved_env: dict[str, str | None],
    deps: CoDeps,
    frontend: Frontend,
) -> tuple[list[ModelMessage], dict]:
    """Execute one foreground turn: run turn, cleanup, finalize.

    cleanup_skill_run_state is guaranteed via finally.
    Returns (next_message_history, next_session_data).
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
    return await _finalize_turn(turn_result, message_history, session_data, deps, frontend)


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

        session_data = restore_session(deps, frontend)
        from co_cli.commands._commands import get_skill_registry

        frontend.on_status(f"  {len(get_skill_registry(deps.skill_commands))} skill(s) loaded")

        # Resume hint: check if a transcript exists for the current session
        transcript_path = deps.sessions_dir / f"{deps.session.session_id}.jsonl"
        if transcript_path.exists():
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
                last_interrupt_time = 0.0  # Reset on successful input
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input.strip():
                    continue

                # /command — slash commands, no LLM
                if user_input.startswith("/"):
                    cmd_ctx = CommandContext(
                        message_history=message_history,
                        deps=deps,
                        agent=agent,
                        completer=completer,
                    )
                    outcome = await dispatch_command(user_input, cmd_ctx)
                    if isinstance(outcome, ReplaceTranscript):
                        message_history = outcome.history
                        # Sync session_data if session ID rotated (/new, /resume)
                        if deps.session.session_id != session_data.get("session_id"):
                            rotated = load_session(
                                deps.sessions_dir / f"{deps.session.session_id}.json"
                            )
                            if rotated:
                                session_data = rotated
                        if outcome.compaction_applied:
                            write_compact_boundary(deps.sessions_dir, deps.session.session_id)
                            session_data = increment_compaction(session_data)
                            save_session(deps.sessions_dir, session_data)
                        continue
                    elif isinstance(outcome, DelegateToAgent):
                        # Skill dispatched — fall through to LLM turn with delegated input
                        user_input = outcome.delegated_input
                        deps.runtime.active_skill_name = outcome.skill_name
                        _saved_env = {k: os.environ.get(k) for k in outcome.skill_env}
                        os.environ.update(outcome.skill_env)
                    else:  # LocalOnly
                        continue

                message_history, session_data = await _run_foreground_turn(
                    message_history=message_history,
                    session_data=session_data,
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
        # Drain pending memory extraction before exit
        from co_cli.memory._extractor import drain_pending_extraction

        await drain_pending_extraction()

        if deps is not None:
            from co_cli.tools.background import kill_task

            for task_state in deps.session.background_tasks.values():
                if task_state.status == "running":
                    try:
                        await kill_task(task_state)
                    except Exception:
                        pass
            deps.shell.cleanup()
        await stack.aclose()


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
