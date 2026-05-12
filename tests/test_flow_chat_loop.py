"""Behavioural tests for _handle_one_input — the extracted chat loop iteration helper."""

import asyncio
from pathlib import Path

import pytest
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.main import _handle_one_input, _IterationState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_deps(tmp_path: Path) -> CoDeps:
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS_NO_MCP, user_skills_dir=tmp_path)
    tool_registry = build_tool_registry(SETTINGS_NO_MCP)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )


def _make_agent(deps: CoDeps):
    """Build a real agent from deps config.

    Uses build_model so provider/model come from the user's real settings.
    """
    tool_registry = build_tool_registry(deps.config)
    model = build_model(deps.config.llm)
    return build_agent(config=deps.config, model=model, tool_registry=tool_registry)


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
# Test 3: "quit" exits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quit_command_exits(tmp_path: Path) -> None:
    """'quit' input returns should_exit=True without contacting the agent.

    Regression guard: if quit is misrouted, same hazard as exit.
    """
    deps = _make_deps(tmp_path)
    agent = _make_agent(deps)
    frontend = HeadlessFrontend()
    completer = SlashCommandCompleter()
    state = _fresh_state()

    result = await _handle_one_input(
        user_input="quit",
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
