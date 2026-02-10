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
    """Assemble system prompt with personality and model quirk counter-steering.

    Processing steps:
    1. Load base system.md
    2. Inject personality template (if specified)
    3. Inject model quirk counter-steering (if known)
    4. Append project instructions from .co-cli/instructions.md (if exists)
    5. Validate result (no empty prompt)

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

    # 2. Inject personality (if specified)
    if personality:
        personality_content = load_personality(personality)
        base_prompt += f"\n\n## Personality\n\n{personality_content}"

    # 3. Inject model quirk counter-steering (if known)
    if model_name:
        from co_cli.prompts.model_quirks import get_counter_steering

        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            base_prompt += f"\n\n## Model-Specific Guidance\n\n{counter_steering}"

    # 4. Load project instructions if present
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        base_prompt += "\n\n## Project-Specific Instructions\n\n"
        base_prompt += instructions_content

    # 5. Validate result
    if not base_prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    return base_prompt
