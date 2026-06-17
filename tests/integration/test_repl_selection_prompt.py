"""Integration: the in-app list picker and free-text question surface and resolve.

Drives a genuinely running single-owner ``app.run_async()`` (create_pipe_input +
AppSession), schedules ``prompt_selection`` / ``prompt_question`` as a task, feeds
keystrokes through the pipe, and asserts they resolve via the app's own event loop
with the in-flight region cleared afterward.

This is the path that previously deadlocked: the ``/resume`` picker and the clarify
question used to suspend the owned app via ``run_in_terminal`` (the picker did raw
termios reads). The keystroke round-trip now rides the app's event loop, so it is
deterministically testable here.
"""

from __future__ import annotations

import asyncio
import io
import time

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.patch_stdout import patch_stdout

from co_cli.commands.completer import SlashCommandCompleter
from co_cli.display.app import ReplRuntime, build_key_bindings, build_repl_app
from co_cli.display.core import QuestionPrompt, TerminalFrontend
from co_cli.main import IterationState, _build_accept_handler


async def _wait_running(app, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not app.is_running:
        if time.monotonic() > deadline:
            raise AssertionError("app did not start running")
        await asyncio.sleep(0.02)


async def _drive(coro_factory, predicate, keystrokes: list[str]):
    frontend = TerminalFrontend()
    runtime = ReplRuntime(state=IterationState(message_history=[], last_interrupt_time=-3.0))

    async def dispatch(*, user_input, eof):
        return None

    captured = io.StringIO()
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=PlainTextOutput(captured)),
        patch_stdout(),
    ):
        accept_handler = _build_accept_handler(runtime, dispatch, lambda: None, None, frontend)
        key_bindings = build_key_bindings(runtime=runtime, dispatch=dispatch, frontend=frontend)
        app = build_repl_app(
            frontend=frontend,
            completer=SlashCommandCompleter(),
            history=InMemoryHistory(),
            accept_handler=accept_handler,
            key_bindings=key_bindings,
        )
        frontend.bind_app(app)

        run_task = asyncio.ensure_future(app.run_async())
        try:
            await _wait_running(app)
            prompt_task = asyncio.ensure_future(coro_factory(frontend))

            async with asyncio.timeout(5):
                while not predicate(frontend):
                    await asyncio.sleep(0.02)
            assert frontend.get_inflight() != ""

            for key in keystrokes:
                pipe.send_text(key)
            async with asyncio.timeout(5):
                result = await prompt_task

            assert frontend.get_inflight() == ""
            return result
        finally:
            app.exit()
            await run_task


_ITEMS = ["alpha", "bravo", "charlie"]


@pytest.mark.asyncio
async def test_selection_enter_picks_first() -> None:
    result = await _drive(
        lambda f: f.prompt_selection(_ITEMS, title="Pick"),
        lambda f: f.selection_active,
        ["\r"],
    )
    assert result == "alpha"


@pytest.mark.asyncio
async def test_selection_down_navigates_then_selects() -> None:
    result = await _drive(
        lambda f: f.prompt_selection(_ITEMS, title="Pick"),
        lambda f: f.selection_active,
        ["\x1b[B", "\x1b[B", "\r"],
    )
    assert result == "charlie"


@pytest.mark.asyncio
async def test_selection_q_cancels() -> None:
    result = await _drive(
        lambda f: f.prompt_selection(_ITEMS, title="Pick"),
        lambda f: f.selection_active,
        ["q"],
    )
    assert result is None


@pytest.mark.asyncio
async def test_free_text_question_resolves_typed_answer() -> None:
    result = await _drive(
        lambda f: f.prompt_question(QuestionPrompt(question="Name?", options=None)),
        lambda f: f.question_active,
        ["hello\r"],
    )
    assert result == "hello"


@pytest.mark.asyncio
async def test_single_select_question_delegates_to_picker() -> None:
    result = await _drive(
        lambda f: f.prompt_question(QuestionPrompt(question="Pick one", options=_ITEMS)),
        lambda f: f.selection_active,
        ["\x1b[B", "\r"],
    )
    assert result == "bravo"
