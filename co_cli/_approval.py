"""Internal helper for shell command safety classification."""


def _is_safe_command(cmd: str, safe_commands: list[str]) -> bool:
    """Check if cmd starts with a safe prefix and has no shell chaining.

    This is a UX convenience, not a security boundary — the Docker sandbox
    provides isolation. See docs/TODO-shell-safety.md for rationale.
    """
    # Reject shell chaining, redirection, and backgrounding — force approval.
    # Single-char ops also catch doubled forms (& catches &&, > catches >>, etc.)
    if any(op in cmd for op in [";", "&", "|", ">", "<", "`", "$(", "\n"]):
        return False
    # Match first token (or multi-word prefix like "git status")
    # Longest prefix first so "git status" matches before "git"
    for prefix in sorted(safe_commands, key=len, reverse=True):
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False
