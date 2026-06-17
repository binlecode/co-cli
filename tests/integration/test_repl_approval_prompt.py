"""Integration: the in-app approval prompt surfaces and resolves a keystroke.

Drives a genuinely running single-owner ``app.run_async()`` (create_pipe_input +
AppSession), schedules ``prompt_approval`` as a task, feeds a single keystroke
through the pipe, and asserts the prompt resolves to the typed choice with the
in-flight region cleared afterward.

This is the path that previously deadlocked: ``prompt_approval`` used to suspend
the owned app via ``run_in_terminal`` + ``Rich.Prompt.ask``, which blocked on a
terminal cursor-position handshake and never surfaced the panel. The keystroke
round-trip now rides the app's own event loop, so it is deterministically
testable here (it was previously punted to manual tty smoke).
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
from co_cli.deps import ApprovalSubject
from co_cli.display.app import ReplRuntime, build_key_bindings, build_repl_app
from co_cli.display.core import TerminalFrontend
from co_cli.main import _build_accept_handler, _IterationState


async def _wait_running(app, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not app.is_running:
        if time.monotonic() > deadline:
            raise AssertionError("app did not start running")
        await asyncio.sleep(0.02)


def _subject() -> ApprovalSubject:
    return ApprovalSubject(
        tool_name="shell_exec",
        kind="shell_exec",
        value="which yt",
        display="Run shell command: which yt",
        can_remember=True,
        preview="which yt youtube ytm",
    )


async def _drive_approval(keystroke: str) -> str:
    frontend = TerminalFrontend()
    runtime = ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=-3.0))

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
            approval_task = asyncio.ensure_future(frontend.prompt_approval(_subject()))

            # Wait until the prompt is genuinely awaiting input (observed state,
            # not a timing guess), then the panel must be live in the in-flight region.
            async with asyncio.timeout(5):
                while not frontend.prompt_active:
                    await asyncio.sleep(0.02)
            assert "Run shell command" in frontend.get_inflight()

            pipe.send_text(keystroke)
            async with asyncio.timeout(5):
                choice = await approval_task

            # Region clears once resolved — no lingering prompt panel.
            assert frontend.get_inflight() == ""
            return choice
        finally:
            app.exit()
            await run_task


@pytest.mark.asyncio
async def test_approval_prompt_resolves_yes() -> None:
    assert await _drive_approval("y") == "y"


@pytest.mark.asyncio
async def test_approval_prompt_resolves_always() -> None:
    assert await _drive_approval("a") == "a"


@pytest.mark.asyncio
async def test_approval_prompt_enter_defaults_to_deny() -> None:
    assert await _drive_approval("\r") == "n"
