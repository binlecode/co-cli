"""Tests for StatusSnapshot assembly and footer toolbar rendering."""

from pathlib import Path

import pytest
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps
from co_cli.display.core import StatusSnapshot, TerminalFrontend
from co_cli.display.headless import HeadlessFrontend
from co_cli.main import _build_status_snapshot
from co_cli.tools.shell_backend import ShellBackend


def _deps(**overrides) -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, **overrides)


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


def test_render_footer_toolbar_omits_ctx_when_none():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="a1b2c3d4",
            mode="idle",
            context_pct=None,
            background_task_count=0,
            approval_count=0,
        )
    )
    assert "ctx" not in frontend.render_footer_toolbar()


def test_render_footer_toolbar_omits_bg_when_zero():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="a1b2c3d4",
            mode="idle",
            context_pct=0.5,
            background_task_count=0,
            approval_count=0,
        )
    )
    assert " bg" not in frontend.render_footer_toolbar()


def test_render_footer_toolbar_omits_approval_when_zero():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="a1b2c3d4",
            mode="idle",
            context_pct=None,
            background_task_count=0,
            approval_count=0,
        )
    )
    assert "approval" not in frontend.render_footer_toolbar()


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


def test_render_footer_toolbar_empty_session_label_renders():
    frontend = TerminalFrontend()
    frontend.update_status(
        StatusSnapshot(
            session_label="—",
            mode="idle",
            context_pct=None,
            background_task_count=0,
            approval_count=0,
        )
    )
    result = frontend.render_footer_toolbar()
    assert "—" in result
    assert "idle" in result


# ── HeadlessFrontend.update_status ────────────────────────────────────────


def test_headless_update_status_stores_snapshot():
    frontend = HeadlessFrontend()
    assert frontend.last_status_snapshot is None
    snapshot = StatusSnapshot(
        session_label="abc",
        mode="active",
        context_pct=0.1,
        background_task_count=0,
        approval_count=0,
    )
    frontend.update_status(snapshot)
    assert frontend.last_status_snapshot is snapshot


# ── _build_status_snapshot ────────────────────────────────────────────────


def test_build_status_snapshot_empty_session_path_produces_dash():
    deps = _deps()
    assert deps.session.session_path == Path()
    snapshot = _build_status_snapshot(deps, "idle")
    assert snapshot.session_label == "—"


def test_build_status_snapshot_no_turn_usage_produces_none_context_pct():
    deps = _deps(model_max_ctx=200_000)
    assert deps.runtime.turn_usage is None
    snapshot = _build_status_snapshot(deps, "idle")
    assert snapshot.context_pct is None


def test_build_status_snapshot_zero_max_ctx_produces_none_context_pct():
    deps = _deps(model_max_ctx=0)
    deps.runtime.turn_usage = RunUsage(input_tokens=5_000)
    snapshot = _build_status_snapshot(deps, "idle")
    assert snapshot.context_pct is None


def test_build_status_snapshot_session_label_from_path_stem():
    deps = _deps()
    deps.session.session_path = Path("/sessions/2026-05-01_a1b2c3d4.jsonl")
    snapshot = _build_status_snapshot(deps, "idle")
    assert snapshot.session_label == "a1b2c3d4"


def test_build_status_snapshot_context_pct_from_usage():
    deps = _deps(model_max_ctx=100_000)
    deps.runtime.turn_usage = RunUsage(input_tokens=47_000)
    snapshot = _build_status_snapshot(deps, "idle")
    assert snapshot.context_pct == pytest.approx(0.47)


def test_build_status_snapshot_mode_propagated():
    deps = _deps()
    assert _build_status_snapshot(deps, "active").mode == "active"
    assert _build_status_snapshot(deps, "idle").mode == "idle"


def test_build_status_snapshot_counts_reflect_session_state():
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    deps = _deps()
    deps.session.session_approval_rules = [
        SessionApprovalRule(kind=ApprovalKindEnum.TOOL, value="shell_exec"),
        SessionApprovalRule(kind=ApprovalKindEnum.TOOL, value="file_write"),
    ]
    snapshot = _build_status_snapshot(deps, "idle")
    assert snapshot.approval_count == 2
    assert snapshot.background_task_count == 0
