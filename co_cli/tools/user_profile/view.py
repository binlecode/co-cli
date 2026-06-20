"""user_profile_view — read the always-injected user profile and its budget usage."""

import math

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.user_profile import read_user_profile
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_concurrent_safe=True,
    spill_threshold_chars=math.inf,
)
async def user_profile_view(ctx: RunContext[CoDeps]) -> ToolReturn:
    """View the current user profile (~/.co-cli/USER.md) and its budget usage.

    The profile holds who the user is and how they want to work — it is
    deterministically injected into every session. Call this before
    user_profile_write to read current content; don't overwrite blind.

    Distinct facts are separated by a § (section sign) on its own line; entries may
    be multiline. Read them as individual facts when deciding what to merge or revise.
    """
    text = read_user_profile(ctx.deps.user_profile_path)
    budget = ctx.deps.config.memory.user_profile_char_budget
    used = len(text)
    body = text if text else "(empty — no user profile saved yet)"
    display = f"{body}\n\n[{used}/{budget} chars used]"
    return tool_output(display, ctx=ctx, used=used, budget=budget)
