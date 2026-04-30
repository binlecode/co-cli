"""Consolidated E2E tests for test_flow_compaction_recovery."""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.context.compaction import emergency_recover_overflow_history
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend


@pytest.mark.asyncio
async def test_emergency_recover_overflow_preserves_pending_user_turn():
    """Emergency circuit breaker must preserve the pending user request when truncating."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="t1")]),
        ModelResponse(parts=[TextPart(content="r1")]),
        ModelRequest(parts=[UserPromptPart(content="t2")]),
        ModelResponse(parts=[TextPart(content="r2")]),
        ModelRequest(parts=[UserPromptPart(content="pending request")]),
    ]
    deps = CoDeps(shell=ShellBackend(), config=make_settings(), session=CoSessionState())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    result = await emergency_recover_overflow_history(ctx, messages)
    recovered = result[0] if isinstance(result, tuple) else result
    assert len(recovered) > 0
    assert recovered[-1].parts[0].content == "pending request"
