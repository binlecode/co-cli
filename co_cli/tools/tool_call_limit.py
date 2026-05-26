"""Tool-call cap constant and rejection payload."""

from typing import Literal, TypedDict

MAX_TOOL_CALLS_PER_MODEL_REQUEST = 3  # small ollama models lose coherence past ~3 parallel calls per response; well within the 64K-floor tail
TOOL_CAP_HARD_STOP_CONSECUTIVE: int = 3


class MaxToolCallsExceededPayload(TypedDict):
    error: Literal["max_tool_calls_per_model_request_exceeded"]
    max: int
    issued: int
    guidance: str


def make_exceeded_payload(issued: int) -> MaxToolCallsExceededPayload:
    return MaxToolCallsExceededPayload(
        error="max_tool_calls_per_model_request_exceeded",
        max=MAX_TOOL_CALLS_PER_MODEL_REQUEST,
        issued=issued,
        guidance=(
            f"Issued {issued} tool calls in one model request; cap is {MAX_TOOL_CALLS_PER_MODEL_REQUEST}. "
            f"Pick the {MAX_TOOL_CALLS_PER_MODEL_REQUEST} most important calls and try again."
        ),
    )
