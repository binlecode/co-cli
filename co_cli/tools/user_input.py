"""clarify tool — pause execution to prompt the user for a clarifying answer."""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.context.tool_approvals import QuestionRequired
from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
async def clarify(
    ctx: RunContext[CoDeps],
    question: str,
    options: list[str] | None = None,
    user_answer: str | None = None,
) -> ToolReturn:
    """Ask the user a clarifying question mid-execution and return their answer.

    Use when the current task is ambiguous and the answer will meaningfully affect
    which actions to take. Do not use for confirmation of planned steps — only for
    unresolvable ambiguity that cannot be resolved from context. Prefer making a
    reasonable default choice yourself when the decision is low-stakes.

    The user's answer is returned directly in the tool result — use it immediately,
    do not call clarify again for the same question.

    Args:
        question: The question to ask the user.
        options: Optional list of valid answers. When provided, the user must pick one.
        user_answer: Injected by the orchestrator after the user responds — do not supply this.
    """
    # Always raise on the first (unapproved) call — this covers both the expected case
    # (user_answer absent) and the LLM escape-hatch case (model pre-supplies user_answer).
    if not ctx.tool_call_approved:
        raise QuestionRequired(question=question, options=options)

    # Resumed call: user_answer injected via ToolApproved(override_args=...).
    if user_answer is None:
        return tool_error("No answer was received from the user.", ctx=ctx)

    if options and user_answer not in options:
        return tool_error(
            f"Answer {user_answer!r} is not one of the valid options: {options}",
            ctx=ctx,
        )

    return tool_output(user_answer, ctx=ctx)
