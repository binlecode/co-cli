"""Prompt templates for the Co CLI agent.

All prompts are stored as Markdown files for easy editing with syntax highlighting.
"""

from pathlib import Path


def load_personality(personality: str) -> str:
    """Load personality by composing orthogonal aspects (character + style).

    Args:
        personality: Personality preset name (finch, jeff, friendly, terse, inquisitive)

    Returns:
        Composed personality prompt text.

    Raises:
        FileNotFoundError: If personality preset or aspect files don't exist.
    """
    from co_cli.prompts.personalities._composer import compose_personality

    try:
        return compose_personality(personality)
    except KeyError:
        raise FileNotFoundError(f"Personality preset not found: {personality}")


def get_system_prompt(
    provider: str,
    personality: str | None = None,
    model_name: str | None = None,
) -> str:
    """Assemble system prompt following the roadmap assembly order.

    Assembly order (recency = precedence):
    1. Base system.md (identity, principles, tool guidance)
    2. Model quirk counter-steering (shapes how personality is expressed)
    3. Personality template (character + style)
    4. Internal knowledge (background reference)
    5. Project instructions (highest precedence user customization)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown).
        personality: Personality preset name (finch, jeff, friendly, terse, inquisitive).
                    If None, no personality is injected.
        model_name: Model identifier for quirk lookup (e.g., "gemini-2.0-flash", "glm-4.7-flash").
                   If None, no counter-steering is injected.

    Returns:
        Assembled system prompt as string.

    Raises:
        FileNotFoundError: If system.md or personality template doesn't exist.
        ValueError: If assembled prompt is empty.
    """
    # 1. Load base prompt
    prompts_dir = Path(__file__).parent
    prompt_file = prompts_dir / "system.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    base_prompt = prompt_file.read_text(encoding="utf-8")

    # 2. Inject model quirk counter-steering (if known)
    if model_name:
        from co_cli.prompts.model_quirks import get_counter_steering

        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            base_prompt += f"\n\n## Model-Specific Guidance\n\n{counter_steering}"

    # 3. Inject personality (if specified)
    if personality:
        personality_content = load_personality(personality)
        base_prompt += f"\n\n## Personality\n\n{personality_content}"

    # 4. Inject internal knowledge (if present)
    from co_cli.knowledge import load_internal_knowledge

    knowledge = load_internal_knowledge()
    if knowledge:
        base_prompt += f"\n\n<system-reminder>\n{knowledge}\n</system-reminder>"

    # 5. Load project instructions if present
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        base_prompt += "\n\n## Project-Specific Instructions\n\n"
        base_prompt += instructions_content

    # Validate result
    if not base_prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    return base_prompt
