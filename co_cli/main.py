import asyncio
import contextlib
import logging
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.agent import InstrumentationSettings
from pydantic_ai.messages import ModelMessage

from co_cli.agents.core import build_agent
from co_cli.bootstrap.banner import display_welcome_banner
from co_cli.bootstrap.core import create_deps, init_session_index, restore_session
from co_cli.bootstrap.project_info import project_info
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.commands.core import dispatch as dispatch_command
from co_cli.commands.registry import build_completer_entries
from co_cli.commands.types import CommandContext, DelegateToAgent, ReplaceTranscript
from co_cli.config.core import (
    DEFAULT_REASONING_DISPLAY,
    LOGS_DB,
    LOGS_DIR,
    REASONING_DISPLAY_FULL,
    USER_DIR,
    VALID_REASONING_DISPLAY_MODES,
    settings,
)
from co_cli.context.orchestrate import TurnResult, run_turn
from co_cli.deps import CoDeps
from co_cli.display.core import PROMPT_CHAR, Frontend, TerminalFrontend, console, set_theme
from co_cli.memory.transcript import persist_session_history
from co_cli.observability.file_logging import setup_file_logging
from co_cli.observability.telemetry import setup_tracer_provider
from co_cli.skills.lifecycle import cleanup_skill_run_state
from co_cli.tools.tool_io import sweep_tool_result_orphans

_VERSION = project_info().version
_SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]


def _setup_observability() -> None:
    setup_file_logging(
        log_dir=LOGS_DIR,
        level=settings.observability.log_level,
        max_size_mb=settings.observability.log_max_size_mb,
        backup_count=settings.observability.log_backup_count,
    )
    tracer_provider = setup_tracer_provider(
        service_name="co-cli",
        service_version=_VERSION,
        redact_patterns=settings.observability.redact_patterns,
    )
    Agent.instrument_all(InstrumentationSettings(tracer_provider=tracer_provider, version=3))
    for logger_name in _SUPPRESS_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


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
    next_history = turn_result.messages

    try:
        deps.session.session_path = persist_session_history(
            session_path=deps.session.session_path,
            messages=turn_result.messages,
            persisted_message_count=deps.runtime.persisted_message_count,
            history_compacted=deps.runtime.compaction_applied_this_turn,
        )
        deps.runtime.persisted_message_count = len(turn_result.messages)
    except OSError as e:
        frontend.on_status(
            f"Session write failed — conversation may not be saved. Check disk space. ({e})"
        )

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
    next_history = await _finalize_turn(turn_result, message_history, deps, frontend)
    _post_turn_hook(deps, next_history, turn_result.tool_iterations)
    return next_history


async def _drain_and_cleanup(
    deps: CoDeps | None,
    stack: AsyncExitStack,
) -> None:
    """Cancel pending review, run dream cycle, release resources.

    Plan 3.5c: turn-boundary firing is the only review path; no inline
    session-end review fires here. A still-pending background review is
    cancelled and bounded-drained for ≤2s. Atomic-write atomicity makes
    the cancel safe regardless of drain outcome.
    """
    if deps is not None:
        review_task = deps.session.background_review_task
        if review_task is not None and not review_task.done():
            review_task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait([review_task], timeout=2.0)

        await _maybe_run_dream_cycle(deps)

        from co_cli.tools.background import kill_task

        for task_state in deps.session.background_tasks.values():
            if task_state.status == "running":
                try:
                    await kill_task(task_state)
                except Exception:
                    pass
            if task_state.log_path is not None:
                task_state.log_path.unlink(missing_ok=True)
        deps.shell.cleanup()
        if deps.memory_store is not None:
            deps.memory_store.close()
    await stack.aclose()


def _sweep_tool_results(deps: CoDeps) -> None:
    swept = sweep_tool_result_orphans(deps.tool_results_dir)
    if swept:
        logging.getLogger(__name__).debug("Swept %d stale tool-result tmp file(s)", swept)


async def _maybe_run_session_review(deps: CoDeps, message_history: list[ModelMessage]) -> None:
    """Run the combined skill+knowledge session review when enabled.

    Used both as an awaited inline call (legacy) and as the body of a background
    asyncio.Task spawned by _post_turn_hook. Yields at entry so spawning code
    returns to the REPL before any sync setup (fork, build_agent, serialize).
    """
    await asyncio.sleep(0)
    if not deps.config.skills.review_enabled:
        return
    if not deps.model:
        return

    from co_cli.agents.session_review import run_session_review
    from co_cli.config.skills import REVIEW_TIMEOUT_SECONDS

    logger = logging.getLogger(__name__)
    try:
        result = await asyncio.wait_for(
            run_session_review(deps, message_history),
            timeout=REVIEW_TIMEOUT_SECONDS,
        )
        cb = deps.runtime.background_status_callback
        if result.summary and cb is not None:
            cb(f"\U0001f4be {result.summary}")
    except TimeoutError:
        logger.warning("Session review timed out")
    except asyncio.CancelledError:
        logger.info("Session review cancelled")
        raise
    except Exception:
        logger.warning("Session review failed", exc_info=True)

    await _maybe_run_curator(deps)


def _curator_gate_passes(curator_state: dict, interval_hours: int, now: datetime) -> bool:
    """Curator runs if it has never run, or interval_hours elapsed since last_run_at."""
    last_run_str = curator_state.get("last_run_at")
    if last_run_str is None:
        return True
    if curator_state.get("paused"):
        return False
    from co_cli.skills.curator import _parse_iso

    try:
        last_run = _parse_iso(last_run_str)
    except (ValueError, TypeError):
        return True
    return (now - last_run) > timedelta(hours=interval_hours)


async def _maybe_run_curator(deps: CoDeps) -> None:
    """Run the skill curator second pass if enabled and time-gate passed."""
    if not deps.config.skills.curator_enabled:
        return
    if not deps.model:
        return

    from co_cli.agents.skill_curator import run_curator
    from co_cli.config.skills import CURATOR_TIMEOUT_SECONDS
    from co_cli.skills.curator import read_curator_state

    logger = logging.getLogger(__name__)
    now = datetime.now(UTC)
    state = read_curator_state(deps)
    if not _curator_gate_passes(state, deps.config.skills.curator_interval_hours, now):
        return
    try:
        await asyncio.wait_for(run_curator(deps), timeout=CURATOR_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("Curator pass timed out")
    except asyncio.CancelledError:
        logger.info("Curator pass cancelled")
        raise
    except Exception:
        logger.warning("Curator pass failed", exc_info=True)


def _post_turn_hook(
    deps: CoDeps | None,
    message_history: list[ModelMessage],
    turn_iteration_count: int,
) -> None:
    """Bump the iteration counter; spawn background review when threshold tripped.

    Constant-time: counter check + threshold compare + at-most-one create_task.
    Single in-flight on deps.session.background_review_task — on skip, the
    counter is not reset so the next eligible turn re-fires once the
    in-flight task completes.
    """
    if deps is None:
        return
    settings = deps.config.skills
    if not settings.review_enabled:
        return
    if deps.model is None:
        return

    deps.session.iterations_since_review += turn_iteration_count
    if deps.session.iterations_since_review < settings.review_nudge_interval:
        return

    task = deps.session.background_review_task
    if task is not None and not task.done():
        return

    deps.session.iterations_since_review = 0
    deps.session.background_review_task = asyncio.create_task(
        _maybe_run_session_review(deps, list(message_history))
    )


async def _maybe_run_dream_cycle(deps: CoDeps) -> None:
    """Run the dream cycle on session end when enabled via knowledge config.

    Errors are logged and never propagated — session shutdown must not fail
    because consolidation hit a snag.
    """
    knowledge_config = deps.config.knowledge
    if not knowledge_config.consolidation_enabled:
        return
    if knowledge_config.consolidation_trigger != "session_end":
        return

    from co_cli.memory.dream import run_dream_cycle
    from co_cli.tools.memory.manage import knowledge_manage

    logger = logging.getLogger(__name__)
    try:
        result = await run_dream_cycle(deps, knowledge_manage)
        if result.any_changes:
            logger.info(
                "Dream cycle: %d extracted, %d merged, %d archived",
                result.extracted,
                result.merged,
                result.decayed,
            )
    except Exception:
        logger.warning("Dream cycle failed", exc_info=True)


def _apply_command_outcome(
    outcome: object,
    message_history: list[ModelMessage],
    deps: CoDeps,
    frontend: Frontend,
) -> tuple[bool, list[ModelMessage], str, dict[str, str | None]]:
    """Map a command outcome to loop control signals.

    Returns (should_continue, new_history, new_input, saved_env).
    should_continue=True means `continue` the while loop; False means proceed to agent turn.
    """
    if isinstance(outcome, ReplaceTranscript):
        if outcome.compaction_applied:
            try:
                deps.session.session_path = persist_session_history(
                    session_path=deps.session.session_path,
                    messages=outcome.history,
                    persisted_message_count=deps.runtime.persisted_message_count,
                    history_compacted=True,
                )
                deps.runtime.persisted_message_count = len(outcome.history)
            except OSError as e:
                frontend.on_status(
                    f"Session write failed — conversation may not be saved. Check disk space. ({e})"
                )
        else:
            deps.runtime.persisted_message_count = len(outcome.history)
        return True, outcome.history, "", {}
    if isinstance(outcome, DelegateToAgent):
        saved_env: dict[str, str | None] = {k: os.environ.get(k) for k in outcome.skill_env}
        os.environ.update(outcome.skill_env)
        deps.runtime.active_skill_name = outcome.skill_name
        return False, message_history, outcome.delegated_input, saved_env
    # LocalOnly
    return True, message_history, "", {}


@dataclass
class _IterationState:
    message_history: list[ModelMessage]
    last_interrupt_time: float
    should_exit: bool = False


async def _handle_one_input(
    user_input: str | None,
    eof: bool,
    state: _IterationState,
    deps: CoDeps,
    agent: Agent,
    frontend: Frontend,
    completer: SlashCommandCompleter,
    now: float,
) -> _IterationState:
    """Process one iteration of the chat loop given pre-parsed input signals.

    user_input=None signals KeyboardInterrupt; eof=True signals EOFError.
    now is an injected clock value for deterministic double-press tests.
    Returns a new _IterationState.
    """
    if eof:
        return _IterationState(
            message_history=state.message_history,
            last_interrupt_time=state.last_interrupt_time,
            should_exit=True,
        )

    if user_input is None:
        # KeyboardInterrupt signal
        if now - state.last_interrupt_time <= 2.0:
            return _IterationState(
                message_history=state.message_history,
                last_interrupt_time=state.last_interrupt_time,
                should_exit=True,
            )
        console.print("\n[dim]Press Ctrl+C again to exit[/dim]")
        return _IterationState(
            message_history=state.message_history,
            last_interrupt_time=now,
            should_exit=False,
        )

    if user_input.lower() in ("exit", "quit"):
        return _IterationState(
            message_history=state.message_history,
            last_interrupt_time=0.0,
            should_exit=True,
        )

    if not user_input.strip():
        return _IterationState(
            message_history=state.message_history,
            last_interrupt_time=state.last_interrupt_time,
            should_exit=False,
        )

    if user_input.startswith("/"):
        cmd_ctx = CommandContext(
            message_history=state.message_history,
            deps=deps,
            agent=agent,
            frontend=frontend,
            completer=completer,
        )
        outcome = await dispatch_command(user_input, cmd_ctx)
        should_continue, new_history, new_input, saved_env = _apply_command_outcome(
            outcome, state.message_history, deps, frontend
        )
        if should_continue:
            return _IterationState(
                message_history=new_history,
                last_interrupt_time=0.0,
                should_exit=False,
            )
        # Delegate to agent turn with skill env and delegated input
        updated_history = await _run_foreground_turn(
            message_history=new_history,
            agent=agent,
            user_input=new_input,
            saved_env=saved_env,
            deps=deps,
            frontend=frontend,
        )
        return _IterationState(
            message_history=updated_history,
            last_interrupt_time=0.0,
            should_exit=False,
        )

    # Plain text input — run foreground turn
    updated_history = await _run_foreground_turn(
        message_history=state.message_history,
        agent=agent,
        user_input=user_input,
        saved_env={},
        deps=deps,
        frontend=frontend,
    )
    return _IterationState(
        message_history=updated_history,
        last_interrupt_time=0.0,
        should_exit=False,
    )


_COMPLETION_STYLE = Style.from_dict(
    {
        "completion-menu": "bg:default",
        "completion-menu.completion": "bg:default",
        "completion-menu.completion.current": "bold bg:default",
        "completion-menu.meta.completion": "fg:#888888 bg:default",
        "completion-menu.meta.completion.current": "fg:#aaaaaa bg:default bold",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "fg:#888888 bg:default",
    }
)


async def _chat_loop(
    reasoning_display: str = DEFAULT_REASONING_DISPLAY,
    theme: str | None = None,
):
    frontend = TerminalFrontend()

    completer = SlashCommandCompleter()
    session = PromptSession(
        history=FileHistory(str(USER_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=True,
        style=_COMPLETION_STYLE,
    )
    stack = AsyncExitStack()
    deps: CoDeps | None = None
    try:
        try:
            deps = await create_deps(frontend, stack, theme_override=theme)
        except ValueError as e:
            console.print(f"[bold red]Startup error:[/bold red] {e}")
            raise SystemExit(1) from e
        deps.session.reasoning_display = reasoning_display

        completer.update(build_completer_entries(deps.skill_index))
        from co_cli.context.manifests.skill_manifest import render_skill_manifest

        skill_manifest = render_skill_manifest(
            deps.skill_index, deps.skills_dir, deps.user_skills_dir
        )
        agent = build_agent(
            config=deps.config,
            model=deps.model,
            toolset=deps.toolset,
            tool_index=deps.tool_index,
            skill_manifest=skill_manifest or None,
        )

        current_session_path = restore_session(deps, frontend)
        init_session_index(deps, current_session_path, frontend)
        _sweep_tool_results(deps)
        from co_cli.skills.index import get_skill_index

        frontend.on_status(f"  {len(get_skill_index(deps.skill_index))} skill(s) loaded")

        if deps.session.session_path.exists():
            console.print("[dim]Previous session available — /resume to continue[/dim]")

        display_welcome_banner(deps)
        from co_cli.bootstrap.security import check_security, render_security_findings

        render_security_findings(check_security())
        frontend.clear_status()

        state = _IterationState(message_history=[], last_interrupt_time=0.0)

        while True:
            try:
                frontend.set_input_active(True)
                user_input = await session.prompt_async(f"Co {PROMPT_CHAR} ")
                frontend.set_input_active(False)
                state = await _handle_one_input(
                    user_input=user_input,
                    eof=False,
                    state=state,
                    deps=deps,
                    agent=agent,
                    frontend=frontend,
                    completer=completer,
                    now=time.monotonic(),
                )
            except EOFError:
                frontend.set_input_active(False)
                state = await _handle_one_input(
                    user_input=None,
                    eof=True,
                    state=state,
                    deps=deps,
                    agent=agent,
                    frontend=frontend,
                    completer=completer,
                    now=time.monotonic(),
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                frontend.set_input_active(False)
                state = await _handle_one_input(
                    user_input=None,
                    eof=False,
                    state=state,
                    deps=deps,
                    agent=agent,
                    frontend=frontend,
                    completer=completer,
                    now=time.monotonic(),
                )
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
                continue
            if state.should_exit:
                break
    finally:
        await _drain_and_cleanup(deps, stack)


def _start_chat(theme: str | None, verbose: bool, reasoning_display: str | None) -> None:
    """Resolve startup options and enter the interactive chat loop."""
    _setup_observability()
    set_theme(theme or settings.theme)
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
        asyncio.run(_chat_loop(reasoning_display=effective_mode, theme=theme))
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
def traces():
    """Open a visual trace viewer with nested spans (like Logfire)."""
    import webbrowser

    from co_cli.observability.viewer import write_trace_html

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
    from co_cli.observability.tail import run_tail

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
