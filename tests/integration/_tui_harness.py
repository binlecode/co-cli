"""Render-fidelity harness — drive the real REPL Application and capture ANSI bytes.

Mirrors ``test_repl_terminal_owner.py``'s real-app drive (``create_pipe_input`` +
``create_app_session`` + ``patch_stdout`` + bounded polling) with two deliberate
changes that make terminal rendering observable:

1. Capture with ``Vt100_Output`` instead of ``PlainTextOutput`` — preserves the
   ESC byte stream, so assertions can distinguish ``\\x1b[`` (correct) from ``?[``
   (the sanitized garble that ``patch_stdout(raw=False)`` produced).
2. Force the shared module ``console`` to emit SGR (``forced_tty_console``) —
   the module console resolves its color system once at import (non-tty under
   pytest, frozen to ``None``), so both ``_force_terminal`` and ``_color_system``
   must be set for ``console.print`` markup to produce real escape sequences.

No mocks: the real ``Application``, real module ``console``, real ``patch_stdout``,
and the real ``_handle_one_input`` echo + dispatch path run. Forcing the console
color state and supplying a fixed terminal size are environment simulation (peer
to ``console._width`` pinning in ``test_flow_bootstrap_banner.py``), not mocking a
dependency under test.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import patch_stdout
from rich.color import ColorSystem
from tests._settings import SETTINGS

from co_cli.commands.completer import SlashCommandCompleter
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._app import _ReplRuntime, build_key_bindings, build_repl_app
from co_cli.display.core import TerminalFrontend, console
from co_cli.main import _build_accept_handler, _handle_one_input, _IterationState
from co_cli.tools.shell_backend import ShellBackend

_SESSION_ID = "abcd1234"


@contextmanager
def forced_tty_console() -> Iterator[None]:
    """Force the shared module ``console`` to emit SGR, restoring both attrs on exit.

    The module console (``display/core.py``) is built without a ``color_system``
    arg, so its ``_color_system`` is resolved once at import (non-tty in pytest)
    and frozen to ``None``. Flipping ``_force_terminal`` alone does not recompute
    it, so ``console.print`` still emits plain text. Both must be set. The original
    ``_color_system`` is ``None``; restoring it re-suppresses SGR so no styling
    bleeds into other tests sharing this global console.
    """
    saved_force = console._force_terminal
    saved_color = console._color_system
    console._force_terminal = True
    console._color_system = ColorSystem.TRUECOLOR
    try:
        yield
    finally:
        console._force_terminal = saved_force
        console._color_system = saved_color


def make_repl_deps(tmp_path: Path) -> CoDeps:
    """Build a minimal real ``CoDeps`` sufficient to drive ``/status`` (no LLM, no MCP).

    Mirrors the ``test_flow_status_command.py`` deps shape. ``/status`` reads only
    in-memory ``deps`` plus cheap local reads, so empty tool/skill catalogs (the
    field defaults) are fine. The harness wires the accept_handler with
    ``agent=None``: ``_handle_one_input`` routes ``/status`` through
    ``dispatch_command`` (a ``LocalOnly`` outcome) and returns before
    ``_run_foreground_turn``, the only agent consumer — so no model is needed.
    """
    session_path = tmp_path / "sessions" / f"2026-06-14T120000.000-{_SESSION_ID}.jsonl"
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(session_path=session_path),
        sessions_dir=tmp_path / "sessions",
        usage_log_path=tmp_path / "usage.jsonl",
        workspace_dir=tmp_path,
    )


async def _wait_running(app, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not app.is_running:
        if time.monotonic() > deadline:
            raise AssertionError("app did not start running")
        await asyncio.sleep(0.02)


async def _wait_armed(runtime: _ReplRuntime, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while runtime.turn_task is None:
        if time.monotonic() > deadline:
            raise AssertionError("turn task never armed after input")
        await asyncio.sleep(0.02)


async def _poll_until(predicate, *, timeout: float = 5.0) -> None:
    """Poll predicate until True or timeout — for the StdoutProxy's async flush."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("sentinel did not appear in captured output within timeout")
        await asyncio.sleep(0.05)


async def drive_repl(deps: CoDeps, keys: str, *, sentinel: str) -> str:
    """Drive the real ``build_repl_app`` through pipe-fed keys; return captured ANSI.

    Runs under ``create_pipe_input`` + ``create_app_session`` with a ``Vt100_Output``
    sink + ``patch_stdout(raw=True)`` (production-equivalent — see ``main.py``). Feeds
    ``keys + "\\r"`` into the real accept_handler → ``_handle_one_input`` (echo +
    dispatch), awaits the armed turn, then polls the captured buffer until ``sentinel``
    appears (the StdoutProxy flushes asynchronously). Always tears the app down in a
    ``finally``. Returns the full captured terminal byte stream as a string.

    Call within ``forced_tty_console()`` so styled ``console.print`` emits real SGR.
    """
    completer = SlashCommandCompleter()
    frontend = TerminalFrontend()
    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=-3.0))

    async def dispatch(*, user_input, eof):
        runtime.state = await _handle_one_input(
            user_input=user_input,
            eof=eof,
            state=runtime.state,
            deps=deps,
            agent=None,  # type: ignore[arg-type]  # /status never reaches the agent (see make_repl_deps)
            frontend=frontend,
            completer=completer,
            now=time.monotonic(),
            queue=runtime.queue,
        )

    captured = io.StringIO()
    with (
        create_pipe_input() as pipe,
        create_app_session(
            input=pipe,
            output=Vt100_Output(
                captured,
                get_size=lambda: Size(rows=24, columns=80),
                term="xterm-256color",
            ),
        ),
        patch_stdout(raw=True),
    ):
        accept_handler = _build_accept_handler(runtime, dispatch, lambda: None, deps)
        key_bindings = build_key_bindings(runtime=runtime, dispatch=dispatch, frontend=frontend)
        app = build_repl_app(
            frontend=frontend,
            completer=completer,
            history=InMemoryHistory(),
            accept_handler=accept_handler,
            key_bindings=key_bindings,
        )
        frontend.bind_app(app)

        run_task = asyncio.ensure_future(app.run_async())
        try:
            await _wait_running(app)
            pipe.send_text(keys + "\r")
            await _wait_armed(runtime)
            await runtime.turn_task
            await _poll_until(lambda: sentinel in captured.getvalue())
        finally:
            app.exit()
            await run_task

    return captured.getvalue()
