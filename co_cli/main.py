import asyncio
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import typer
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import BinaryContent, ModelMessage

from co_cli.agent.build import build_orchestrator
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.bootstrap.banner import display_welcome_banner
from co_cli.bootstrap.core import (
    create_deps,
    maybe_autospawn_dream,
    start_session,
)
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.commands.core import dispatch as dispatch_command
from co_cli.commands.queue_control import run_queue_control
from co_cli.commands.registry import build_completer_entries
from co_cli.commands.status_report import context_pct
from co_cli.commands.types import CommandContext, DelegateToAgent, ReplaceTranscript
from co_cli.config.core import (
    DEFAULT_REASONING_DISPLAY,
    LOGS_DIR,
    REASONING_DISPLAY_FULL,
    USER_DIR,
    VALID_REASONING_DISPLAY_MODES,
    settings,
)
from co_cli.context.orchestrate import TurnResult, run_turn
from co_cli.context.summarization import estimate_message_tokens
from co_cli.daemons.dream.kick import write_review_kick
from co_cli.deps import CoDeps
from co_cli.display.app import ReplRuntime, build_key_bindings, build_repl_app
from co_cli.display.core import (
    PROMPT_CHAR,
    Frontend,
    StatusSnapshot,
    TerminalFrontend,
    console,
    set_theme,
)
from co_cli.observability.setup import setup_observability
from co_cli.project_info import project_info
from co_cli.session.browser import extract_title
from co_cli.session.persistence import persist_session_history
from co_cli.session.usage import ORIGIN_SESSION, append_turn
from co_cli.skills.lifecycle import cleanup_skill_run_state
from co_cli.tools.tool_io import sweep_tool_result_orphans
from co_cli.tools.vision.intake import (
    ImageRejection,
    detect_lone_image_path,
    read_image,
)

_VERSION = project_info().version


def _setup_observability() -> None:
    setup_observability(
        LOGS_DIR,
        app_log_name="co-cli.jsonl",
        spans_log_name="co-cli-spans.jsonl",
        errors_log_name="errors.jsonl",
        settings=settings,
    )


from co_cli.commands.dream import dream_app
from co_cli.commands.google import google_app

app = typer.Typer(
    help="Co — personal AI operator · local-first · approval-first",
    context_settings={"help_option_names": ["--help", "-h"]},
    invoke_without_command=True,
)
app.add_typer(dream_app, name="dream")
app.add_typer(google_app, name="google")


@app.callback()
def _default(ctx: typer.Context):
    """Start an interactive chat session (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        _start_chat(theme=None, verbose=False, reasoning_display=None)


def _flush_turn_usage(deps: CoDeps) -> None:
    """Append the turn's accumulated token usage to the durable ledger, then reset.

    Best-effort (append_turn swallows its own errors). The line is session-origin,
    keyed by the active short session id; reset clears the accumulator for the next turn.
    """
    accumulator = deps.usage_accumulator
    session_path = deps.session.session_path
    if session_path is not None:
        append_turn(
            deps.usage_log_path,
            origin=ORIGIN_SESSION,
            session_id=session_path.stem[-8:],
            input_tokens=accumulator.input_tokens,
            output_tokens=accumulator.output_tokens,
            turn_ended_at=datetime.now(UTC),
        )
    deps.usage_accumulator.reset()


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
        if deps.session.session_title is None:
            deps.session.session_title = extract_title(deps.session.session_path)
    except OSError as e:
        frontend.on_status(
            f"Session write failed — conversation may not be saved. Check disk space. ({e})"
        )

    _flush_turn_usage(deps)

    # Emit error banner when outcome is error
    if turn_result.outcome == "error":
        console.print("[error]An error occurred during this turn.[/error]")

    return next_history


async def _run_foreground_turn(
    *,
    message_history: list[ModelMessage],
    agent: Agent[CoDeps, str | DeferredToolRequests],
    user_input: str | list[str | BinaryContent],
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
    _post_turn_hook(deps, next_history, turn_result.model_requests)
    return next_history


def _fire_session_end_kicks(deps: CoDeps) -> None:
    """Fire memory and skill review KICKs at session end.

    Both KICKs fire unconditionally (no counter check) so that the daemon
    has a chance to review the session regardless of how many turns ran.
    Guard: only fires when review is enabled and a model is configured.
    """
    if not deps.config.skills.review_enabled:
        return
    if deps.model is None:
        return
    persisted = deps.runtime.persisted_message_count
    session_id = deps.session.session_path.stem
    write_review_kick(domain="memory", session_id=session_id, persisted_message_count=persisted)
    write_review_kick(domain="skill", session_id=session_id, persisted_message_count=persisted)


async def _drain_and_cleanup(
    deps: CoDeps | None,
    stack: AsyncExitStack,
) -> None:
    """Fire session-end review KICKs and release resources."""
    if deps is not None:
        _fire_session_end_kicks(deps)

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
            deps.memory_store.index.close()
    await stack.aclose()


def _sweep_tool_results(deps: CoDeps) -> None:
    swept = sweep_tool_result_orphans(deps.tool_results_dir)
    if swept:
        logging.getLogger(__name__).debug("Swept %d stale tool-result tmp file(s)", swept)


def _maybe_kick_memory_review(deps: CoDeps) -> None:
    """Fire a memory KICK when the turn counter reaches the configured interval."""
    skill_settings = deps.config.skills
    if deps.session.turns_since_memory_review >= skill_settings.review_memory_nudge_interval:
        deps.session.turns_since_memory_review = 0
        write_review_kick(
            domain="memory",
            session_id=deps.session.session_path.stem,
            persisted_message_count=deps.runtime.persisted_message_count,
        )


def _maybe_kick_skill_review(deps: CoDeps) -> None:
    """Fire a skill KICK when the model-request counter reaches the configured interval."""
    skill_settings = deps.config.skills
    if (
        deps.session.model_requests_since_skill_review
        >= skill_settings.review_skill_nudge_interval
    ):
        deps.session.model_requests_since_skill_review = 0
        write_review_kick(
            domain="skill",
            session_id=deps.session.session_path.stem,
            persisted_message_count=deps.runtime.persisted_message_count,
        )


def _post_turn_hook(
    deps: CoDeps | None,
    message_history: list[ModelMessage],
    model_request_count: int,
) -> None:
    """Bump per-domain counters and fire review KICKs when thresholds are reached.

    Constant-time: counter bumps + threshold compares + at-most-two atomic file writes.
    No single-in-flight guard — the queue is the back-pressure layer.
    """
    if deps is None:
        return
    skill_settings = deps.config.skills
    if not skill_settings.review_enabled:
        return
    if deps.model is None:
        return

    deps.session.turns_since_memory_review += 1
    deps.session.model_requests_since_skill_review += model_request_count
    _maybe_kick_memory_review(deps)
    _maybe_kick_skill_review(deps)


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
            # /compact's summarizer ran via llm_call, so the accumulator holds its tokens —
            # flush them as this turn's line, else they mis-attribute to the next real turn.
            _flush_turn_usage(deps)
        else:
            deps.runtime.persisted_message_count = len(outcome.history)
            # /resume and other no-LLM transcript swaps: reset defensively (normally 0 here).
            deps.usage_accumulator.reset()
        # Seed the context-usage estimate so the footer shows `ctx %` immediately
        # after a transcript swap, before the next turn runs. Mirrors the spill
        # trigger formula (history_processors.py) so the value cannot drift.
        deps.runtime.current_request_tokens_estimate = (
            deps.static_floor_tokens + estimate_message_tokens(outcome.history)
        )
        return True, outcome.history, "", {}
    if isinstance(outcome, DelegateToAgent):
        saved_env: dict[str, str | None] = {k: os.environ.get(k) for k in outcome.skill_env}
        os.environ.update(outcome.skill_env)
        deps.runtime.active_skill_name = outcome.skill_name
        deps.runtime.active_skill_env = dict(outcome.skill_env)
        return False, message_history, outcome.delegated_input, saved_env
    # LocalOnly
    return True, message_history, "", {}


@dataclass
class IterationState:
    message_history: list[ModelMessage]
    last_interrupt_time: float
    should_exit: bool = False


def _attach_user_image(
    user_input: str, path: Path, deps: CoDeps
) -> str | list[str | BinaryContent]:
    """Build the turn input for a user-dragged lone image path.

    The path is already known to be a lone image gesture (detect_lone_image_path matched).
    When the agent model can see, read the pixels and return ``[text, BinaryContent]`` so the
    agent answers about it on this turn — the text (including the path) is retained verbatim.
    A blind model, or an unreadable/oversize image, gets exactly one honest notice and the
    turn runs text-only.
    """
    if not deps.agent_vision_capable:
        console.print(
            "[dim]You referenced an image, but the current model can't see it — "
            "proceeding text-only.[/dim]"
        )
        return user_input
    image = read_image(path)
    if isinstance(image, ImageRejection):
        console.print(f"[dim]Could not attach image: {image.message}[/dim]")
        return user_input
    return [user_input, image]


async def _handle_one_input(
    user_input: str | None,
    eof: bool,
    state: IterationState,
    deps: CoDeps,
    agent: Agent,
    frontend: Frontend,
    completer: SlashCommandCompleter,
    now: float,
    queue: deque[str],
) -> IterationState:
    """Process one iteration of the chat loop given pre-parsed input signals.

    user_input=None signals KeyboardInterrupt; eof=True signals EOFError.
    now is an injected clock value for deterministic double-press tests.
    Returns a new IterationState.
    """
    if eof:
        return IterationState(
            message_history=state.message_history,
            last_interrupt_time=state.last_interrupt_time,
            should_exit=True,
        )

    if user_input is None:
        # KeyboardInterrupt signal
        if now - state.last_interrupt_time <= 2.0:
            return IterationState(
                message_history=state.message_history,
                last_interrupt_time=state.last_interrupt_time,
                should_exit=True,
            )
        console.print("\n[dim]Press Ctrl+C again to exit[/dim]")
        return IterationState(
            message_history=state.message_history,
            last_interrupt_time=now,
            should_exit=False,
        )

    if user_input.lower() in ("exit", "quit"):
        return IterationState(
            message_history=state.message_history,
            last_interrupt_time=0.0,
            should_exit=True,
        )

    if not user_input.strip():
        return IterationState(
            message_history=state.message_history,
            last_interrupt_time=state.last_interrupt_time,
            should_exit=False,
        )

    # Echo the submitted input to scrollback — the inline Application's TextArea
    # does not commit accepted input (unlike PromptSession.prompt), so without this
    # neither slash commands nor turns leave a record of what the user typed. One
    # place covers both idle-armed and queue-drained inputs.
    console.print(f"[dim]{PROMPT_CHAR}[/dim] {user_input}")

    # A lone image path the user dragged in is a "look at this" gesture, not a command —
    # detect it BEFORE slash dispatch so a bare absolute path (which starts with "/") is
    # honored rather than rejected as an unknown command. Collision-free: no slash command
    # ends in an image suffix, so this only fires when the whole input is an existing image.
    image_path = detect_lone_image_path(user_input, deps.workspace_dir)
    if image_path is not None:
        turn_input = _attach_user_image(user_input, image_path, deps)
        updated_history = await _run_foreground_turn(
            message_history=state.message_history,
            agent=agent,
            user_input=turn_input,
            saved_env={},
            deps=deps,
            frontend=frontend,
        )
        frontend.update_status(_build_status_snapshot(deps, "idle", queue))
        return IterationState(
            message_history=updated_history,
            last_interrupt_time=0.0,
            should_exit=False,
        )

    if user_input.startswith("/"):
        cmd_ctx = CommandContext(
            message_history=state.message_history,
            deps=deps,
            agent=agent,
            frontend=frontend,
            completer=completer,
            input_queue=queue,
        )
        outcome = await dispatch_command(user_input, cmd_ctx)
        should_continue, new_history, new_input, saved_env = _apply_command_outcome(
            outcome, state.message_history, deps, frontend
        )
        if should_continue:
            return IterationState(
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
        frontend.update_status(_build_status_snapshot(deps, "idle", queue))
        return IterationState(
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
    frontend.update_status(_build_status_snapshot(deps, "idle", queue))
    return IterationState(
        message_history=updated_history,
        last_interrupt_time=0.0,
        should_exit=False,
    )


_QUEUE_PREVIEW_BUDGET_CHARS = 30


def _queue_head_preview(queue: deque[str]) -> str | None:
    if not queue:
        return None
    head = queue[0].replace("\n", " ").strip()
    if len(head) <= _QUEUE_PREVIEW_BUDGET_CHARS:
        return head
    return head[: _QUEUE_PREVIEW_BUDGET_CHARS - 1] + "…"


_SESSION_LABEL_BUDGET_CHARS = 30


def _session_label(title: str | None) -> str:
    """Footer label: a placeholder before the first message, else the truncated title."""
    if title is None:
        return "(new session)"
    if len(title) <= _SESSION_LABEL_BUDGET_CHARS:
        return title
    return title[: _SESSION_LABEL_BUDGET_CHARS - 1] + "…"


def _build_status_snapshot(
    deps: CoDeps, mode: Literal["idle", "active"], queue: deque[str]
) -> StatusSnapshot:
    return StatusSnapshot(
        session_label=_session_label(deps.session.session_title),
        mode=mode,
        context_pct=context_pct(deps),
        background_task_count=len(deps.session.background_tasks),
        approval_count=len(deps.session.session_approval_rules),
        queue_depth=len(queue),
        queue_head_preview=_queue_head_preview(queue),
    )


def _parse_queue_command(text: str) -> tuple[bool, str]:
    """Mirror `dispatch`'s slash parse to decide if `text` is a `/queue` invocation.

    Returns `(is_queue, args)`. Used by the mid-turn bypass in
    `_build_accept_handler` so idle and mid-turn parsing cannot diverge.
    """
    if not text.startswith("/"):
        return False, ""
    parts = text[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return name == "queue", args


_QUEUE_NOTICE_BUDGET_CHARS = 60


def _preview(text: str, budget: int = _QUEUE_NOTICE_BUDGET_CHARS) -> str:
    """One-line preview of a queue item for drop/reject notices."""
    text = text.replace("\n", " ")
    if len(text) <= budget:
        return text
    return text[: budget - 1] + "…"


def _enqueue(
    runtime: ReplRuntime,
    text: str,
    deps: CoDeps,
    on_status: Callable[[], None],
) -> None:
    """Append a mid-turn submission to the input queue under the bounded-queue policy.

    Blank-drop comes first — a blank never counts against the cap (Phase 1). Then,
    when ``repl.queue_cap > 0`` and the append would exceed the cap, drop per
    ``repl.drop_policy``: ``"oldest"`` pops the head then appends the new item;
    ``"newest"`` rejects the incoming item. Either drop path emits exactly one
    notice. Every accepted/rejected non-blank submission ends in exactly one
    ``on_status`` repaint. ``queue_cap == 0`` is unbounded (Phase 1/2 default, C3).
    """
    if not text.strip():
        return
    cap = deps.config.repl.queue_cap
    if cap > 0 and len(runtime.queue) >= cap:
        if deps.config.repl.drop_policy == "oldest":
            dropped = runtime.queue.popleft()
            runtime.queue.append(text)
            console.print(
                f"[dim]Queue full (cap {cap}) — dropped oldest: {_preview(dropped)!r}[/dim]"
            )
        else:
            console.print(
                f"[dim]Queue full (cap {cap}) — rejected new item: {_preview(text)!r}[/dim]"
            )
    else:
        runtime.queue.append(text)
    on_status()


def _build_accept_handler(
    runtime: ReplRuntime,
    dispatch: Callable[..., Awaitable[None]],
    on_status: Callable[[], None],
    deps: CoDeps,
    frontend: TerminalFrontend,
) -> Callable[[Buffer], bool]:
    """Return the input TextArea accept_handler wired for the input queue.

    Idle submissions arm a turn; submissions arriving while a turn is active
    enqueue (FIFO, non-blank only) instead of dropping. Each armed turn carries
    a done-callback that drains the next queued item at the turn boundary —
    normal completion *and* Esc-cancel both fire it — so the queue advances one
    item per turn. ``on_status`` repaints the toolbar on enqueue and on dequeue.
    Mid-turn ``/queue`` bypasses the queue and runs the queue-control core via
    ``schedule_control`` — it is a buffer op, not a turn, so it never carries
    the `_drain_next` callback (Phase 2 C1).
    The handler only schedules/enqueues; it never blocks the app's input loop,
    per co's same-loop concurrency discipline.
    """

    def _arm_turn(coro: Awaitable[None]) -> None:
        runtime.turn_task = asyncio.ensure_future(coro)
        runtime.turn_task.add_done_callback(_drain_next)

    def _drain_next(_task: "asyncio.Task[None]") -> None:
        # A drained item that set should_exit ends the session; remaining queued
        # items are intentionally dropped (C8). Empty queue → nothing to drain.
        if runtime.state.should_exit or not runtime.queue:
            return
        next_input = runtime.queue.popleft()
        on_status()
        _arm_turn(dispatch(user_input=next_input, eof=False))

    async def _run_queue_bypass(args: str) -> None:
        run_queue_control(runtime.queue, args)
        on_status()

    def accept_handler(buffer: Buffer) -> bool:
        text = buffer.text
        if frontend.question_active:
            frontend.resolve_question(text)
            return False
        if runtime.turn_active:
            is_queue, queue_args = _parse_queue_command(text)
            if is_queue:
                runtime.schedule_control(_run_queue_bypass(queue_args))
                return False
            _enqueue(runtime, text, deps, on_status)
            return False
        _arm_turn(dispatch(user_input=text, eof=False))
        return False

    return accept_handler


async def _chat_loop(
    reasoning_display: str = DEFAULT_REASONING_DISPLAY,
    theme: str | None = None,
):
    frontend = TerminalFrontend()
    completer = SlashCommandCompleter()
    history = FileHistory(str(USER_DIR / "history.txt"))
    stack = AsyncExitStack()
    deps: CoDeps | None = None
    try:
        try:
            deps = await create_deps(
                on_status=frontend.on_status,
                stack=stack,
                theme_override=theme,
            )
        except ValueError as e:
            console.print(f"[bold red]Startup error:[/bold red] {e}")
            raise SystemExit(1) from e
        deps.session.reasoning_display = reasoning_display

        completer.update(build_completer_entries(deps.skill_catalog))
        agent = build_orchestrator(ORCHESTRATOR_SPEC, deps)

        start_session(deps, frontend)
        _sweep_tool_results(deps)
        from co_cli.skills.index import get_skill_catalog

        frontend.on_status(f"  {len(get_skill_catalog(deps.skill_catalog))} skill(s) loaded")

        if any(deps.sessions_dir.glob("*.jsonl")):
            console.print("[dim]Previous session available — /resume to continue[/dim]")

        memory_count = 0
        session_count = 0
        if deps.memory_store is not None:
            memory_count = deps.memory_store.count()
        if deps.session_store is not None:
            session_count = deps.session_store.count()
        maybe_autospawn_dream(deps, frontend)
        display_welcome_banner(deps, memory_count=memory_count, session_count=session_count)
        from co_cli.bootstrap.security import check_security, render_security_findings

        render_security_findings(check_security())
        frontend.clear_status()
        # Startup snapshot fires before `runtime` is constructed; the queue is
        # genuinely empty here, so 0 is the literal truth — not a default
        # waiting for a corrector push.
        frontend.update_status(_build_status_snapshot(deps, "idle", deque()))

        # Single owner of turn state, shared by the accept_handler and the key
        # bindings (Esc cancels the active turn) (F7) — created here in loop
        # scope, never a module global.
        runtime = ReplRuntime(state=IterationState(message_history=[], last_interrupt_time=0.0))

        async def _dispatch(*, user_input: str | None, eof: bool) -> None:
            """Run one chat-loop iteration, apply its result, and exit when asked.

            Scheduled as the turn task (idle submission or queued-item drain) or
            as a control task (Ctrl+C / EOF). A mid-turn Esc cancels the turn
            task, surfacing here as CancelledError — clean up the terminal and let
            it propagate; the task's done-callback then advances the queue.
            """
            frontend.update_status(_build_status_snapshot(deps, "active", runtime.queue))
            try:
                runtime.state = await _handle_one_input(
                    user_input=user_input,
                    eof=eof,
                    state=runtime.state,
                    deps=deps,
                    agent=agent,
                    frontend=frontend,
                    completer=completer,
                    now=time.monotonic(),
                    queue=runtime.queue,
                )
            except asyncio.CancelledError:
                frontend.cleanup()
                raise
            except Exception as e:
                frontend.cleanup()
                console.print(f"[bold red]Error:[/bold red] {e}")
                runtime.state = IterationState(
                    message_history=runtime.state.message_history,
                    last_interrupt_time=0.0,
                )
            frontend.update_status(_build_status_snapshot(deps, "idle", runtime.queue))
            if runtime.state.should_exit:
                app.exit()

        def _on_queue_status() -> None:
            frontend.update_status(_build_status_snapshot(deps, "active", runtime.queue))

        accept_handler = _build_accept_handler(
            runtime, _dispatch, _on_queue_status, deps, frontend
        )
        key_bindings = build_key_bindings(runtime=runtime, dispatch=_dispatch, frontend=frontend)
        app = build_repl_app(
            frontend=frontend,
            completer=completer,
            history=history,
            accept_handler=accept_handler,
            key_bindings=key_bindings,
        )
        frontend.bind_app(app)

        # patch_stdout makes the ~128 incidental console.print/display_* sites
        # reflow above the input area (BC4) — the load-bearing half of single
        # terminal ownership, not polish.
        # raw=True so the themed console's ANSI (rich detects the proxied stdout
        # as a tty and emits SGR codes) is passed through via write_raw instead of
        # sanitized ESC->'?' by Vt100_Output.write — without it every styled
        # mid-app console.print renders garbled escape sequences.
        with patch_stdout(raw=True):
            await app.run_async()
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
        None, "--reasoning-display", help="Reasoning display mode: off, collapsed, full"
    ),
):
    """Start an interactive chat session with Co."""
    _start_chat(theme=theme, verbose=verbose, reasoning_display=reasoning_display)


@app.command()
def tail(
    trace_id: str = typer.Option(None, "--trace", "-i", help="Filter to a specific trace ID"),
    tools_only: bool = typer.Option(False, "--tools-only", "-T", help="Only show tool spans"),
    models_only: bool = typer.Option(False, "--models-only", "-m", help="Only show model spans"),
    poll: float = typer.Option(0.1, "--poll", "-p", help="Poll interval in seconds"),
    no_follow: bool = typer.Option(False, "--no-follow", "-n", help="Print recent spans and exit"),
    last: int = typer.Option(20, "--last", "-l", help="Number of recent spans to show on startup"),
    detail: bool = typer.Option(
        False, "--detail", "-d", help="Append type-aware detail blocks (input/output/args/result)"
    ),
):
    """Tail agent spans in real time (like tail -f for the structured-log spans file)."""
    from co_cli.observability.tail import run_tail

    run_tail(
        trace_id=trace_id,
        tools_only=tools_only,
        models_only=models_only,
        poll_interval=poll,
        no_follow=no_follow,
        last=last,
        detail=detail,
    )


@app.command()
def trace(
    trace_id: str = typer.Argument(..., help="Trace ID to render"),
):
    """Render one trace as a snapshot tree from the structured-log spans file."""
    from co_cli.observability.trace_view import render_trace

    render_trace(trace_id)


if __name__ == "__main__":
    app()
