"""clarify tool — pause execution to prompt the user for clarifying answers."""

import json

from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.approvals import QuestionRequired
from co_cli.tools.tool_io import tool_error, tool_output


class ClarifyOption(BaseModel):
    label: str
    description: str = ""


class ClarifyQuestion(BaseModel):
    question: str
    options: list[ClarifyOption] | None = None
    multiple: bool = False


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
async def clarify(
    ctx: RunContext[CoDeps],
    questions: list[ClarifyQuestion],
) -> ToolReturn:
    """Ask the user one or more clarifying questions mid-execution and return their answers.

    Use when the task is ambiguous and the answer meaningfully changes which actions to
    take, or there are real tradeoffs the user should decide. Don't use for approval of
    dangerous actions (handled separately), low-stakes choices with a reasonable default,
    or anything answerable by reading the workspace first.

    Batch all related questions into one call; prefer concise multiple-choice options.

    CRITICAL — one call only: the tool result IS the user's answers (a JSON list of
    strings positionally aligned to your questions). Do NOT call clarify again after
    receiving it.

    Args:
        questions: Questions to ask. Each has question (str), optional options (label +
            optional description; user picks one, or multiple if multiple=True), and
            multiple (bool, default False).
    """
    # First (unapproved) call: pause for user input via the deferred-tool mechanism.
    if not ctx.tool_call_approved:
        raise QuestionRequired(questions=[q.model_dump() for q in questions])

    # Resumed (approved) call: answers stashed by the orchestrator in runtime state,
    # keyed by tool_call_id (see CoRuntimeState.clarify_answers). Injecting via deps
    # rather than override_args keeps the original questions args intact for validation.
    user_answers = ctx.deps.runtime.clarify_answers.get(ctx.tool_call_id)
    if user_answers is None:
        return tool_error("No answers were received from the user.", ctx=ctx)

    if len(user_answers) != len(questions):
        return tool_error(
            f"Expected {len(questions)} answers, got {len(user_answers)}.",
            ctx=ctx,
        )

    return tool_output(json.dumps(user_answers), ctx=ctx)
