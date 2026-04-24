"""Per-turn instruction builder functions for the orchestrator agent."""

from pydantic_ai import RunContext

from co_cli.deps import CoDeps


async def recall_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: inject date, personality memories, and recalled knowledge."""
    from co_cli.context._prompt_text import _recall_prompt_text

    return await _recall_prompt_text(ctx)


def safety_prompt(ctx: RunContext[CoDeps]) -> str:
    """Per-turn: inject doom loop / shell reflection warnings when condition is active."""
    from co_cli.context._prompt_text import _safety_prompt_text

    return _safety_prompt_text(ctx)


def add_shell_guidance(ctx: RunContext[CoDeps]) -> str:
    """Inject shell tool guidance when shell is available."""
    return (
        "Prefer dedicated workspace file tools over shell primitives: "
        "file_read for known files, file_find for path/name discovery, and "
        "file_search for content search. Use file_search(glob=...) when you "
        "need to search inside only a subset of files. "
        "Shell runs as subprocess. DENY-pattern commands are blocked before deferral. "
        "Safe-prefix commands execute directly. All others require user approval. "
        "On non-zero exit, the tool returns the exit code and combined output as a "
        "tool result — read the output to diagnose the failure (wrong flag, missing "
        "binary, permission issue, syntax error) and retry with a corrected command. "
        "Account for platform differences: macOS uses BSD utilities "
        "(stat -f not -c; sed -i '' not -i; no GNU long-opts like --count)."
    )


def add_category_awareness_prompt(ctx: RunContext[CoDeps]) -> str:
    """Inject category-level awareness so the model discovers deferred tools via search_tools."""
    from co_cli.tools._deferred_prompt import build_category_awareness_prompt

    return build_category_awareness_prompt(ctx.deps.tool_index)
