"""Tool-call cap rejection payload.

The cap constants themselves live in ``config/tuning.py`` (foundational); this
module owns the rejection payload that references them.
"""

from typing import Literal, TypedDict

from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST


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
