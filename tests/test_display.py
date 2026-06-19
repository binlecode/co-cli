"""Tests for StatusSnapshot assembly and footer toolbar rendering."""

import asyncio
from collections import deque

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps
from co_cli.display.core import StatusSnapshot, TerminalFrontend
from co_cli.main import _build_status_snapshot
from co_cli.tools.shell_backend import ShellBackend


def _deps(**overrides) -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, **overrides)


# ── build_repl_app (Application scaffold) ──────────────────────────────────


@pytest.mark.asyncio
async def test_repl_app_builds():
    from prompt_toolkit.application.current import create_app_session
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.output import DummyOutput

    from co_cli.commands.completer import SlashCommandCompleter
    from co_cli.display.app import ReplRuntime, build_key_bindings, build_repl_app
    from co_cli.main import IterationState

    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="sess1234",
            mode="idle",
            context_pct=None,
            background_task_count=0,
            approval_count=0,
        )
    )

    dispatched: list[tuple] = []

    async def dispatch(*, user_input, eof):
        dispatched.append((user_input, eof))

    runtime = ReplRuntime(state=IterationState(message_history=[], last_interrupt_time=0.0))
    kb = build_key_bindings(runtime=runtime, dispatch=dispatch, frontend=frontend)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_repl_app(
            frontend=frontend,
            completer=SlashCommandCompleter(),
            history=InMemoryHistory(),
            accept_handler=lambda buf: False,
            key_bindings=kb,
        )

        # The toolbar window's text callable invokes frontend.render_footer_toolbar.
        children = app.layout.container.children
        toolbar_text = children[-1].content.text()
        assert "sess1234" in toolbar_text

        cc = kb.get_bindings_for_keys((Keys.ControlC,))[0]

        # Idle c-c dispatches to _handle_one_input with user_input=None.
        cc.handler(object())
        await asyncio.sleep(0.01)
        assert dispatched == [(None, False)]

        # c-c while a turn task is active is exit-only — it does NOT cancel the
        # turn (interrupt moved to Esc), just routes the double-press signal.
        turn_task = asyncio.ensure_future(asyncio.sleep(10))
        await asyncio.sleep(0)
        runtime.turn_task = turn_task
        cc.handler(object())
        await asyncio.sleep(0.01)
        assert not turn_task.cancelled()
        assert dispatched[-1] == (None, False)

        # Esc cancels the active turn (the new interrupt key). With no list picker
        # active, prompt_toolkit dispatches the escape binding whose filter holds —
        # the unfiltered turn-cancel one, not the selection-mode binding.
        esc = next(b for b in kb.get_bindings_for_keys((Keys.Escape,)) if b.filter())
        esc.handler(object())
        await asyncio.sleep(0.01)
        assert turn_task.cancelled()


# ── TerminalFrontend in-flight streaming buffer ────────────────────────────


def _capture_committed():
    """An (context_manager, get_text) pair capturing print_formatted_text output.

    Routes committed scrollback output through a PlainTextOutput-backed app session
    so it can be asserted without a real terminal.
    """
    import io

    from prompt_toolkit.application.current import create_app_session
    from prompt_toolkit.output.plain_text import PlainTextOutput

    buffer = io.StringIO()
    return create_app_session(output=PlainTextOutput(buffer)), buffer


def test_inflight_set_on_text_delta_and_cleared_on_commit():
    frontend = TerminalFrontend()
    frontend.on_text_delta("hello world")
    assert "hello world" in frontend.get_inflight()

    session, buffer = _capture_committed()
    with session:
        frontend.on_text_commit("hello world")
    assert frontend.get_inflight() == ""
    # Text-commit path commits its content to scrollback.
    assert "hello world" in buffer.getvalue()


def test_thinking_commit_erases_inflight_and_commits_final():
    frontend = TerminalFrontend()
    frontend.on_thinking_delta("partial reasoning text")
    assert "partial reasoning text" in frontend.get_inflight()

    session, buffer = _capture_committed()
    with session:
        frontend.on_thinking_commit("final reasoning text")
    # Transient parity: the in-flight thinking region is erased on commit.
    assert frontend.get_inflight() == ""
    out = buffer.getvalue()
    assert "final reasoning text" in out
    # The transient partial is discarded, not committed — unlike the text-commit path.
    assert "partial reasoning text" not in out


class _StubApp:
    """Minimal owned-app stand-in so in-flight surfaces take the live region path
    (on_status prints directly only when no app is bound)."""

    def invalidate(self) -> None:
        pass


def _run_thinking_segment(mode: str):
    """Drive a StreamRenderer through a thinking→text segment, capturing scrollback.

    Returns the committed scrollback text. `time.sleep` between deltas defeats the
    20 FPS throttle so the thinking delta actually renders.
    """
    import time

    from co_cli.display.stream_renderer import StreamRenderer

    frontend = TerminalFrontend()
    frontend._app = _StubApp()
    renderer = StreamRenderer(frontend, reasoning_display=mode)

    session, buffer = _capture_committed()
    with session:
        renderer.append_thinking("the model is weighing options ")
        time.sleep(_RENDER_INTERVAL_S)
        renderer.append_thinking("and reaching a conclusion")
        renderer.append_text("the real answer")
        renderer.finish()
    return buffer.getvalue()


_RENDER_INTERVAL_S = 0.06


def test_collapsed_mode_commits_timer_header_not_body():
    out = _run_thinking_segment("collapsed")
    # Durable timer summary lands in scrollback; raw reasoning body never does.
    assert "Thought for" in out
    assert "weighing options" not in out
    assert "the real answer" in out


def test_full_mode_commits_body_and_timer():
    out = _run_thinking_segment("full")
    assert "Thought for" in out
    assert "weighing options" in out
    assert "the real answer" in out


def test_off_mode_emits_no_reasoning_surface():
    out = _run_thinking_segment("off")
    assert "Thought for" not in out
    assert "weighing options" not in out
    assert "the real answer" in out


class _HeaderCapture:
    """Minimal frontend capturing live thinking-header repaints."""

    def __init__(self) -> None:
        self.headers: list[str] = []

    def on_thinking_delta(self, accumulated: str) -> None:
        self.headers.append(accumulated)

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass


@pytest.mark.asyncio
async def test_thinking_counter_repaints_without_new_deltas(monkeypatch):
    """The live header advances on a wall-clock ticker — repaints keep coming after a
    single delta with no further thinking tokens, and stop once the segment closes."""
    from co_cli.display import stream_renderer

    monkeypatch.setattr(stream_renderer, "_TICK_INTERVAL", 0.02)
    frontend = _HeaderCapture()
    renderer = stream_renderer.StreamRenderer(frontend, reasoning_display="collapsed")

    renderer.append_thinking("one reasoning chunk")
    await asyncio.sleep(0.1)
    repaints_during = len(frontend.headers)
    renderer.close()
    await asyncio.sleep(0.1)
    repaints_after = len(frontend.headers)

    # Ticked several times off one delta (the bug was: only one repaint, at delta time).
    assert repaints_during >= 3
    # All repaints are the live header; none happened after close (ticker stopped).
    assert all(h.startswith("Thinking…") for h in frontend.headers)
    assert repaints_after == repaints_during


# ── TerminalFrontend tool-exec timing ──────────────────────────────────────


def test_tool_complete_commits_duration_line_for_non_string_result():
    """Every finished tool commits a duration line, even when the result is
    structured/non-string (so no result Panel renders)."""
    frontend = TerminalFrontend()
    frontend._app = _StubApp()
    frontend.on_tool_start("t1", "memory_search", 'memory_search "x"')

    session, buffer = _capture_committed()
    with session:
        frontend.on_tool_complete("t1", {"hits": []})
    out = buffer.getvalue()
    # Committed completion line carries the stable label and an elapsed suffix,
    # independent of the (non-string) result payload.
    assert 'memory_search "x"' in out
    assert "s)" in out


def test_inflight_tool_label_elapsed_advances_across_refreshes(monkeypatch):
    """The in-flight tool label's elapsed seconds increase as wall-clock advances,
    with no on_tool_progress call (the ticker drives _refresh_tool_inflight)."""
    from co_cli.display import core

    clock = {"now": 100.0}
    monkeypatch.setattr(core.time, "monotonic", lambda: clock["now"])

    frontend = TerminalFrontend()
    frontend._app = _StubApp()
    frontend.on_tool_start("t1", "shell", "shell ls")
    assert "(0s)" in frontend.get_inflight()

    clock["now"] = 102.0
    frontend._refresh_tool_inflight()
    assert "(2s)" in frontend.get_inflight()

    clock["now"] = 105.0
    frontend._refresh_tool_inflight()
    assert "(5s)" in frontend.get_inflight()


def test_tool_ticker_is_noop_without_running_loop():
    """With no running event loop (sync caller), on_tool_start renders the elapsed
    label without error — the timer degrades to refresh-driven updates, no ticker crash."""
    frontend = TerminalFrontend()
    frontend._app = _StubApp()
    frontend.on_tool_start("t1", "shell", "shell ls")
    # In-flight label still renders with an elapsed suffix despite no ticker.
    assert "shell ls" in frontend.get_inflight()
    assert "s)" in frontend.get_inflight()


@pytest.mark.asyncio
async def test_tool_ticker_repaints_then_stops_on_complete(monkeypatch):
    """Under a running loop the ticker repaints the in-flight panel on its cadence
    while a tool runs, and stops once the tool completes."""
    from co_cli.display import core

    monkeypatch.setattr(core, "_TOOL_TICK_INTERVAL", 0.02)

    repaints: list[str] = []

    class _RecordingApp:
        def invalidate(self) -> None:
            repaints.append(self_frontend.get_inflight())

    frontend = TerminalFrontend()
    self_frontend = frontend
    frontend._app = _RecordingApp()

    frontend.on_tool_start("t1", "shell", "shell ls")
    await asyncio.sleep(0.1)
    repaints_during = len(repaints)

    session, _ = _capture_committed()
    with session:
        frontend.on_tool_complete("t1", "done")
    await asyncio.sleep(0.1)
    repaints_after_complete = len(repaints)

    # Ticked several times while in flight; ticker stopped after completion.
    assert repaints_during >= 3
    assert len(repaints) == repaints_after_complete
    assert frontend._tool_ticker_task is None


@pytest.mark.asyncio
async def test_waiting_ticker_repaints_then_hands_off_to_thinking(monkeypatch):
    """The pre-response waiting indicator ticks while no stream byte has arrived,
    then is superseded by the reasoning header at the first thinking delta."""
    from co_cli.display import core

    monkeypatch.setattr(core, "_WAITING_TICK_INTERVAL", 0.02)

    repaints: list[str] = []

    class _RecordingApp:
        def invalidate(self) -> None:
            repaints.append(self_frontend.get_inflight())

    frontend = TerminalFrontend()
    self_frontend = frontend
    frontend._app = _RecordingApp()

    frontend.begin_waiting()
    await asyncio.sleep(0.1)
    waiting_repaints = len(repaints)

    assert "Waiting…" in frontend.get_inflight()
    assert waiting_repaints >= 3

    frontend.on_thinking_delta("Thinking… 0s\n\nreasoning body")
    await asyncio.sleep(0.1)

    assert "Waiting…" not in frontend.get_inflight()
    assert "Thinking…" in frontend.get_inflight()
    assert frontend._waiting_ticker_task is None


def test_status_persists_to_scrollback_when_superseded_by_text():
    frontend = TerminalFrontend()
    frontend._app = _StubApp()
    frontend.on_status("Co is thinking...")

    session, buffer = _capture_committed()
    with session:
        frontend.on_text_delta("the real answer")
    # Generic status retains transient=False parity — committed on supersession.
    assert "Co is thinking..." in buffer.getvalue()


# ── TerminalFrontend.render_footer_toolbar ─────────────────────────────────


def test_render_footer_toolbar_no_snapshot_returns_empty():
    assert TerminalFrontend().render_footer_toolbar() == ""


def test_render_footer_toolbar_all_fields():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="a1b2c3d4",
            mode="idle",
            context_pct=0.47,
            background_task_count=2,
            approval_count=1,
        )
    )
    result = frontend.render_footer_toolbar()
    assert "a1b2c3d4" in result
    assert "idle" in result
    assert "ctx 47%" in result
    assert "2 bg" in result
    assert "1 approval" in result
    assert " · " in result


def test_render_footer_toolbar_plural_approvals():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="a1b2c3d4",
            mode="idle",
            context_pct=None,
            background_task_count=0,
            approval_count=3,
        )
    )
    assert "3 approvals" in frontend.render_footer_toolbar()


def test_status_toolbar_renders_queue_depth():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="a1b2c3d4",
            mode="active",
            context_pct=0.5,
            background_task_count=0,
            approval_count=0,
            queue_depth=3,
            queue_head_preview="fix the parser bug",
        )
    )
    result = frontend.render_footer_toolbar()
    assert '3 queued: "fix the parser bug"' in result
    assert result.index("active") < result.index("3 queued") < result.index("ctx")


def test_update_status_invalidates():
    class _StubApp:
        def __init__(self) -> None:
            self.invalidate_count = 0

        def invalidate(self) -> None:
            self.invalidate_count += 1

    snapshot = StatusSnapshot(
        session_label="abc",
        mode="idle",
        context_pct=None,
        background_task_count=0,
        approval_count=0,
    )

    # Bound app: update_status triggers exactly one invalidate.
    frontend = TerminalFrontend()
    stub = _StubApp()
    frontend.bind_app(stub)
    frontend.update_status(snapshot)
    assert stub.invalidate_count == 1

    # No app bound: update_status is a silent no-op (no raise).
    TerminalFrontend().update_status(snapshot)


# ── _build_status_snapshot ────────────────────────────────────────────────


def test_build_status_snapshot_no_title_shows_new_session_placeholder():
    deps = _deps()
    assert deps.session.session_title is None
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.session_label == "(new session)"


def test_build_status_snapshot_no_estimate_reports_static_floor_baseline():
    deps = _deps(model_max_context_tokens=200_000, static_floor_tokens=10_000)
    assert deps.runtime.current_request_tokens_estimate is None
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.context_pct == pytest.approx(0.05)


def test_build_status_snapshot_zero_max_context_tokens_produces_none_context_pct():
    deps = _deps(model_max_context_tokens=0)
    deps.runtime.current_request_tokens_estimate = 5_000
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.context_pct is None


def test_build_status_snapshot_session_label_from_title():
    deps = _deps()
    deps.session.session_title = "fix the resume picker"
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.session_label == "fix the resume picker"


def test_build_status_snapshot_long_title_truncated_with_ellipsis():
    deps = _deps()
    deps.session.session_title = "x" * 60
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.session_label.endswith("…")
    assert len(snapshot.session_label) == 30


def test_build_status_snapshot_context_pct_from_realtime_estimate():
    deps = _deps(model_max_context_tokens=100_000)
    deps.runtime.current_request_tokens_estimate = 47_000
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.context_pct == pytest.approx(0.47)


def test_build_status_snapshot_queue_head_preview_populated_when_non_empty():
    deps = _deps()
    snapshot = _build_status_snapshot(deps, "active", deque(["fix the parser bug", "second"]))
    assert snapshot.queue_depth == 2
    assert snapshot.queue_head_preview == "fix the parser bug"


def test_build_status_snapshot_queue_head_preview_truncated_past_budget():
    deps = _deps()
    long_head = "fix the parser bug urgently — it's blocking ship"
    snapshot = _build_status_snapshot(deps, "active", deque([long_head]))
    assert snapshot.queue_depth == 1
    assert snapshot.queue_head_preview is not None
    assert snapshot.queue_head_preview.endswith("…")
    assert len(snapshot.queue_head_preview) <= 30


def test_build_status_snapshot_queue_head_preview_none_when_empty():
    deps = _deps()
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.queue_depth == 0
    assert snapshot.queue_head_preview is None


def test_build_status_snapshot_counts_reflect_session_state():
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    deps = _deps()
    deps.session.session_approval_rules = [
        SessionApprovalRule(kind=ApprovalKindEnum.TOOL, value="shell_exec"),
        SessionApprovalRule(kind=ApprovalKindEnum.TOOL, value="file_write"),
    ]
    snapshot = _build_status_snapshot(deps, "idle", deque())
    assert snapshot.approval_count == 2
    assert snapshot.background_task_count == 0
