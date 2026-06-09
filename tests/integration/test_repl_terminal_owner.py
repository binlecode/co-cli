"""Integration smoke: a real turn streams and commits under a running single-owner Application.

Drives a genuinely running ``app.run_async()`` (via create_pipe_input + AppSession,
so run_in_terminal's suspend/restore path is real, not the no-app inline fallback)
with a real warm Ollama model. Feeds one prompt through the pipe into the
accept_handler, awaits the turn, and asserts the single-owner invariants:
committed output reaches scrollback, the in-flight buffer is empty at end, no Rich
Live surfaces exist, and run_in_terminal + invalidate while a delta is mid-render
corrupt neither the in-flight buffer nor committed scrollback (co's run_async +
turn-task divergence from hermes's thread model).

The live approval *keystroke* round-trip (y/n/a typed at the terminal) is part of
the mandatory manual tty smoke (Gate-2 #2) — Rich's Prompt.ask reads the real tty,
which cannot be driven deterministically here without mocking (forbidden). This
test validates the run_in_terminal mechanism that prompt_approval rides on with a
canned callback instead.
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path

import pytest
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.patch_stdout import patch_stdout
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._app import _ReplRuntime, build_key_bindings, build_repl_app
from co_cli.display.core import TerminalFrontend
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


def _make_agent(deps: CoDeps):
    """Build a real orchestrator agent; build_model bakes noreason_model_settings()."""
    toolset, tool_catalog = build_native_toolset()
    deps.toolset = toolset
    deps.tool_catalog = tool_catalog
    deps.model = build_model(deps.config.llm)
    return build_orchestrator(ORCHESTRATOR_SPEC, deps)


async def _wait_running(app, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not app.is_running:
        if time.monotonic() > deadline:
            raise AssertionError("app did not start running")
        await asyncio.sleep(0.02)


async def _poll_until(predicate, *, timeout: float = 3.0) -> None:
    """Poll predicate until True or timeout — for the StdoutProxy's async flush."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_repl_renders_real_turn_under_owned_application(tmp_path: Path) -> None:
    # ensure_ollama_warm is infrastructure prep — outside any asyncio.timeout.
    await ensure_ollama_warm(TEST_LLM.model)

    deps = _make_deps(tmp_path)
    # A writable session path so post-turn persistence succeeds (no error status
    # lingers in the in-flight region) — the bootstrap normally sets this.
    deps.session.session_path = tmp_path / "co-session.jsonl"
    agent = _make_agent(deps)
    completer = SlashCommandCompleter()
    frontend = TerminalFrontend()
    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=-3.0))

    async def dispatch(*, user_input, eof):
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

    captured = io.StringIO()
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=PlainTextOutput(captured)),
        patch_stdout(),
    ):
        accept_handler = _build_accept_handler(runtime, dispatch, lambda: None, deps)
        key_bindings = build_key_bindings(runtime=runtime, dispatch=dispatch)
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

            # BC3: no Rich Live surfaces exist on the single-owner frontend.
            assert not hasattr(frontend, "_live")
            assert not hasattr(frontend, "_status_live")
            assert not hasattr(frontend, "_tool_live")

            # Feed one prompt through the pipe into the accept_handler, which
            # schedules the turn task; await it under the warm-call timeout.
            pipe.send_text("Say OK\r")
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
                while runtime.turn_task is None:
                    await asyncio.sleep(0.02)
                await runtime.turn_task

            # The turn produced output (user + assistant response in history) and
            # the in-flight region is empty at end.
            assert len(runtime.state.message_history) >= 2
            assert frontend.get_inflight() == ""

            # Same-loop concurrency hazard (run_async + turn-task, not hermes's
            # thread model): with a delta mid-render, an invalidate() and a
            # run_in_terminal round-trip corrupt neither the in-flight buffer nor
            # the committed scrollback.
            frontend.on_text_delta("streaming chunk")
            inflight_mid = frontend.get_inflight()
            assert inflight_mid != ""
            app.invalidate()
            resolved = await run_in_terminal(lambda: "approved")
            assert resolved == "approved"
            assert frontend.get_inflight() == inflight_mid

            # Committing while a delta was mid-render clears the in-flight region
            # and the committed text reaches scrollback (via the StdoutProxy under
            # patch_stdout, flushed asynchronously — poll within a bounded window).
            frontend.on_text_commit("integration-marker-xyz")
            assert frontend.get_inflight() == ""
            await _poll_until(lambda: "integration-marker-xyz" in captured.getvalue())
        finally:
            app.exit()
            await run_task
