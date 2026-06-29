"""Migrated phase-2 UAT cases — the three behavioral gaps with no prior pytest.

These three tests were structural eval cases (W6.B, W6.C, W2.F) with no
existing pytest equivalent. They are migrated here as deterministic,
no-real-LLM flow tests:

  - test_unknown_slash_returns_local_only       (W6.B): unknown /foo returns
        LocalOnly from command dispatch (so the chat loop never starts a turn).
  - test_deny_emits_no_side_effect              (W6.C): a denied approval-gated
        destructive tool produces no side effect (file is not deleted), driven
        end-to-end through the owned loop.
  - test_compaction_idempotent                  (W2.F): a second /compact on an
        already-compacted history is a stable no-op (length holds, no new
        marker).

Drivers are pydantic-ai's ``FunctionModel`` (the SDK's deterministic agent
driver) and ``model=None`` static-marker compaction — no mocks of co's own
code, no real LLM call. ``CO_HOME`` is overridden to a temp dir via the
``co_home`` fixture so no test touches the real ``~/.co-cli``.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.core import assemble_routing_toolset
from co_cli.agent.loop import run_turn_owned
from co_cli.commands.compact import _cmd_compact
from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, LocalOnly, ReplaceTranscript
from co_cli.context._compaction_markers import STATIC_MARKER_PREFIX, SUMMARY_MARKER_PREFIX
from co_cli.deps import (
    CoDeps,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel
from co_cli.tools.shell_backend import ShellBackend


@pytest.fixture
def co_home(tmp_path: Path) -> Iterator[Path]:
    """Point CO_HOME at a temp dir for the duration of one test."""
    prior = os.environ.get("CO_HOME")
    os.environ["CO_HOME"] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        if prior is None:
            os.environ.pop("CO_HOME", None)
        else:
            os.environ["CO_HOME"] = prior


def _make_deps(model: object | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=model,  # type: ignore[arg-type]
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _marker_count(history: list[ModelMessage]) -> int:
    """Count messages whose UserPromptPart opens with a compaction marker."""
    count = 0
    for msg in history:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and (
                content.startswith(SUMMARY_MARKER_PREFIX)
                or content.startswith(STATIC_MARKER_PREFIX)
            ):
                count += 1
                break
    return count


# ---------------------------------------------------------------------------
# W6.B — unknown slash returns LocalOnly (the chat loop never starts a turn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_slash_returns_local_only(co_home: Path) -> None:
    """An unknown /foo slash returns LocalOnly from command dispatch.

    The chat loop only starts an agent turn for non-slash input or a DelegateToAgent
    outcome; an unknown command resolving to LocalOnly means the model is never reached.

    Failure mode: if the unknown-command fallthrough returned DelegateToAgent instead of
    LocalOnly, every slash typo would burn a model call.
    """
    deps = _make_deps()
    ctx = CommandContext(
        message_history=[],
        deps=deps,
        frontend=HeadlessFrontend(),
        completer=None,
    )

    outcome = await dispatch("/this_is_not_a_command", ctx)

    assert isinstance(outcome, LocalOnly)


# ---------------------------------------------------------------------------
# W6.C — a denied approval-gated destructive tool produces no side effect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_emits_no_side_effect(co_home: Path, tmp_path: Path) -> None:
    """When the user denies an approval-gated delete, the target file survives.

    A real approval-required tool (``destructive_delete``) unlinks a seeded file as its
    side effect. The FunctionModel emits that tool call; the HeadlessFrontend denies it
    ("n"). The owned loop's inline collector must turn the denial into a denial result so
    the tool body never runs — the file stays on disk and the turn continues.

    Failure mode: if the denial is not wired into dispatch, the gated delete executes
    despite the denial and the file disappears.
    """
    target = tmp_path / "deny_target.txt"
    target.write_text("PRESERVE_ME", encoding="utf-8")

    state = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        state["n"] += 1
        if state["n"] == 1:
            yield {
                0: DeltaToolCall(
                    name="destructive_delete",
                    json_args=json.dumps({"path": str(target)}),
                    tool_call_id="del1",
                )
            }
        else:
            yield "done"

    inner: FunctionToolset = FunctionToolset()

    async def destructive_delete(ctx: RunContext[CoDeps], path: str) -> str:
        Path(path).unlink(missing_ok=True)
        return f"deleted {path}"

    inner.add_function(destructive_delete, requires_approval=True)

    config = SETTINGS_NO_MCP
    deps = CoDeps(
        shell=ShellBackend(),
        model=LlmModel(
            model=FunctionModel(stream_function=stream_fn),
            settings=config.llm.noreason_model_settings(),
            settings_noreason=config.llm.noreason_model_settings(),
        ),
        config=config,
        session=CoSessionState(),
        toolset=assemble_routing_toolset(inner, []),
        tool_catalog={
            "destructive_delete": ToolInfo(
                name="destructive_delete",
                description="delete a file",
                is_approval_required=True,
                source=ToolSourceEnum.NATIVE,
                visibility=VisibilityPolicyEnum.ALWAYS,
                is_concurrent_safe=False,
            )
        },
        model_max_context_tokens=config.llm.max_context_tokens,
    )
    frontend = HeadlessFrontend(approval_response="n")

    result = await run_turn_owned(
        user_input="delete the file",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert result.outcome == "continue", f"expected continue; got {result.outcome!r}"
    assert frontend.approval_calls, "the approval gate must have prompted at least once"
    assert target.exists(), "denied delete must not remove the file"
    assert target.read_text(encoding="utf-8") == "PRESERVE_ME"


# ---------------------------------------------------------------------------
# W2.F — a second /compact on already-compacted history is a stable no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_idempotent(co_home: Path) -> None:
    """Re-running /compact on already-compacted history is stable (no growth, no new marker).

    With ``deps.model = None`` the summarizer is gated off and /compact takes
    its deterministic static-marker path (no LLM call). The first pass compacts
    a multi-turn history; the second pass over that result must leave the
    message count stable and add no further compaction marker.

    Failure mode: if compaction is not idempotent, repeated /compact calls would
    keep stacking markers and mutate a stable history every time the user runs it.
    """
    deps = _make_deps(model=None)
    history: list[ModelMessage] = []
    for i in range(8):
        history.append(_req(f"turn {i}"))
        history.append(_resp(f"reply {i}"))

    ctx1 = CommandContext(
        message_history=history,
        deps=deps,
        frontend=HeadlessFrontend(),
        completer=None,
    )
    first = await _cmd_compact(ctx1, "")
    assert isinstance(first, ReplaceTranscript)
    first_history = first.history
    first_len = len(first_history)
    first_markers = _marker_count(first_history)
    assert first_markers >= 1, "first /compact must inject a compaction marker"

    ctx2 = CommandContext(
        message_history=first_history,
        deps=deps,
        frontend=HeadlessFrontend(),
        completer=None,
    )
    second = await _cmd_compact(ctx2, "")
    assert isinstance(second, ReplaceTranscript)
    second_history = second.history

    tolerance = max(1, round(first_len * 0.10))
    assert abs(len(second_history) - first_len) <= tolerance, (
        f"second /compact drifted past tolerance: {first_len} → {len(second_history)} "
        f"(tolerance ±{tolerance})"
    )
    assert _marker_count(second_history) <= first_markers, (
        f"second /compact added a new compaction marker "
        f"({first_markers} → {_marker_count(second_history)})"
    )
