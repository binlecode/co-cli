"""Defect-B: compaction snapshots the pre-drop transcript and fires a memory KICK.

The snapshot is captured at the ``compact_messages`` chokepoint — the one place
whole messages are dropped — so the daemon can review the original turns at full
fidelity before the live file is rewritten in place. PATH 1 (strip-only-fits)
drops no message and must not snapshot; the once-per-compaction guard suppresses
the no-progress escalation re-entry.
"""

from __future__ import annotations

import json

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.context.compaction import compact_messages, recover_overflow_history
from co_cli.deps import CoDeps, CoSessionState
from co_cli.session.persistence import load_transcript
from co_cli.tools.shell_backend import ShellBackend

_UNIQUE_FACT = "the deploy key rotates every 90 days"


def _review_config():
    return SETTINGS.model_copy(
        update={"skills": SETTINGS.skills.model_copy(update={"review_enabled": True})}
    )


def _make_ctx(
    tmp_path, *, model_max_context_tokens: int = 2000, with_model: bool = True
) -> RunContext:
    deps = CoDeps(shell=ShellBackend(), config=_review_config(), session=CoSessionState())
    deps.model_max_context_tokens = model_max_context_tokens
    deps.model = object() if with_model else None
    deps.session.session_path = tmp_path / "sess-abc123.jsonl"
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _patch_dirs(monkeypatch, tmp_path):
    snapshots = tmp_path / "snapshots"
    queue = tmp_path / "queue"
    monkeypatch.setattr("co_cli.context.compaction.DREAM_SNAPSHOTS_DIR", snapshots)
    monkeypatch.setattr("co_cli.session.review_kick.DREAM_QUEUE_DIR", queue)
    return snapshots, queue


def _multi_turn_history() -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content=f"remember: {_UNIQUE_FACT}")]),
        ModelResponse(parts=[TextPart(content="noted")]),
        ModelRequest(parts=[UserPromptPart(content="next question")]),
        ModelResponse(parts=[TextPart(content="answer")]),
    ]


def _kicks(queue_dir):
    if not queue_dir.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(queue_dir.glob("*.json"))]


@pytest.mark.asyncio
async def test_compaction_snapshots_full_history_and_fires_one_memory_kick(monkeypatch, tmp_path):
    """compact_messages snapshots the full pre-drop history; one memory KICK points at it."""
    snapshots, queue = _patch_dirs(monkeypatch, tmp_path)
    ctx = _make_ctx(tmp_path)
    messages = _multi_turn_history()

    await compact_messages(ctx, messages, (1, 3, 2), summarize=False)

    snapshot_files = list(snapshots.glob("*.jsonl"))
    assert len(snapshot_files) == 1, "exactly one snapshot file"
    captured = load_transcript(snapshot_files[0])
    assert len(captured) == len(messages), "snapshot holds the full original message set"
    assert any(_UNIQUE_FACT in str(getattr(p, "content", "")) for m in captured for p in m.parts)

    kicks = _kicks(queue)
    assert len(kicks) == 1, "exactly one KICK (memory only, no skill)"
    assert kicks[0]["domain"] == "memory"
    assert kicks[0]["transcript_override"] == str(snapshot_files[0])


@pytest.mark.asyncio
async def test_escalation_reentry_is_suppressed(monkeypatch, tmp_path):
    """The one-shot skip flag (set around the escalation) suppresses the re-entry snapshot."""
    snapshots, queue = _patch_dirs(monkeypatch, tmp_path)
    ctx = _make_ctx(tmp_path)
    ctx.deps.runtime.skip_compaction_snapshot = True

    await compact_messages(ctx, _multi_turn_history(), (1, 3, 2), summarize=False)

    assert list(snapshots.glob("*.jsonl")) == [], "skip flag must suppress the snapshot"
    assert _kicks(queue) == [], "skip flag must suppress the KICK"


@pytest.mark.asyncio
async def test_second_in_turn_compaction_still_snapshots(monkeypatch, tmp_path):
    """A genuine later-in-turn compaction snapshots even after an earlier one committed.

    Regression guard: the old turn-scoped guard suppressed every compaction after the
    first; the one-shot flag must only suppress the escalation re-entry, so a separate
    proactive pass (compaction_applied_this_turn already True, skip flag False) snapshots.
    """
    snapshots, queue = _patch_dirs(monkeypatch, tmp_path)
    ctx = _make_ctx(tmp_path)
    ctx.deps.runtime.compaction_applied_this_turn = True
    ctx.deps.runtime.skip_compaction_snapshot = False

    await compact_messages(ctx, _multi_turn_history(), (1, 3, 2), summarize=False)

    assert len(list(snapshots.glob("*.jsonl"))) == 1, "separate compaction must still snapshot"
    assert len(_kicks(queue)) == 1, "separate compaction must still fire one memory KICK"


@pytest.mark.asyncio
async def test_strip_only_path_produces_no_snapshot(monkeypatch, tmp_path):
    """recover_overflow_history PATH 1 (strip-only-fits) drops no message → no snapshot."""
    snapshots, queue = _patch_dirs(monkeypatch, tmp_path)
    ctx = _make_ctx(tmp_path, model_max_context_tokens=2000)

    big = "x" * 5000
    messages = [
        ModelRequest(parts=[UserPromptPart(content="t1")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="file_read", args={"p": "a"}, tool_call_id="c1")]
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_read", tool_call_id="c1", content=big)]
        ),
        ModelRequest(parts=[UserPromptPart(content="pending")]),
    ]

    result = await recover_overflow_history(ctx, messages)

    assert result is not None, "strip-only-fits should recover"
    assert list(snapshots.glob("*.jsonl")) == [], (
        "PATH 1 has no reviewer-visible loss → no snapshot"
    )
    assert _kicks(queue) == [], "PATH 1 fires no review KICK"
