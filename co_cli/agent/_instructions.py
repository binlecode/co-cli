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


def tool_category_awareness_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: list deferred tool-category domains available via search_tools.

    Lives post-static so mid-session integration registration / tool toggles are reflected
    on the next turn without invalidating the static prefix.
    """
    from co_cli.tools.deferred_prompt import build_tool_category_awareness_prompt

    return build_tool_category_awareness_prompt(ctx.deps.tool_index)


def skill_manifest_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: render the <available_skills> manifest from the live skill index.

    Lives post-static so newly-created skills become visible to the model on the very next
    turn without process restart, and skill index mutations don't churn the static prefix.
    """
    from co_cli.context.manifests.skill_manifest import render_skill_manifest

    return render_skill_manifest(
        ctx.deps.skill_index, ctx.deps.skills_dir, ctx.deps.user_skills_dir
    )
