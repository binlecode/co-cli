"""Integration smoke: typed-ahead survives a real turn and drains FIFO.

Drives a genuinely running ``app.run_async()`` (via create_pipe_input + AppSession)
with a real warm Ollama model. Feeds one prompt, waits until the turn is genuinely
active (observed, not timed), then feeds a second prompt while the first is in
flight — proving it enqueues (depth 1) instead of being dropped — and asserts both
turns commit output and the queue returns to 0. This is the end-to-end proof of the
repl-input-queue feature: type-ahead is never lost during an active turn.

Two sequential production-tool-context turns run here, so the combined await uses a
doubled per-call budget (LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2) and the pytest safety
net is raised to 180s (100s wrapped + ~50s mid-suite KV-cache flush + overhead),
per tests/_timeouts.py. ensure_ollama_warm stays outside every asyncio.timeout.
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path

import pytest
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
from co_cli.main import (
    _build_accept_handler,
    _build_status_snapshot,
    _handle_one_input,
    _IterationState,
)
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


@pytest.mark.asyncio
async def test_typed_ahead_enqueues_and_drains_under_real_turn(tmp_path: Path) -> None:
    # ensure_ollama_warm is infrastructure prep — outside any asyncio.timeout.
    await ensure_ollama_warm(TEST_LLM.model)

    deps = _make_deps(tmp_path)
    # Writable session path so post-turn persistence succeeds (bootstrap sets this).
    deps.session.session_path = tmp_path / "co-session.jsonl"
    agent = _make_agent(deps)
    completer = SlashCommandCompleter()
    frontend = TerminalFrontend()
    runtime = _ReplRuntime(state=_IterationState(message_history=[], last_interrupt_time=-3.0))

    completed: list[str] = []

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
        completed.append(user_input)

    snapshots = []

    def _on_queue_status() -> None:
        snapshot = _build_status_snapshot(deps, "active", runtime.queue)
        snapshots.append(snapshot)
        frontend.update_status(snapshot)

    captured = io.StringIO()
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=PlainTextOutput(captured)),
        patch_stdout(),
    ):
        accept_handler = _build_accept_handler(runtime, dispatch, _on_queue_status, deps)
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

            # Feed the first prompt; wait until the turn is genuinely active
            # (observed runtime state, not a timing guess) before feeding more.
            pipe.send_text("Say the single word apple\r")
            async with asyncio.timeout(10):
                while not runtime.turn_active:
                    await asyncio.sleep(0.02)

            # Feed the second prompt while the first turn is in flight — it must
            # enqueue (depth 1), not drop and not interleave.
            pipe.send_text("Say the single word banana\r")
            async with asyncio.timeout(5):
                while len(runtime.queue) != 1:
                    await asyncio.sleep(0.02)
            assert snapshots[-1].queue_depth == 1

            # Both turns drain one-per-boundary under a doubled per-call budget
            # (two sequential production-tool-context calls).
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
                while len(completed) < 2:
                    await asyncio.sleep(0.05)

            # FIFO order, both committed, queue drained, in-flight clean.
            assert completed == ["Say the single word apple", "Say the single word banana"]
            assert len(runtime.state.message_history) >= 4
            assert len(runtime.queue) == 0
            assert frontend.get_inflight() == ""
        finally:
            app.exit()
            await run_task
