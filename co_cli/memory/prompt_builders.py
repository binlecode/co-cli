"""Prompt builder functions for memory agent user-turn construction."""


def build_extraction_user_prompt(line_count: int, manifest: str) -> str:
    """Build the framing instruction appended to the conversation window for the extraction agent.

    Returns the framing/manifest part only — the conversation window itself is prepended
    by the caller as ``window + "\\n\\n" + build_extraction_user_prompt(...)``.

    When manifest is non-empty, includes an existing-files section so the extractor
    avoids outputting candidates that duplicate known memories.
    """
    framing = f"Analyze the ~{line_count} lines of conversation above."
    if manifest:
        framing += (
            f"\n\n## Existing memory files\n\n{manifest}\n\n"
            "Check this list before writing — update an existing file rather than creating a duplicate."
        )
    return framing


def build_save_user_prompt(instruction: str) -> str:
    """Build the user-turn prompt for the save agent.

    Wraps the caller's natural-language instruction in save-task framing
    so the save subagent knows what action to take.
    """
    return (
        f"The user has asked you to save the following to memory:\n\n{instruction}\n\n"
        "Write the memory to the appropriate file in the memory directory following the "
        "two-step protocol: (1) write the topic file with correct frontmatter, "
        "(2) update MEMORY.md with a one-line index pointer."
    )
