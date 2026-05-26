"""TokenTrackingCapability — tracks latest provider-reported input_tokens on runtime.

Writes ``runtime.last_reported_input_tokens`` from each ``ModelResponse`` whose
``usage.input_tokens > 0``. ``commit_compaction`` overwrites the same field with
a local post-compaction estimate so the next trigger pass sees the compacted
size instead of the stale pre-compaction value.

Order-independent — does not interact with spans or other capabilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse

from co_cli.deps import CoDeps

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.models import ModelRequestContext


class TokenTrackingCapability(AbstractCapability[CoDeps]):
    """Capture provider-reported input_tokens after each model request."""

    async def after_model_request(
        self,
        ctx: RunContext[CoDeps],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        if response.usage.input_tokens > 0:
            ctx.deps.runtime.last_reported_input_tokens = response.usage.input_tokens
        return response
