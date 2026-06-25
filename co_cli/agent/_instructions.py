"""Per-turn instruction builders for the orchestrator agent.

The builders take ``deps`` directly (plus explicit ``messages`` / ``request_count``
params for the two that need turn-scoped state). The owned loop calls them directly
(``co_cli/agent/preflight.py``); the graph path registers the ``*_ctx`` shims at the
bottom of this module via ``agent.instructions(...)`` (referenced from
``ORCHESTRATOR_SPEC.per_turn_instructions``), threading the same values from the
``RunContext``. Keeping one logic source with two call shapes (CD-M-2) avoids the
blanket ``lambda ctx: builder(ctx.deps)`` shim that would silently drop the wrap-up
nudge (reads ``ctx.usage.requests``) and the safety warnings (read ``ctx.messages``)
on the graph path.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic_ai import RunContext

from co_cli.deps import CoDeps

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


def current_time_prompt(deps: CoDeps) -> str:
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


def safety_prompt(deps: CoDeps, *, messages: list[ModelMessage]) -> str:
    """Per-turn: inject doom loop / shell reflection warnings when condition is active.

    Reads the message history to detect repeated tool calls / shell-error streaks, so
    ``messages`` is passed explicitly (the owned loop sources it from the turn history;
    the graph shim sources it from ``ctx.messages``).
    """
    from co_cli.context.prompt_text import safety_prompt_text

    return safety_prompt_text(deps, messages)


WRAP_UP_TEXT = (
    "This is your last allowed step this turn — the model-request budget is about to "
    "run out, so any further tool calls will be cut off before you can answer. Do not "
    "call any more tools. Produce your final answer now from what you already have."
)
"""Final-request wrap-up nudge. Injected as a dynamic instruction (not a history
message), so it is recomputed fresh each request and never replayed next turn."""


def wrap_up_prompt(deps: CoDeps, *, request_count: int) -> str:
    """Per-turn: nudge the model to finish on its last allowed request before the cap.

    The model-request cap aborts the turn once the completed-request count reaches the
    limit. ``request_count`` is the number of completed requests so far, so it reads
    ``limit - 1`` right before the last allowed request. On that request only, emit the
    wrap-up text so the model returns a final answer instead of spending its last step
    on tool calls (which would be cold-truncated to an error result). Inert when the cap
    is disabled (``resolve_request_limit`` → ``None``).

    The graph path passes ``request_count=ctx.usage.requests`` (completed requests); the
    owned loop passes its own completed-request count, which must carry the same
    "requests completed before this one" semantics so the nudge fires on the same step.
    """
    from co_cli.config.llm import resolve_request_limit

    limit = resolve_request_limit(deps.config.llm)
    if limit is None or request_count != limit - 1:
        return ""
    return WRAP_UP_TEXT


def deferred_tool_awareness_prompt(deps: CoDeps) -> str:
    """Per-turn: emit a per-tool stub (name + one-liner) for every deferred tool.

    Lists each DEFERRED tool by name and purpose so the model can load it via tool_view
    before calling it. Lives post-static so mid-session integration registration / tool
    toggles are reflected on the next turn without invalidating the static prefix.
    """
    from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt

    return build_deferred_tool_awareness_prompt(deps.tool_catalog, deps.runtime.revealed_tools)


def skill_manifest_prompt(deps: CoDeps) -> str:
    """Per-turn: render the <available_skills> manifest from the live skill index.

    Lives post-static so newly-created skills become visible to the model on the very next
    turn without process restart, and skill index mutations don't churn the static prefix.
    """
    from co_cli.skills.manifest import render_skill_manifest

    return render_skill_manifest(deps.skill_catalog, deps.skills_dir, deps.user_skills_dir)


# ---------------------------------------------------------------------------
# Graph-path ctx shims — thread RunContext-derived values into the deps builders.
# Referenced from ORCHESTRATOR_SPEC.per_turn_instructions and registered via
# agent.instructions(...) by build_orchestrator. Deleted with the graph at Phase 5;
# the owned loop calls the builders above directly. Order here is the graph
# registration order (must match assemble_instructions in preflight.py).
# ---------------------------------------------------------------------------


def safety_prompt_ctx(ctx: RunContext[CoDeps]) -> str:
    return safety_prompt(ctx.deps, messages=ctx.messages)


def wrap_up_prompt_ctx(ctx: RunContext[CoDeps]) -> str:
    return wrap_up_prompt(ctx.deps, request_count=ctx.usage.requests)


def current_time_prompt_ctx(ctx: RunContext[CoDeps]) -> str:
    return current_time_prompt(ctx.deps)


def deferred_tool_awareness_prompt_ctx(ctx: RunContext[CoDeps]) -> str:
    return deferred_tool_awareness_prompt(ctx.deps)


def skill_manifest_prompt_ctx(ctx: RunContext[CoDeps]) -> str:
    return skill_manifest_prompt(ctx.deps)
