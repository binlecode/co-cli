"""clarify tool — pause execution to prompt the user for clarifying answers."""

import json

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.approvals import QuestionRequired
from co_cli.tools.tool_io import tool_error, tool_output


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
async def clarify(
    ctx: RunContext[CoDeps],
    questions: list[dict],
    user_answers: list[str] | None = None,
) -> ToolReturn:
    """Ask the user one or more clarifying questions mid-execution and return their answers.

    Use when:
    - the task is ambiguous and the answer will meaningfully affect which actions to take
    - there are multiple reasonable approaches with real tradeoffs and the user should choose
    - you need missing preferences, constraints, or decisions that cannot be inferred from context
    - you want the user to pick between concrete options rather than forcing a guess

    Do NOT use for:
    - approval or confirmation of dangerous actions (that is handled separately)
    - low-stakes choices where a reasonable default is fine
    - questions that can be answered by reading the workspace or using other tools first

    One clarify call should collect all related questions — batch them to avoid multiple
    round-trips. Prefer concise multiple-choice options when decisions can be enumerated.

    CRITICAL — one call only:
    - Call clarify exactly ONCE per batch.
    - The tool result IS the user's answers — read it from the ToolReturnPart as a JSON
      list of strings positionally aligned to your `questions` list.
    - Do NOT call clarify again after receiving the result. Do NOT pass user_answers
      yourself — it is always injected by the system and must be omitted entirely.

    Each question dict:
        question:  str — the question text
        options:   list[{label: str, description: str}] | None — constrained choices;
                   when provided the user picks one (or multiple if multiple=True)
        multiple:  bool (default False) — allow comma-joined multi-select from options

    Returns: JSON-encoded list[str] — one string per question, positionally aligned.
        Multi-select answers are comma-joined into a single string.

    Args:
        questions: List of question dicts to ask sequentially.
        user_answers: System-injected after the user responds. NEVER supply this
            argument — leave it out of every call you make.
    """
    # Always raise on the first (unapproved) call — covers both the expected case
    # (user_answers absent) and the LLM escape-hatch case (model pre-supplies answers).
    if not ctx.tool_call_approved:
        raise QuestionRequired(questions=questions)

    # Resumed call: user_answers injected via ToolApproved(override_args=...).
    if user_answers is None:
        return tool_error("No answers were received from the user.", ctx=ctx)

    if len(user_answers) != len(questions):
        return tool_error(
            f"Expected {len(questions)} answers, got {len(user_answers)}.",
            ctx=ctx,
        )

    return tool_output(json.dumps(user_answers), ctx=ctx)
