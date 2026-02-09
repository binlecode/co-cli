"""Prompt templates for the Co CLI agent.

All prompts are stored as Markdown files for easy editing with syntax highlighting.
Use load_prompt() to load a prompt by name.
"""

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
