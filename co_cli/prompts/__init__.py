"""Prompt templates for the Co CLI agent.

Aspect-driven system prompt assembly with tier-based model adaptation.
Behavioral aspects are stored as independent Markdown files in aspects/.
"""

from pathlib import Path


_ASPECTS_DIR = Path(__file__).parent / "aspects"


def _load_aspect(name: str) -> str:
    """Load a single behavioral aspect file by name.

    Args:
        name: Aspect filename without extension (e.g., "identity").

    Returns:
        Stripped text content of the aspect file.

    Raises:
        FileNotFoundError: If the aspect file doesn't exist.
    """
    path = _ASPECTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Aspect file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


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


def load_personality_style_only(personality: str) -> str:
    """Load only the style aspect of a personality (skip character).

    Used by tier 2 models where character content is too expensive.

    Args:
        personality: Personality preset name.

    Returns:
        Style-only personality prompt text.

    Raises:
        FileNotFoundError: If personality preset or style file doesn't exist.
    """
    from co_cli.prompts.personalities._composer import compose_style_only

    try:
        return compose_style_only(personality)
    except KeyError:
        raise FileNotFoundError(f"Personality preset not found: {personality}")


def get_system_prompt(
    provider: str,
    personality: str | None = None,
    model_name: str | None = None,
) -> str:
    """Assemble system prompt from behavioral aspects with tier-based selection.

    Assembly order (recency = precedence):
    1. Behavioral aspects (tier-selected subset from aspects/)
    2. Model quirk counter-steering (shapes how personality is expressed)
    3. Personality template (tier-dependent: skip / style-only / full)
    4. Internal knowledge (background reference)
    5. Project instructions (highest precedence user customization)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown).
        personality: Personality preset name (finch, jeff, friendly, terse, inquisitive).
                    If None, no personality is injected.
        model_name: Model identifier for quirk/tier lookup (e.g., "gemini-2.0-flash",
                   "glm-4.7-flash"). If None, uses default tier (3).

    Returns:
        Assembled system prompt as string.

    Raises:
        FileNotFoundError: If aspect files or personality template doesn't exist.
        ValueError: If assembled prompt is empty.
    """
    from co_cli.prompts.model_quirks import get_model_tier, get_counter_steering, TIER_ASPECTS

    # 1. Load aspects by tier
    tier = get_model_tier(provider, model_name)
    aspect_names = TIER_ASPECTS[tier]
    prompt = "\n\n".join(_load_aspect(name) for name in aspect_names)

    # 2. Counter-steering (all tiers)
    if model_name:
        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            prompt += f"\n\n## Model-Specific Guidance\n\n{counter_steering}"

    # 3. Personality (tier-dependent)
    if personality:
        if tier == 1:
            pass  # skip personality entirely — budget too tight
        elif tier == 2:
            style_content = load_personality_style_only(personality)
            prompt += f"\n\n## Personality\n\n{style_content}"
        else:
            personality_content = load_personality(personality)
            prompt += f"\n\n## Personality\n\n{personality_content}"

    # 4. Internal knowledge (all tiers)
    from co_cli.knowledge import load_internal_knowledge

    knowledge = load_internal_knowledge()
    if knowledge:
        prompt += f"\n\n<system-reminder>\n{knowledge}\n</system-reminder>"

    # 5. Project instructions (all tiers)
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        prompt += "\n\n## Project-Specific Instructions\n\n"
        prompt += instructions_content

    # Validate result
    if not prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    return prompt
