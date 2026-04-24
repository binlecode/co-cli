"""clarify tool — pause execution to prompt the user for a clarifying answer."""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.approvals import QuestionRequired
from co_cli.tools.tool_io import tool_error, tool_output


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
async def clarify(
    ctx: RunContext[CoDeps],
    question: str,
    options: list[str] | None = None,
    user_answer: str | None = None,
) -> ToolReturn:
    """Ask the user a clarifying question mid-execution and return their answer.

    Use when:
    - the task is ambiguous and the answer will meaningfully affect which actions to take
    - there are multiple reasonable approaches with real tradeoffs and the user should choose
    - you need a missing preference, constraint, or decision that cannot be inferred from context
    - you want the user to pick between concrete options rather than forcing a guess

    Do NOT use for:
    - approval or confirmation of dangerous actions (that is handled separately)
    - low-stakes choices where a reasonable default is fine
    - questions that can be answered by reading the workspace or using other tools first

    Prefer concise multiple-choice options when the decision can be enumerated; use an
    open-ended question only when free-form input is actually needed.

    CRITICAL — one call only:
    - Call clarify exactly ONCE for a given question.
    - The tool result IS the user's answer — read it from the ToolReturnPart and
      use it immediately in your response.
    - Do NOT call clarify again after receiving the result. Do NOT pass user_answer
      yourself — it is always injected by the system and must be omitted entirely.

    Args:
        question: The question to ask the user.
        options: Optional list of valid answer strings (e.g. ["yes", "no"]). When
            provided, the user must pick one of these exact strings.
        user_answer: System-injected after the user responds. NEVER supply this
            argument — leave it out of every call you make.
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
