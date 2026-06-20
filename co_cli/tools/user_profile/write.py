"""user_profile_write — wholesale rewrite of the always-injected user profile."""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import ApprovalKindEnum, ApprovalSubject, CoDeps, VisibilityPolicyEnum
from co_cli.memory.user_profile import UserProfileBudgetError, write_user_profile
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output


def _write_subject(args: dict) -> ApprovalSubject:
    """Approval subject for the single-file profile overwrite (no name arg)."""
    return ApprovalSubject(
        tool_name="user_profile_write",
        kind=ApprovalKindEnum.TOOL,
        value="tool:user_profile_write",
        display="user_profile_write(...)",
        can_remember=True,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_approval_required=True,
    approval_subject_fn=_write_subject,
)
async def user_profile_write(ctx: RunContext[CoDeps], content: str) -> ToolReturn:
    """Replace the whole user profile (~/.co-cli/USER.md).

    Wholesale rewrite, not a targeted edit: call user_profile_view first, merge
    the new fact into the existing profile, then write the full text back. Keep it
    under the character budget — consolidate rather than append indefinitely.

    Separate distinct facts with a § (section sign) on its own line; entries may be
    multiline. This is a readability convention, not enforced structure — it lets you
    and later rewrites see and revise one fact at a time without disturbing the rest.

    Use for who the user is and how they want to work (preferences, working style,
    persona). Standing rules, fetched articles, and free-form notes go to
    memory_create instead.

    Args:
        content: Full replacement profile text (markdown). Replaces the entire file.
    """
    budget = ctx.deps.config.memory.user_profile_char_budget
    try:
        write_user_profile(ctx.deps.user_profile_path, content, char_budget=budget)
    except UserProfileBudgetError as e:
        return tool_error(str(e), ctx=ctx)
    return tool_output(
        f"User profile updated ({len(content)}/{budget} chars).",
        ctx=ctx,
        used=len(content),
        budget=budget,
    )
