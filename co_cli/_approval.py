"""Internal helper for shell command safety classification."""

# Patterns that indicate path traversal, glob expansion, or shell injection risk in arg tokens.
# Single-letter flags (-f, -v) and --word-flags contain none of these.
# "$" blocks env-var expansion ($HOME, $PATH) and command substitution ($(...) is
# already caught by the chaining check, but bare $ is an additional guard).
_FORBIDDEN_ARG_PATTERNS = frozenset({
    "*", "?", "[", "]", "{", "}", "/", "\\", "./", "~/", "..", "\x00", "$",
})


def _validate_args(args_str: str) -> bool:
    """Return False if any token contains a path traversal or glob character.

    Single-letter flags (-f) and long flags (--short) pass; absolute paths,
    globs, and traversal sequences are rejected.
    """
    for token in args_str.split():
        for pattern in _FORBIDDEN_ARG_PATTERNS:
            if pattern in token:
                return False
    return True


def _is_safe_command(cmd: str, safe_commands: list[str]) -> bool:
    """Check if cmd starts with a safe prefix and has no shell chaining or
    dangerous arg tokens.

    UX convenience — approval is the security boundary.
    """
    # Reject shell chaining, redirection, and backgrounding — force approval.
    # Single-char ops also catch doubled forms (& catches &&, > catches >>, etc.)
    if any(op in cmd for op in [";", "&", "|", ">", "<", "`", "$(", "\n"]):
        return False
    # Match first token (or multi-word prefix like "git status")
    # Longest prefix first so "git status" matches before "git"
    for prefix in sorted(safe_commands, key=len, reverse=True):
        if cmd == prefix:
            return True
        if cmd.startswith(prefix + " "):
            args_str = cmd[len(prefix) + 1:]
            return _validate_args(args_str)
    return False
