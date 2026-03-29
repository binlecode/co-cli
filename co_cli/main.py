import asyncio
import os
import subprocess
import time
import tomllib
from contextlib import AsyncExitStack
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from co_cli.deps import CoDeps
from co_cli.context._orchestrate import run_turn
from co_cli.context._history import precompute_compaction
from co_cli.agent import build_agent
from co_cli.observability._telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR, LOGS_DB
from co_cli.display._core import console, set_theme, PROMPT_CHAR, TerminalFrontend, Frontend
from co_cli.bootstrap._render_status import get_status, render_status_table, check_security, render_security_findings
from co_cli.bootstrap._banner import display_welcome_banner
from co_cli.commands._commands import (
    dispatch as dispatch_command, CommandContext, BUILTIN_COMMANDS,
    LocalOnly, ReplaceTranscript, DelegateToAgent,
    _build_completer_words,
)
from co_cli.context._session import touch_session, increment_compaction, save_session
from co_cli.bootstrap._bootstrap import create_deps, sync_knowledge, restore_session, initialize_session_capabilities

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


def _cleanup_skill_run_state(saved_env: dict[str, str | None], deps: CoDeps) -> None:
    """Restore saved skill-run env vars and clear active skill session state."""
    for k, v in saved_env.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    deps.runtime.active_skill_name = None


async def _finalize_turn(
    turn_result: Any,
    message_history: list,
    session_data: Any,
    deps: CoDeps,
    frontend: Any,
    primary_model: str,
) -> tuple[list, Any, "asyncio.Task | None"]:
    """Consolidate post-turn lifecycle: history, signals, session, compaction, errors.

    Returns (next_message_history, next_session_data, next_bg_compaction_task).
    Does NOT handle skill-run cleanup — that is done by _cleanup_skill_run_state() in finally.
    Does NOT handle /compact or built-in slash-command persistence.
    """
    from co_cli.memory._signal_detector import analyze_for_signals, handle_signal

    next_history = turn_result.messages

    # Signal detection — only on clean (non-interrupted, non-error) turns
    if not turn_result.interrupted and turn_result.outcome != "error":
        signal = await analyze_for_signals(next_history, primary_model, services=deps.services)
        await handle_signal(signal, deps, frontend, primary_model)

    # Clear precomputed result (consumed or stale after this turn)
    deps.runtime.precomputed_compaction = None

    # Touch session and persist
    next_session = touch_session(session_data)
    save_session(deps.config.session_path, next_session)

    # Spawn background compaction for the next turn
    next_task = asyncio.create_task(
        precompute_compaction(next_history, deps, primary_model)
    )

    # Emit error banner when outcome is error
    if turn_result.outcome == "error":
        console.print("[error]An error occurred during this turn.[/error]")

    return next_history, next_session, next_task


def _consume_bg_compaction(
    task: asyncio.Task | None,
    deps: CoDeps,
) -> None:
    """Harvest a completed background compaction task or cancel an unfinished one.

    Contract:
    - Only reads completed tasks (never awaits or blocks).
    - Cancels tasks still running — compaction falls back to inline on the next turn.
    - Writes only deps.runtime.precomputed_compaction (no other state touched).
    """
    if task is None:
        return
    if task.done():
        try:
            deps.runtime.precomputed_compaction = task.result()
        except asyncio.CancelledError:
            # CancelledError is BaseException, not Exception — catch explicitly.
            deps.runtime.precomputed_compaction = None
        except Exception:
            deps.runtime.precomputed_compaction = None
    else:
        task.cancel()
        deps.runtime.precomputed_compaction = None


@dataclass
class _ChatTurnState:
    message_history: list[Any]
    session_data: Any
    next_turn_compaction_task: asyncio.Task | None = None


async def _run_foreground_turn(
    state: "_ChatTurnState",
    *,
    agent: Agent,
    user_input: str,
    saved_env: dict[str, str | None],
    deps: CoDeps,
    frontend: Frontend,
    primary_model: str,
    verbose: bool,
) -> None:
    """Execute one foreground turn: consume bg compaction, run turn, cleanup, finalize.

    _cleanup_skill_run_state is guaranteed via finally. State fields
    (message_history, session_data, next_turn_compaction_task) are updated
    in-place through the shared state object.
    """
    _consume_bg_compaction(state.next_turn_compaction_task, deps)
    try:
        turn_result = await run_turn(
            agent=agent,
            user_input=user_input,
            deps=deps,
            message_history=state.message_history,
            verbose=verbose,
            frontend=frontend,
        )
    finally:
        _cleanup_skill_run_state(saved_env, deps)
    state.message_history, state.session_data, state.next_turn_compaction_task = await _finalize_turn(
        turn_result, state.message_history, state.session_data, deps, frontend, primary_model
    )


async def _chat_loop(verbose: bool = False):
    frontend = TerminalFrontend()

    completer = WordCompleter([f"/{name}" for name in BUILTIN_COMMANDS], sentence=True)
    session = PromptSession(
        history=FileHistory(str(DATA_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=False,
    )
    try:
        deps, startup_statuses = create_deps()
    except ValueError as e:
        console.print(f"[bold red]Startup error:[/bold red] {e}")
        raise SystemExit(1)
    for status in startup_statuses:
        frontend.on_status(status)

    from co_cli._model_factory import ResolvedModel
    from co_cli.config import ROLE_REASONING
    _none_resolved = ResolvedModel(model=None, settings=None)
    resolved = (
        deps.services.model_registry.get(ROLE_REASONING, _none_resolved)
        if deps.services.model_registry else _none_resolved
    )
    primary_model = resolved.model  # used for signal detection and background compaction

    agent_result = build_agent(config=deps.config, resolved=resolved)
    agent = agent_result.agent
    deps.tools.tool_names = agent_result.tool_names
    deps.tools.tool_approvals = agent_result.tool_approvals

    stack = AsyncExitStack()
    state = _ChatTurnState(message_history=[], session_data=None)
    last_interrupt_time = 0.0
    try:
        _mcp_init_ok = False
        try:
            await stack.enter_async_context(agent)
            _mcp_init_ok = True
        except Exception as e:
            console.print(f"[warn]MCP server failed to connect: {e} — running without MCP tools.[/warn]")

        session_cap = await initialize_session_capabilities(agent, deps, frontend, _mcp_init_ok)
        completer.words = _build_completer_words(deps.session.skill_commands)

        sync_knowledge(deps, frontend)
        state.session_data = restore_session(deps, frontend)
        frontend.on_status(f"  {session_cap.skill_count} skill(s) loaded")

        display_welcome_banner(deps, startup_statuses)

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
                        message_history=state.message_history,
                        deps=deps,
                        agent=agent,
                        tool_names=deps.tools.tool_names,
                        completer=completer,
                    )
                    outcome = await dispatch_command(user_input, cmd_ctx)
                    if isinstance(outcome, ReplaceTranscript):
                        state.message_history = outcome.history
                        if outcome.compaction_applied:
                            state.session_data = increment_compaction(state.session_data)
                            save_session(deps.config.session_path, state.session_data)
                        continue
                    elif isinstance(outcome, DelegateToAgent):
                        # Skill dispatched — fall through to LLM turn with delegated input
                        user_input = outcome.delegated_input
                        deps.runtime.active_skill_name = outcome.skill_name
                        _saved_env = {k: os.environ.get(k) for k in outcome.skill_env}
                        os.environ.update(outcome.skill_env)
                    else:  # LocalOnly
                        continue

                await _run_foreground_turn(
                    state,
                    agent=agent,
                    user_input=user_input,
                    saved_env=_saved_env,
                    deps=deps,
                    frontend=frontend,
                    primary_model=primary_model,
                    verbose=verbose,
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
        if state.next_turn_compaction_task is not None:
            state.next_turn_compaction_task.cancel()
        await deps.services.task_runner.shutdown()
        await stack.aclose()
        deps.services.shell.cleanup()


@app.command()
def chat(
    theme: str = typer.Option(None, "--theme", "-t", help="Color theme: dark or light"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream LLM thinking/reasoning tokens"),
):
    """Start an interactive chat session with Co."""
    if theme:
        settings.theme = theme
        set_theme(theme)
    try:
        asyncio.run(_chat_loop(verbose=verbose))
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
