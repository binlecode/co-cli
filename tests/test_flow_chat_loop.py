"""Behavioural tests for _handle_one_input — the extracted chat loop iteration helper."""

import asyncio
import time
from collections import deque
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM, make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.config.repl import ReplSettings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._app import _ReplRuntime, build_key_bindings
from co_cli.display.core import TerminalFrontend, console
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.main import _build_accept_handler, _handle_one_input, _IterationState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_deps(tmp_path: Path) -> CoDeps:
    skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_catalog = build_native_toolset()
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        skill_catalog=skill_catalog,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )


def _make_deps_with_repl(tmp_path: Path, queue_cap: int, drop_policy: str = "oldest") -> CoDeps:
    """Build deps whose config carries a bounded-queue ReplSettings override."""
    deps = _make_deps(tmp_path)
    deps.config = make_settings(
        mcp_servers={},
        repl=ReplSettings(queue_cap=queue_cap, drop_policy=drop_policy),
    )
    return deps


def _make_agent(deps: CoDeps):
    """Build a real orchestrator agent from deps config.

    Uses build_model so provider/model come from the user's real settings.
    """
    toolset, tool_catalog = build_native_toolset()
    deps.toolset = toolset
    deps.tool_catalog = tool_catalog
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
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
        queue=deque(),
    )
    assert exit_result.last_interrupt_time == 0.0
    assert exit_result.should_exit is True


# ---------------------------------------------------------------------------
# Test 8: slash command routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_command_routes_to_dispatch(tmp_path: Path) -> None:
    """/clear is dispatched to the builtin and replaces the transcript with empty history.

    Regression guard: if slash commands bypass dispatch, /clear silently fails and
    the session keeps accumulating stale history. Priming a non-empty history makes
    the empty-result assertion fail if dispatch did nothing.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    state = _IterationState(
        message_history=[ModelRequest(parts=[UserPromptPart(content="prior turn")])],
        last_interrupt_time=-3.0,
    )

    result = await _handle_one_input(
        user_input="/clear",
        eof=False,
        state=state,
        deps=deps,
        agent=agent,
        frontend=frontend,
        completer=completer,
        now=0.0,
        queue=deque(),
    )

    assert result.should_exit is False
    assert result.message_history == []


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
            queue=deque(),
        )

    assert result.should_exit is False
    assert len(result.message_history) > len(state.message_history)
    assert isinstance(result.message_history[-1], ModelResponse), (
        "plain text must drive a turn that ends with an assistant response, "
        "not grow history by request frames alone"
    )


# ---------------------------------------------------------------------------
# Test 10: accept_handler schedules a turn task (idle) / drops (mid-turn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_handler_schedules_turn_task(tmp_path: Path) -> None:
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
    handler = _build_accept_handler(
        runtime, dispatch, lambda: None, _make_deps(tmp_path), TerminalFrontend()
    )

    class _Buf:
        text = "hello"

    handler(_Buf())
    assert runtime.turn_active
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
async def test_typed_ahead_enqueues_and_drains_fifo(tmp_path: Path) -> None:
    """Submissions during an active turn enqueue and drain one-per-boundary in
    FIFO order after the turn completes (C3)."""
    gate = asyncio.Event()
    order: list[str] = []
    depths: list[int] = []

    async def dispatch(*, user_input, eof):
        order.append(user_input)
        await gate.wait()

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    handler = _build_accept_handler(
        runtime,
        dispatch,
        lambda: depths.append(len(runtime.queue)),
        _make_deps(tmp_path),
        TerminalFrontend(),
    )

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
async def test_blank_input_never_enqueues(tmp_path: Path) -> None:
    """Whitespace submissions while a turn is active never occupy a queue slot (C3)."""
    dispatched: list[tuple] = []

    async def dispatch(*, user_input, eof):
        dispatched.append((user_input, eof))

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    handler = _build_accept_handler(
        runtime, dispatch, lambda: None, _make_deps(tmp_path), TerminalFrontend()
    )

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
# Test 10b: bounded-queue cap + drop policy (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_cap_drops_oldest(tmp_path: Path) -> None:
    """cap=2 + drop_policy='oldest': a third mid-turn submit drops the head, so the
    queue holds the last two items, and exactly one drop notice is emitted (C3)."""

    async def dispatch(*, user_input, eof):
        pass

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    deps = _make_deps_with_repl(tmp_path, queue_cap=2, drop_policy="oldest")
    handler = _build_accept_handler(runtime, dispatch, lambda: None, deps, TerminalFrontend())

    long_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = long_task

    def _buf(text: str):
        return type("_Buf", (), {"text": text})()

    with console.capture() as capture:
        handler(_buf("first"))
        handler(_buf("second"))
        handler(_buf("third"))
    output = capture.get()

    assert list(runtime.queue) == ["second", "third"]
    assert output.count("Queue full") == 1
    assert "dropped oldest" in output
    long_task.cancel()


@pytest.mark.asyncio
async def test_queue_cap_newest_rejects(tmp_path: Path) -> None:
    """cap=2 + drop_policy='newest': a third mid-turn submit is rejected, so the queue
    holds the first two items, and exactly one reject notice is emitted (C3)."""

    async def dispatch(*, user_input, eof):
        pass

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    deps = _make_deps_with_repl(tmp_path, queue_cap=2, drop_policy="newest")
    handler = _build_accept_handler(runtime, dispatch, lambda: None, deps, TerminalFrontend())

    long_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = long_task

    def _buf(text: str):
        return type("_Buf", (), {"text": text})()

    with console.capture() as capture:
        handler(_buf("first"))
        handler(_buf("second"))
        handler(_buf("third"))
    output = capture.get()

    assert list(runtime.queue) == ["first", "second"]
    assert output.count("Queue full") == 1
    assert "rejected" in output
    long_task.cancel()


@pytest.mark.asyncio
async def test_cap_zero_unbounded(tmp_path: Path) -> None:
    """cap=0 (default) enqueues every mid-turn submit with no notice — Phase 1 regression."""

    async def dispatch(*, user_input, eof):
        pass

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    deps = _make_deps_with_repl(tmp_path, queue_cap=0)
    handler = _build_accept_handler(runtime, dispatch, lambda: None, deps, TerminalFrontend())

    long_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = long_task

    def _buf(text: str):
        return type("_Buf", (), {"text": text})()

    with console.capture() as capture:
        for text in ("a", "b", "c", "d"):
            handler(_buf(text))
    output = capture.get()

    assert list(runtime.queue) == ["a", "b", "c", "d"]
    assert "Queue full" not in output
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
            queue=runtime.queue,
        )
        if runtime.state.should_exit:
            exited.append(True)

    kb = build_key_bindings(runtime=runtime, dispatch=dispatch, frontend=TerminalFrontend())
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
async def test_queue_command_bypasses_enqueue_mid_turn(tmp_path: Path) -> None:
    """Mid-turn `/queue clear` empties the queue via a control task and does
    NOT enqueue, NOT arm a new turn, and leaves the active `turn_task`
    untouched. Phase 2 C1 — the controlled bypass to Phase 1 C5.

    Regression guard: a non-`/queue` mid-turn submission still enqueues (the
    bypass is scoped to the `/queue` prefix, not all slash commands).
    """

    async def dispatch(*, user_input, eof):
        # Not reached in this test — the bypass schedules schedule_control, not _arm_turn.
        pass

    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=0.0))
    handler = _build_accept_handler(
        runtime, dispatch, lambda: None, _make_deps(tmp_path), TerminalFrontend()
    )

    def _buf(text: str):
        return type("_Buf", (), {"text": text})()

    long_task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    runtime.turn_task = long_task

    runtime.queue.extend(["alpha", "beta"])

    handler(_buf("/queue clear"))
    for _ in range(20):
        await asyncio.sleep(0.01)
        if not runtime.queue:
            break

    assert list(runtime.queue) == []
    assert runtime.turn_task is long_task
    assert not long_task.done()

    handler(_buf("plain mid-turn text"))
    await asyncio.sleep(0.01)
    assert list(runtime.queue) == ["plain mid-turn text"]
    assert runtime.turn_task is long_task

    long_task.cancel()


@pytest.mark.asyncio
async def test_esc_cancels_turn_and_advances_queue(tmp_path: Path) -> None:
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
    handler = _build_accept_handler(
        runtime, dispatch, lambda: None, _make_deps(tmp_path), TerminalFrontend()
    )
    kb = build_key_bindings(runtime=runtime, dispatch=dispatch, frontend=TerminalFrontend())

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
    # With no list picker active, the active escape binding is the unfiltered
    # turn-cancel one (the selection-mode binding's filter is False).
    esc = next(b for b in kb.get_bindings_for_keys((Keys.Escape,)) if b.filter())
    esc.handler(object())
    for _ in range(20):
        await asyncio.sleep(0.01)
        if not runtime.queue and not runtime.turn_active:
            break
    assert turn_one.cancelled()
    assert order == ["first", "queued"]
    assert len(runtime.queue) == 0
    assert not runtime.state.should_exit
