"""Prompt templates for the Co CLI agent.

All prompts are stored as Markdown files for easy editing with syntax highlighting.
Use load_prompt() to load a prompt by name.
"""

import re
from pathlib import Path


def load_prompt(name: str) -> str:
    """Load a prompt template by name.

    Args:
        name: Prompt filename without extension (e.g., "system" for "system.md")

    Returns:
        The prompt content as a string.

    Raises:
        FileNotFoundError: If the prompt file doesn't exist.
    """
    prompts_dir = Path(__file__).parent
    prompt_file = prompts_dir / f"{name}.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    return prompt_file.read_text(encoding="utf-8")


def load_personality(personality: str) -> str:
    """Load personality template by name.

    Args:
        personality: Personality name (professional, friendly, terse, inquisitive)

    Returns:
        Personality template content as string

    Raises:
        FileNotFoundError: If personality template doesn't exist
    """
    prompts_dir = Path(__file__).parent
    personality_file = prompts_dir / "personalities" / f"{personality}.md"

    if not personality_file.exists():
        raise FileNotFoundError(f"Personality template not found: {personality_file}")

    return personality_file.read_text(encoding="utf-8")


def get_system_prompt(
    provider: str,
    personality: str | None = None,
    model_name: str | None = None,
) -> str:
    """Assemble system prompt with model conditionals, personality, and project overrides.

    Processing steps:
    1. Load base system.md
    2. Process model conditionals ([IF gemini] / [IF ollama])
    3. Inject personality template (if specified)
    4. Inject model quirk counter-steering (if known)
    5. Append project instructions from .co-cli/instructions.md (if exists)
    6. Validate result (no empty prompt, no unprocessed markers)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown).
                 Unknown providers default to Ollama (conservative).
        personality: Personality name (professional, friendly, terse, inquisitive).
                    If None, no personality is injected.
        model_name: Model identifier for quirk lookup (e.g., "gemini-1.5-pro", "deepseek-coder").
                   If None, no counter-steering is injected.

    Returns:
        Assembled system prompt as string.

    Raises:
        FileNotFoundError: If system.md or personality template doesn't exist.
        ValueError: If assembled prompt is empty or has unprocessed conditionals.

    Example:
        >>> prompt = get_system_prompt("gemini", personality="friendly", model_name="gemini-1.5-pro")
        >>> assert "[IF ollama]" not in prompt  # Ollama sections removed
        >>> assert "[IF gemini]" not in prompt  # Markers cleaned up
        >>> assert "Personality" in prompt  # Personality injected
        >>> assert "scope of the user's request" in prompt  # Counter-steering for overeager model
    """
    # 1. Load base prompt
    prompts_dir = Path(__file__).parent
    prompt_file = prompts_dir / "system.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    base_prompt = prompt_file.read_text(encoding="utf-8")

    # 2. Process model conditionals
    provider_lower = provider.lower()

    if provider_lower == "gemini":
        # Remove Ollama sections
        base_prompt = re.sub(
            r"\[IF ollama\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        # Clean up Gemini markers
        base_prompt = base_prompt.replace("[IF gemini]", "").replace("[ENDIF]", "")
    elif provider_lower == "ollama":
        # Remove Gemini sections
        base_prompt = re.sub(
            r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        # Clean up Ollama markers
        base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
    else:
        # Unknown provider - treat as Ollama (conservative default)
        base_prompt = re.sub(
            r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")

    # 3. Inject personality (if specified)
    if personality:
        personality_content = load_personality(personality)
        base_prompt += f"\n\n## Personality\n\n{personality_content}"

    # 4. Inject model quirk counter-steering (if known)
    if model_name:
        from co_cli.prompts.model_quirks import get_counter_steering

        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            base_prompt += f"\n\n## Model-Specific Guidance\n\n{counter_steering}"

    # 5. Load project instructions if present
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        base_prompt += "\n\n## Project-Specific Instructions\n\n"
        base_prompt += instructions_content

    # 6. Validate result
    if not base_prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    # Check for unprocessed conditionals (indicates bug in regex)
    if "[IF " in base_prompt:
        raise ValueError("Unprocessed conditional markers remain in prompt")

    return base_prompt
