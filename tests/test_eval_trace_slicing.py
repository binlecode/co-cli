"""``record_turn`` records only the turn's own messages, given the caller's cut point.

``run_turn`` returns cumulative ``all_messages()`` (history passed in + this turn's
new messages). ``record_turn`` slices ``messages[prior_message_count:]`` so the trace
captures only the current turn. These tests drive ``record_turn`` with real
``TurnResult`` / pydantic-ai message objects (no mocks) over two real shapes:

- fresh turn (``prior_message_count=0``) — the whole list is this turn's; its tool
  call must be captured (the bug: a stale prior count sliced it to empty).
- continuation turn (``prior_message_count=len(prior)``) — only the new tail is
  recorded; the prior turn's tool call must NOT leak into this turn's trace.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals._trace import record_turn
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.agent.turn_state import TurnResult


def _skill_view_turn(skill_name: str, call_id: str, reply: str) -> list:
    """One real turn's messages: user prompt → skill_view call → return → reply."""
    return [
        ModelRequest(parts=[UserPromptPart(content=f"summarize via {skill_name}")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="skill_view",
                    args=json.dumps({"name": skill_name}),
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="skill_view", content="ok", tool_call_id=call_id)]
        ),
        ModelResponse(parts=[TextPart(reply)]),
    ]


def _selected_skill_names(trace) -> set[str]:
    """Skill names captured from the trace's skill_view tool calls."""
    names: set[str] = set()
    for call in trace.tool_calls:
        if call.tool_name != "skill_view":
            continue
        names.add(json.loads(call.args)["name"])
    return names


@pytest.mark.asyncio
async def test_fresh_history_turn_captures_its_own_tool_call(tmp_path: Path) -> None:
    """A fresh-history turn (prior_message_count=0) records its own skill_view call."""
    messages = _skill_view_turn("office", "c1", "done")
    result = TurnResult(outcome="continue", interrupted=False, messages=messages, output="done")

    _, trace = await record_turn(
        case_id="T.fresh",
        turn_index=0,
        user_input="summarize via office",
        prior_message_count=0,
        run_turn_callable=lambda: _coro(result),
        case_dir_path=tmp_path / "case_T.fresh.jsonl",
    )

    assert _selected_skill_names(trace) == {"office"}


@pytest.mark.asyncio
async def test_continuation_turn_records_only_new_messages(tmp_path: Path) -> None:
    """A continuation turn records only its new tail, not the prior turn's tool call."""
    prior = _skill_view_turn("pdf", "c0", "first")
    this_turn = _skill_view_turn("office", "c1", "second")
    cumulative = prior + this_turn
    result = TurnResult(
        outcome="continue", interrupted=False, messages=cumulative, output="second"
    )

    _, trace = await record_turn(
        case_id="T.cont",
        turn_index=1,
        user_input="summarize via office",
        prior_message_count=len(prior),
        run_turn_callable=lambda: _coro(result),
        case_dir_path=tmp_path / "case_T.cont.jsonl",
    )

    assert _selected_skill_names(trace) == {"office"}


async def _coro(value):
    """Real zero-arg async callable returning a prepared TurnResult."""
    return value
