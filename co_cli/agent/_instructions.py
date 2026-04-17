"""Per-turn instruction builder functions for the orchestrator agent."""

from datetime import date

from pydantic_ai import RunContext

from co_cli.deps import CoDeps


def add_current_date(ctx: RunContext[CoDeps]) -> str:
    """Inject the current date so the model can reason about time."""
    return f"Today is {date.today().isoformat()}."


def add_shell_guidance(ctx: RunContext[CoDeps]) -> str:
    """Inject shell tool guidance when shell is available."""
    return (
        "Shell runs as subprocess. DENY-pattern commands are blocked before deferral. "
        "Safe-prefix commands execute directly. All others require user approval. "
        "On non-zero exit, the tool returns the exit code and combined output as a "
        "tool result — read the output to diagnose the failure (wrong flag, missing "
        "binary, permission issue, syntax error) and retry with a corrected command. "
        "Account for platform differences: macOS uses BSD utilities "
        "(stat -f not -c; sed -i '' not -i; no GNU long-opts like --count)."
    )


def add_personality_memories(ctx: RunContext[CoDeps]) -> str:
    """Inject personality-context memories for relationship continuity."""
    if not ctx.deps.config.personality:
        return ""
    from co_cli.prompts.personalities._injector import _load_personality_memories

    return _load_personality_memories()


def add_category_awareness_prompt(ctx: RunContext[CoDeps]) -> str:
    """Inject category-level awareness so the model discovers deferred tools via search_tools."""
    from co_cli.context._deferred_tool_prompt import build_category_awareness_prompt

    return build_category_awareness_prompt(ctx.deps.tool_index)
