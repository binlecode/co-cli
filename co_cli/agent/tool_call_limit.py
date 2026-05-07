"""Tool-call cap constant and rejection payload."""

from typing import Literal, TypedDict

MAX_TOOL_CALLS_PER_MODEL_TURN = 6  # 6 non-spilling calls fit the 64K-floor tail; see Sizing


class MaxToolCallsExceededPayload(TypedDict):
    error: Literal["max_tool_calls_per_turn_exceeded"]
    max: int
    issued: int
    guidance: str


def make_exceeded_payload(issued: int) -> MaxToolCallsExceededPayload:
    return MaxToolCallsExceededPayload(
        error="max_tool_calls_per_turn_exceeded",
        max=MAX_TOOL_CALLS_PER_MODEL_TURN,
        issued=issued,
        guidance=(
            f"Issued {issued} tool calls in one model turn; cap is {MAX_TOOL_CALLS_PER_MODEL_TURN}. "
            f"Pick the {MAX_TOOL_CALLS_PER_MODEL_TURN} most important calls and try again."
        ),
    )
