"""Per-turn instruction builder functions for the orchestrator agent."""

from datetime import datetime

from pydantic_ai import RunContext

from co_cli.deps import CoDeps


def current_time_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: inject the current date (day-only) for grounding.

    Day-only granularity is deliberate. This block sits in the system prompt ahead
    of all message history; on the Ollama/llama.cpp path the prefix cache breaks at
    the first differing token, so a minute-precision clock here changed nearly every
    turn and forced the entire growing history to be re-prefilled. Day precision keeps
    the system block byte-stable across same-day turns, extending the cached prefix
    through the system block into history. Time-of-day, if ever needed, is a tool call,
    not a prompt fact.
    """
    return datetime.now().strftime("Current date: %A, %B %d, %Y")


def safety_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: inject doom loop / shell reflection warnings when condition is active."""
    from co_cli.context.prompt_text import safety_prompt_text

    return safety_prompt_text(ctx)


WRAP_UP_TEXT = (
    "This is your last allowed step this turn — the model-request budget is about to "
    "run out, so any further tool calls will be cut off before you can answer. Do not "
    "call any more tools. Produce your final answer now from what you already have."
)
"""Final-request wrap-up nudge. Injected as a dynamic instruction (not a history
message), so the SDK recomputes it fresh each request and never replays it next turn."""


def wrap_up_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: nudge the model to finish on its last allowed request before the cap.

    The SDK enforces ``max_model_requests_per_turn`` via ``UsageLimits`` and aborts
    mid-run once ``usage.requests`` reaches the cap. ``ctx.usage.requests`` counts
    completed requests, so it reads ``limit - 1`` right before the last allowed
    request. On that request only, emit the wrap-up text so the model returns a final
    answer instead of spending its last step on tool calls (which would be
    cold-truncated to an error result). Instructions are recomputed per request and
    historical ``ModelRequest.instructions`` are ignored in the agent flow, so the
    nudge is never replayed — no strip step is needed. Inert when the cap is disabled
    (``resolve_request_limit`` → ``None``).
    """
    from co_cli.config.llm import resolve_request_limit

    limit = resolve_request_limit(ctx.deps.config.llm)
    if limit is None or ctx.usage.requests != limit - 1:
        return ""
    return WRAP_UP_TEXT


def deferred_tool_awareness_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: emit a per-tool stub (name + one-liner) for every deferred tool.

    Lists each DEFERRED tool by name and purpose so the model can load it via tool_view
    before calling it. Lives post-static so mid-session integration registration / tool
    toggles are reflected on the next turn without invalidating the static prefix.
    """
    from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt

    return build_deferred_tool_awareness_prompt(
        ctx.deps.tool_catalog, ctx.deps.runtime.revealed_tools
    )


def skill_manifest_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: render the <available_skills> manifest from the live skill index.

    Lives post-static so newly-created skills become visible to the model on the very next
    turn without process restart, and skill index mutations don't churn the static prefix.
    """
    from co_cli.skills.manifest import render_skill_manifest

    return render_skill_manifest(
        ctx.deps.skill_catalog, ctx.deps.skills_dir, ctx.deps.user_skills_dir
    )
