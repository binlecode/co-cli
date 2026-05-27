"""Behavioural tests for _handle_one_input — the extracted chat loop iteration helper."""

import asyncio
import time
from pathlib import Path

import pytest
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._app import _ReplRuntime, build_key_bindings
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.main import _build_accept_handler, _handle_one_input, _IterationState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_deps(tmp_path: Path) -> CoDeps:
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_index = build_native_toolset(SETTINGS_NO_MCP)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )


def _make_agent(deps: CoDeps):
    """Build a real orchestrator agent from deps config.

    Uses build_model so provider/model come from the user's real settings.
    """
    toolset, tool_index = build_native_toolset(deps.config)
    deps.toolset = toolset
    deps.tool_index = tool_index
    deps.model = build_model(deps.config.llm)
    return build_orchestrator(ORCHESTRATOR_SPEC, deps)


def _fresh_state() -> _IterationState:
    """Return an initial _IterationState where the interrupt timer has never been set.

    Uses last_interrupt_time=-3.0 so that injected now=0.0 yields a delta of 3.0 > 2.0,
    matching real-world behaviour where time.monotonic() is always large and positive.
    """
    return _IterationState(message_history=[], last_interrupt_time=-3.0)


# ---------------------------------------------------------------------------
# Test 1: empty input — loop continues without exiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input_continues(tmp_path: Path) -> None:
    """Empty input returns the same state unchanged with should_exit=False.

    Regression guard: if empty input triggers an agent turn or sets should_exit,
    the user cannot submit an accidental blank line without disrupting the session.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    state = _fresh_state()

    result = await _handle_one_input(
        user_input="",
        eof=False,
        state=state,
        deps=deps,
        agent=agent,
        frontend=frontend,
        completer=completer,
        now=0.0,
    )

    assert result.should_exit is False
    assert result.message_history == []


# ---------------------------------------------------------------------------
# Test 2: "exit" exits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_command_exits(tmp_path: Path) -> None:
    """'exit' input returns should_exit=True without contacting the agent.

    Regression guard: if exit is misrouted to the agent, the session never terminates
    on user request and requires a process kill.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    state = _fresh_state()

    result = await _handle_one_input(
        user_input="exit",
        eof=False,
        state=state,
        deps=deps,
        agent=agent,
        frontend=frontend,
        completer=completer,
        now=0.0,
    )

    assert result.should_exit is True


# ---------------------------------------------------------------------------
# Test 4: Ctrl+C double-press within 2s exits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctrl_c_double_press_within_window_exits() -> None:
    """Two Ctrl+C presses within 2s produce should_exit=True on the second press.

    Regression guard: if the double-press timer is broken, a single Ctrl+C exits
    immediately (too aggressive) or never exits (inaccessible).
    """
    # Use None deps/agent/frontend — these code paths don't reach them
    state = _fresh_state()

    # First press at now=0.0: delta = 0.0 - (-3.0) = 3.0 > 2.0 → should not exit, updates timer
    first = await _handle_one_input(
        user_input=None,
        eof=False,
        state=state,
        # deps/agent are never reached when user_input is None (interrupt path exits early)
        deps=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        frontend=HeadlessFrontend(),
        completer=SlashCommandCompleter(),
        now=0.0,
    )
    assert first.should_exit is False
    assert first.last_interrupt_time == 0.0

    # Second press at now=1.5: delta = 1.5 - 0.0 = 1.5 <= 2.0 → exits
    second = await _handle_one_input(
        user_input=None,
        eof=False,
        state=first,
        # deps/agent are never reached when user_input is None (interrupt path exits early)
        deps=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        frontend=HeadlessFrontend(),
        completer=SlashCommandCompleter(),
        now=1.5,
    )
    assert second.should_exit is True


# ---------------------------------------------------------------------------
# Test 5: Ctrl+C outside 2s window resets timer without exiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctrl_c_outside_window_resets_timer() -> None:
    """Two Ctrl+C presses more than 2s apart do not exit; second resets the timer.

    Regression guard: if the timer is not reset, subsequent presses accumulate
    and may trigger spurious exits or never allow exit.
    """
    state = _fresh_state()

    # First press at now=0.0: delta = 3.0 > 2.0 → no exit, timer set to 0.0
    first = await _handle_one_input(
        user_input=None,
        eof=False,
        state=state,
        # deps/agent are never reached when user_input is None (interrupt path exits early)
        deps=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        frontend=HeadlessFrontend(),
        completer=SlashCommandCompleter(),
        now=0.0,
    )
    assert first.should_exit is False
    assert first.last_interrupt_time == 0.0

    # Second press at now=3.0: delta = 3.0 - 0.0 = 3.0 > 2.0 → no exit, timer reset to 3.0
    second = await _handle_one_input(
        user_input=None,
        eof=False,
        state=first,
        # deps/agent are never reached when user_input is None (interrupt path exits early)
        deps=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        frontend=HeadlessFrontend(),
        completer=SlashCommandCompleter(),
        now=3.0,
    )
    assert second.should_exit is False
    assert second.last_interrupt_time == 3.0


# ---------------------------------------------------------------------------
# Test 6: EOF exits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eof_exits() -> None:
    """EOFError signal (eof=True) returns should_exit=True immediately.

    Regression guard: if EOF is not handled, piped or terminal-closed sessions
    spin indefinitely waiting for input.
    """
    state = _fresh_state()

    result = await _handle_one_input(
        user_input=None,
        eof=True,
        state=state,
        # deps/agent are never reached when eof=True (EOF path returns before any agent call)
        deps=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        frontend=HeadlessFrontend(),
        completer=SlashCommandCompleter(),
        now=0.0,
    )

    assert result.should_exit is True


# ---------------------------------------------------------------------------
# Test 7: successful input resets the interrupt timer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_input_resets_interrupt_timer(tmp_path: Path) -> None:
    """Non-interrupt input with a non-empty, non-exit value resets last_interrupt_time to 0.0.

    Regression guard: if the timer is not reset on normal input, a Ctrl+C at t=1s
    after a Ctrl+C at t=0s (with real input between them) still exits — wrong behaviour.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    # Prime the state with a non-zero interrupt timer
    state = _IterationState(message_history=[], last_interrupt_time=1.0)

    # Whitespace-only input does NOT reset the timer (it returns early unchanged)
    whitespace_result = await _handle_one_input(
        user_input="   ",
        eof=False,
        state=state,
        deps=deps,
        agent=agent,
        frontend=frontend,
        completer=completer,
        now=5.0,
    )
    assert whitespace_result.last_interrupt_time == 1.0

    # "exit" resets timer to 0.0 and sets should_exit=True
    exit_result = await _handle_one_input(
        user_input="exit",
        eof=False,
        state=state,
        deps=deps,
        agent=agent,
        frontend=frontend,
        completer=completer,
        now=5.0,
    )
    assert exit_result.last_interrupt_time == 0.0
    assert exit_result.should_exit is True


# ---------------------------------------------------------------------------
# Test 8: slash command routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_command_routes_to_dispatch(tmp_path: Path) -> None:
    """/clear slash command is dispatched and returns should_exit=False.

    Regression guard: if slash commands bypass dispatch, built-in commands like
    /clear, /help, /resume silently fail — the session accumulates stale history.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    state = _fresh_state()

    result = await _handle_one_input(
        user_input="/clear",
        eof=False,
        state=state,
        deps=deps,
        agent=agent,
        frontend=frontend,
        completer=completer,
        now=0.0,
    )

    assert result.should_exit is False


# ---------------------------------------------------------------------------
# Test 9: plain text routing — non-slash input reaches _run_foreground_turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_text_routes_to_foreground_turn(tmp_path: Path) -> None:
    """Plain text input is forwarded to _run_foreground_turn, extending message history.

    Regression guard: if plain text bypasses the agent, user messages are silently
    swallowed with no response — the session appears to hang.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    state = _fresh_state()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        result = await _handle_one_input(
            user_input="Say OK",
            eof=False,
            state=state,
            deps=deps,
            agent=agent,
            frontend=frontend,
            completer=completer,
            now=0.0,
        )

    assert result.should_exit is False
    assert len(result.message_history) > len(state.message_history)


# ---------------------------------------------------------------------------
# Test 10: accept_handler schedules a turn task (idle) / drops (mid-turn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_handler_schedules_turn_task() -> None:
    """Idle submission arms a turn task via the accept_handler; a submission while
    a turn is active enqueues (FIFO) instead of dropping — the Phase 1 seam.

    Regression guard: if the accept_handler blocked or ran turns concurrently,
    mid-turn submissions would interleave on the single owned terminal instead of
    buffering for the next turn boundary.
    """
    dispatched: list[tuple] = []

    async def dispatch(*, user_input, eof):
        dispatched.append((user_input, eof))

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=-3.0))
    handler = _build_accept_handler(runtime, dispatch, lambda: None)

    class _Buf:
        text = "hello"

    handler(_Buf())
    assert runtime.turn_task is not None
    await asyncio.sleep(0.01)
    assert dispatched == [("hello", False)]

    # Mid-turn: a still-running turn task makes the next submission enqueue.
    long_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = long_task
    dispatched.clear()
    handler(_Buf())
    await asyncio.sleep(0.01)
    assert dispatched == []
    assert list(runtime.queue) == ["hello"]
    long_task.cancel()


@pytest.mark.asyncio
async def test_typed_ahead_enqueues_and_drains_fifo() -> None:
    """Submissions during an active turn enqueue and drain one-per-boundary in
    FIFO order after the turn completes (C3)."""
    gate = asyncio.Event()
    order: list[str] = []
    depths: list[int] = []

    async def dispatch(*, user_input, eof):
        order.append(user_input)
        await gate.wait()

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    handler = _build_accept_handler(runtime, dispatch, lambda: depths.append(len(runtime.queue)))

    def _buf(text: str):
        return type("_Buf", (), {"text": text})()

    # Arm turn 1 and let it reach the gate so it is genuinely active.
    handler(_buf("first"))
    await asyncio.sleep(0)
    assert runtime.turn_active

    # Two mid-turn submissions enqueue (depth 1 → 2).
    handler(_buf("second"))
    handler(_buf("third"))
    assert list(runtime.queue) == ["second", "third"]
    assert depths == [1, 2]

    # Release the gate: turn 1 completes, queue drains FIFO one-per-boundary.
    gate.set()
    for _ in range(20):
        await asyncio.sleep(0.01)
        if not runtime.queue and not runtime.turn_active:
            break
    assert order == ["first", "second", "third"]
    assert len(runtime.queue) == 0


@pytest.mark.asyncio
async def test_blank_input_never_enqueues() -> None:
    """Whitespace submissions while a turn is active never occupy a queue slot (C3)."""
    dispatched: list[tuple] = []

    async def dispatch(*, user_input, eof):
        dispatched.append((user_input, eof))

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    handler = _build_accept_handler(runtime, dispatch, lambda: None)

    long_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = long_task

    handler(type("_Buf", (), {"text": "   "})())
    handler(type("_Buf", (), {"text": ""})())
    handler(type("_Buf", (), {"text": "\t\n"})())
    await asyncio.sleep(0.01)
    assert list(runtime.queue) == []
    long_task.cancel()


# ---------------------------------------------------------------------------
# Test 11: Ctrl+C is exit-only (double-press); interrupt moved to Esc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctrl_c_is_exit_only_double_press() -> None:
    """c-c no longer cancels the active turn (interrupt moved to Esc); the first
    press arms the 2 s double-press window, a second within 2 s exits (C6).

    Regression guard: if c-c cancelled the turn, Esc and Ctrl+C would collide on
    the interrupt role and double-press exit semantics would be ambiguous.
    """
    from prompt_toolkit.keys import Keys

    exited: list[bool] = []
    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))

    async def dispatch(*, user_input, eof):
        runtime.state = await _handle_one_input(
            user_input=user_input,
            eof=eof,
            state=runtime.state,
            deps=None,  # type: ignore[arg-type]
            agent=None,  # type: ignore[arg-type]
            frontend=HeadlessFrontend(),
            completer=SlashCommandCompleter(),
            now=time.monotonic(),
        )
        if runtime.state.should_exit:
            exited.append(True)

    kb = build_key_bindings(runtime=runtime, dispatch=dispatch)
    cc = kb.get_bindings_for_keys((Keys.ControlC,))[0]

    turn_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = turn_task

    # First c-c: does NOT cancel the active turn; arms double-press (no exit).
    cc.handler(object())
    await asyncio.sleep(0.01)
    assert not turn_task.cancelled()
    assert not turn_task.done()
    assert exited == []

    # Second c-c within 2 s: exits.
    cc.handler(object())
    await asyncio.sleep(0.01)
    assert exited == [True]
    turn_task.cancel()


@pytest.mark.asyncio
async def test_esc_cancels_turn_and_advances_queue() -> None:
    """Esc cancels the active turn; its done-callback drains the next queued item
    as the next turn (C4). The queue advances one item, exit is untouched."""
    from prompt_toolkit.keys import Keys

    gate = asyncio.Event()
    order: list[str] = []

    async def dispatch(*, user_input, eof):
        order.append(user_input)
        if user_input == "first":
            await gate.wait()

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    handler = _build_accept_handler(runtime, dispatch, lambda: None)
    kb = build_key_bindings(runtime=runtime, dispatch=dispatch)

    def _buf(text: str):
        return type("_Buf", (), {"text": text})()

    # Arm turn 1 (carries the drain callback) and reach the gate.
    handler(_buf("first"))
    await asyncio.sleep(0)
    assert runtime.turn_active
    turn_one = runtime.turn_task

    # One queued item waiting behind the active turn.
    handler(_buf("queued"))
    assert list(runtime.queue) == ["queued"]

    # Esc cancels turn 1; the done-callback drains "queued" as the next turn.
    esc = kb.get_bindings_for_keys((Keys.Escape,))[0]
    esc.handler(object())
    for _ in range(20):
        await asyncio.sleep(0.01)
        if not runtime.queue and not runtime.turn_active:
            break
    assert turn_one.cancelled()
    assert order == ["first", "queued"]
    assert len(runtime.queue) == 0
    assert not runtime.state.should_exit
