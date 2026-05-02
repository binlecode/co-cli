"""Behavioral tests for plan_compaction_boundaries and find_first_run_end.

Production path: co_cli/context/_compaction_boundaries.py
No LLM needed — pure function over message lists.
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.context._compaction_boundaries import (
    find_first_run_end,
    group_by_turn,
    plan_compaction_boundaries,
)


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def test_normal_three_turn_history_returns_valid_bounds() -> None:
    """Three-turn history yields a valid (head_end, tail_start, dropped_count) triple.

    The head-guard in the planner (start_index <= head_end → break) structurally
    prevents tail_start <= head_end, so the result is non-None whenever the history
    has at least 3 turn groups (2 drops). The budget is irrelevant to whether a
    valid boundary is found; it only determines how many groups accumulate in the tail.
    """
    messages = [
        _req("turn 1 user"),
        _resp("turn 1 model"),
        _req("turn 2 user"),
        _resp("turn 2 model"),
        _req("turn 3 user"),
        _resp("turn 3 model"),
    ]
    result = plan_compaction_boundaries(messages, budget=8000, tail_fraction=0.4)

    assert result is not None
    head_end, tail_start, dropped_count = result
    assert head_end >= 1
    assert tail_start > head_end
    assert dropped_count == tail_start - head_end


def test_returns_none_when_only_one_turn_group() -> None:
    """Single-turn history returns None — nothing to compact.

    Failure mode: planner returns a boundary on a 1-group history →
    dropped_count=0 → empty compaction loop that writes a useless marker.
    """
    messages = [
        _req("only turn"),
        _resp("only response"),
    ]
    result = plan_compaction_boundaries(messages, budget=8000, tail_fraction=0.4)

    assert result is None


def test_last_turn_group_always_retained_even_over_tail_budget() -> None:
    """Last turn group lands in tail even when its token count alone exceeds tail_budget.

    Failure mode: last user turn silently dropped from context → model loses
    the current request on the next segment.
    """
    large_content = "x" * 2000  # ~500 tokens at 4 chars/token
    # budget=100, tail_fraction=0.4 → tail_budget=40; last turn is ~500 >> 40
    messages = [
        _req("turn 1"),
        _resp("response 1"),
        _req("turn 2"),
        _resp("response 2"),
        _req(large_content),  # turn 3 — oversized last turn
        _resp("response 3"),
    ]
    result = plan_compaction_boundaries(messages, budget=100, tail_fraction=0.4)

    assert result is not None
    _, tail_start, _ = result
    # The large UserPromptPart must appear in the tail slice
    tail_messages = messages[tail_start:]
    tail_contents = [
        part.content
        for msg in tail_messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, UserPromptPart)
    ]
    assert large_content in tail_contents, (
        f"Last turn's UserPromptPart not in tail (tail_start={tail_start})"
    )


def test_group_by_turn_multi_turn() -> None:
    """group_by_turn must split a two-turn history into exactly two turn groups."""
    messages = [
        _req("turn 1"),
        _resp("turn 1 resp"),
        _req("turn 2"),
        _resp("turn 2 resp"),
    ]
    groups = group_by_turn(messages)
    assert len(groups) == 2
    assert groups[0].messages[0].parts[0].content == "turn 1"
    assert groups[1].messages[0].parts[0].content == "turn 2"


def test_find_first_run_end_anchors_at_first_text_response() -> None:
    """find_first_run_end skips tool-only responses and anchors at the first TextPart response.

    Failure mode: head anchors too early at a tool-only ModelResponse → the first
    substantive model output (TextPart) falls into the dropped middle region.
    """
    messages = [
        _req("user turn 1"),
        ModelResponse(parts=[ToolCallPart(tool_name="shell", args="{}")]),  # tool-only
        _resp("first real text response"),  # TextPart here
    ]
    idx = find_first_run_end(messages)

    assert idx == 2, (
        f"Expected index 2 (TextPart response), got {idx} — "
        "tool-only response should not be accepted as anchor"
    )
