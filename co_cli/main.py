import asyncio
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
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent
from pydantic_ai.agent import InstrumentationSettings
from pydantic_ai.messages import ModelMessage

from co_cli.deps import CoDeps
from co_cli.context._orchestrate import run_turn, TurnResult
from co_cli.context._history import HistoryCompactionState
from co_cli.agent import build_agent, build_task_agent
from co_cli.observability._telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR, LOGS_DB, DEFAULT_REASONING_DISPLAY, REASONING_DISPLAY_FULL, VALID_REASONING_DISPLAY_MODES, ROLE_TASK
from co_cli.display._core import console, set_theme, PROMPT_CHAR, TerminalFrontend, Frontend
from co_cli.bootstrap._render_status import get_status, render_status_table, check_security, render_security_findings
from co_cli.bootstrap._banner import display_welcome_banner
from co_cli.commands._commands import (
    dispatch as dispatch_command, CommandContext, BUILTIN_COMMANDS,
    LocalOnly, ReplaceTranscript, DelegateToAgent,
    _build_completer_words,
)
from co_cli.context._session import touch_session, increment_compaction, save_session
from co_cli.context._skill_env import _cleanup_skill_run_state
from co_cli.bootstrap._bootstrap import create_deps, initialize_knowledge, sync_knowledge, restore_session, initialize_session_capabilities

exporter = SQLiteSpanExporter()

_VERSION = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]

resource = Resource.create({
    "service.name": "co-cli",
    "service.version": _VERSION,
})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(tracer_provider)

# Enable pydantic-ai instrumentation for all agents
# Using version=3 for latest OTel GenAI semantic conventions (spec compliant)
Agent.instrument_all(InstrumentationSettings(
    tracer_provider=tracer_provider,
    version=3,
))

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
    compactor: "HistoryCompactionState",
) -> tuple[list[ModelMessage], dict]:
    """Consolidate post-turn lifecycle: history, signals, session, compaction, errors.

    Returns (next_message_history, next_session_data).
    Does NOT handle skill-run cleanup — that is done by _cleanup_skill_run_state() in finally.
    Does NOT handle /compact or built-in slash-command persistence.
    """
    from co_cli.memory._signal_detector import analyze_for_signals, handle_signal

    next_history = turn_result.messages

    # Signal detection — only on clean (non-interrupted, non-error) turns
    if not turn_result.interrupted and turn_result.outcome != "error":
        signal = await analyze_for_signals(next_history, services=deps.services)
        await handle_signal(signal, deps, frontend)

    # Touch session and persist
    next_session = touch_session(session_data)
    save_session(deps.config.session_path, next_session)

    # Spawn background compaction for the next turn
    compactor.on_turn_end(next_history, deps)

    # Emit error banner when outcome is error
    if turn_result.outcome == "error":
        console.print("[error]An error occurred during this turn.[/error]")

    return next_history, next_session


async def _run_foreground_turn(
    *,
    message_history: list[ModelMessage],
    session_data: dict,
    compactor: "HistoryCompactionState",
    agent: Agent,
    user_input: str,
    saved_env: dict[str, str | None],
    deps: CoDeps,
    frontend: Frontend,
    reasoning_display: str,
) -> tuple[list[ModelMessage], dict]:
    """Execute one foreground turn: harvest bg compaction, run turn, cleanup, finalize.

    _cleanup_skill_run_state is guaranteed via finally.
    Returns (next_message_history, next_session_data).
    """
    compactor.on_turn_start(deps)
    try:
        turn_result = await run_turn(
            agent=agent,
            user_input=user_input,
            deps=deps,
            message_history=message_history,
            reasoning_display=reasoning_display,
            frontend=frontend,
        )
    finally:
        _cleanup_skill_run_state(saved_env, deps)
    return await _finalize_turn(
        turn_result, message_history, session_data, deps, frontend, compactor
    )


async def _chat_loop(reasoning_display: str = DEFAULT_REASONING_DISPLAY):
    frontend = TerminalFrontend()

    completer = WordCompleter([f"/{name}" for name in BUILTIN_COMMANDS], sentence=True)
    session = PromptSession(
        history=FileHistory(str(DATA_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=False,
    )
    try:
        deps = create_deps()
    except ValueError as e:
        console.print(f"[bold red]Startup error:[/bold red] {e}")
        raise SystemExit(1)

    # Build model registry (pure config — no IO)
    from co_cli._model_factory import ModelRegistry, ResolvedModel
    deps.services.model_registry = ModelRegistry.from_config(deps.config)

    agent_result = build_agent(config=deps.config, model_registry=deps.services.model_registry)
    agent = agent_result.agent
    deps.services.tool_index = agent_result.tool_index

    _none_resolved = ResolvedModel(model=None, settings=None)
    if deps.services.model_registry:
        task_resolved = deps.services.model_registry.get(ROLE_TASK, _none_resolved)
        if task_resolved.model:
            deps.services.task_agent = build_task_agent(config=deps.config, resolved=task_resolved).agent

    stack = AsyncExitStack()
    message_history: list[ModelMessage] = []
    session_data: dict | None = None
    compactor = HistoryCompactionState()
    last_interrupt_time = 0.0
    try:
        _mcp_init_ok = False
        try:
            await stack.enter_async_context(agent)
            _mcp_init_ok = True
        except Exception as e:
            console.print(f"[warn]MCP server failed to connect: {e} — running without MCP tools.[/warn]")

        session_cap = await initialize_session_capabilities(agent, deps, frontend, _mcp_init_ok)
        completer.words = _build_completer_words(deps.services.skill_commands)

        initialize_knowledge(deps, frontend)
        sync_knowledge(deps, frontend)
        session_data = restore_session(deps, frontend)
        frontend.on_status(f"  {session_cap.skill_count} skill(s) loaded")

        display_welcome_banner(deps)

        while True:
            _saved_env: dict[str, str | None] = {}
            try:
                user_input = await session.prompt_async(f"Co {PROMPT_CHAR} ")
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
                        if outcome.compaction_applied:
                            session_data = increment_compaction(session_data)
                            save_session(deps.config.session_path, session_data)
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
                    compactor=compactor,
                    agent=agent,
                    user_input=user_input,
                    saved_env=_saved_env,
                    deps=deps,
                    frontend=frontend,
                    reasoning_display=reasoning_display,
                )

            except EOFError:
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
                now = time.monotonic()
                if now - last_interrupt_time <= 2.0:
                    break
                last_interrupt_time = now
                console.print("\n[dim]Press Ctrl+C again to exit[/dim]")
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
    finally:
        compactor.shutdown()
        from co_cli.tools._background import kill_task
        for task_state in deps.session.background_tasks.values():
            if task_state.status == "running":
                try:
                    await kill_task(task_state)
                except Exception:
                    pass
        await stack.aclose()
        deps.services.shell.cleanup()


@app.command()
def chat(
    theme: str = typer.Option(None, "--theme", "-t", help="Color theme: dark or light"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Alias for --reasoning-display full"),
    reasoning_display: str = typer.Option(None, "--reasoning-display", help="Reasoning display mode: off, summary, full"),
):
    """Start an interactive chat session with Co."""
    if theme:
        settings.theme = theme
        set_theme(theme)
    # Resolve effective mode: explicit flag > --verbose alias > persistent config default
    if reasoning_display is not None:
        if reasoning_display not in VALID_REASONING_DISPLAY_MODES:
            console.print(f"[bold red]Error:[/bold red] --reasoning-display must be one of: {', '.join(sorted(VALID_REASONING_DISPLAY_MODES))}")
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
    from pathlib import Path
    from co_cli.deps import CoConfig
    sys_status = get_status(CoConfig.from_settings(settings, cwd=Path.cwd()))
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
    console.print(f"[bold green]Opening Datasette dashboard...[/bold green]")
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
    models_only: bool = typer.Option(False, "--models-only", "-m", help="Only show model/chat spans"),
    poll: float = typer.Option(1.0, "--poll", "-p", help="Poll interval in seconds"),
    no_follow: bool = typer.Option(False, "--no-follow", "-n", help="Print recent spans and exit"),
    last: int = typer.Option(20, "--last", "-l", help="Number of recent spans to show on startup"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show LLM input/output content for model spans"),
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
