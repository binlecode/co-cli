"""Per-turn instruction builder functions for the orchestrator agent."""

from datetime import datetime

from pydantic_ai import RunContext

from co_cli.deps import CoDeps


def current_time_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: inject current date and time for accuracy without freezing it in cached Block 0."""
    return datetime.now().strftime("Current time: %A, %B %d, %Y %I:%M %p")


def safety_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: inject doom loop / shell reflection warnings when condition is active."""
    from co_cli.context.prompt_text import safety_prompt_text

    return safety_prompt_text(ctx)
