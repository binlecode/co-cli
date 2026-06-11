"""Name-addressed deferred-tool loader — the `tool_view` tool.

co hides low-frequency tools behind DEFERRED visibility: their full schemas are
withheld from the prompt and replaced by a one-line stub (name + purpose) for every
DEFERRED tool every turn (``tools/deferred_prompt.py``). So discovery is free — the
model can copy the exact name it needs. `tool_view(name)` is the single tool that
brings a deferred tool into reach, family-consistent with ``memory_view`` /
``session_view`` / ``skill_view``.

There is no separate deferring mechanism: a DEFERRED tool is hidden by the per-turn
visibility filter (``agent/toolset.py``) until its name is in
``deps.runtime.revealed_tools``, and an exact-name ``tool_view`` call adds it there.
Because the reveal set lives in runtime memory (not message history), reveals survive
compaction with no preservation coupling, and the SDK's keyword loader never engages.

Resolution ladder inside ``tool_view(name)``:

1. Normalized-exact match (case-insensitive; ``-``/whitespace folded to ``_``) against
   the DEFERRED catalog → reveal it; the model calls it directly next turn. Happy path.
2. No exact match → fuzzy ``difflib`` over the names → "did you mean" candidates,
   **revealing nothing** (a hallucinated name must never resolve to a plausible wrong
   tool). The model retries with an exact name.
3. No fuzzy match → "does not exist — do not retry."
"""

import difflib

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output

# Max "did you mean" candidates returned on a fuzzy near-miss.
_MAX_SUGGESTIONS = 5

# difflib similarity floor for a name to count as a near-miss suggestion.
_FUZZY_CUTOFF = 0.6


def _normalize(name: str) -> str:
    """Fold a tool name for case- and separator-insensitive exact matching.

    ``Skill-Create`` / ``skill create`` / ``skill_create`` all normalize to
    ``skill_create``; hyphen and whitespace runs collapse to a single ``_``.
    """
    return "_".join(name.strip().lower().replace("-", " ").split())


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_concurrent_safe=True,
)
async def tool_view(
    ctx: RunContext[CoDeps],
    name: str,
) -> ToolReturn:
    """Load a deferred tool by name so you can call it.

    Deferred tools are listed by name in the per-turn deferred-tools block but their
    full schemas are not loaded. Pass the tool's exact name here to bring it into reach;
    on an exact match it becomes callable on your next step — then call it directly with
    its own arguments. If the name is slightly off you get "did you mean" suggestions —
    retry with one of those exact names. If nothing matches, the tool does not exist —
    do not retry.

    Args:
        name: The exact name of the deferred tool to load (e.g. "skill_create"),
            copied from the deferred-tools list.
    """
    if not name or not name.strip():
        return tool_error("tool_view: provide the exact name of the tool to load.", ctx=ctx)

    catalog = {
        info.name: info.description
        for info in ctx.deps.tool_catalog.values()
        if info.visibility == VisibilityPolicyEnum.DEFERRED
    }
    by_normalized = {_normalize(canonical): canonical for canonical in catalog}
    query = _normalize(name)

    canonical = by_normalized.get(query)
    if canonical is not None:
        # Honest gate: a DEFERRED tool may carry a per-turn check_fn (e.g. image_view needs a
        # vision-capable model; google_* needs a credential). When that gate is currently
        # false the per-turn visibility filter would keep the tool hidden even after a reveal,
        # so unlocking it here would hand the model a tool that never materializes. Surface the
        # unavailability instead of revealing it.
        info = ctx.deps.tool_catalog.get(canonical)
        if info is not None and info.check_fn is not None and not info.check_fn(ctx.deps):
            return tool_output(
                f"`{canonical}` exists but is not available in this session — its runtime "
                f"prerequisites are not met (a required model or credential is absent). "
                f"Not loaded; do not retry until the prerequisite is configured.",
                ctx=ctx,
            )
        ctx.deps.runtime.revealed_tools.add(canonical)
        return tool_output(
            f"Loaded `{canonical}`. It is now callable — call it directly with its arguments.",
            ctx=ctx,
        )

    matches = difflib.get_close_matches(
        query, list(by_normalized), n=_MAX_SUGGESTIONS, cutoff=_FUZZY_CUTOFF
    )
    if matches:
        suggestions = ", ".join(f"`{by_normalized[m]}`" for m in matches)
        return tool_output(
            f"No tool named '{name}'. Did you mean: {suggestions}? "
            "Retry tool_view with one exact name.",
            ctx=ctx,
        )

    return tool_error(
        f"tool_view: no tool matches '{name}'. It does not exist — do not retry.", ctx=ctx
    )
