"""Shell command policy engine: DENY / REQUIRE_APPROVAL / ALLOW classification."""

import re
from dataclasses import dataclass
from enum import Enum

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


class ShellDecisionEnum(Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class ShellPolicyResult:
    decision: ShellDecisionEnum
    reason: str


def evaluate_shell_command(cmd: str, safe_prefixes: list[str]) -> ShellPolicyResult:
    """Classify a shell command as DENY, ALLOW, or REQUIRE_APPROVAL.

    DENY tier is checked first — these commands are blocked regardless of safe prefixes.
    ALLOW tier delegates to _is_safe_command for prefix + arg validation.
    Everything else falls to REQUIRE_APPROVAL.
    """
    # DENY tier — check for patterns that indicate injection or destruction risk

    # 1. Control characters (except tab \x09 and newline \x0a)
    for ch in cmd:
        if ord(ch) < 0x20 and ch not in ('\t', '\n'):
            return ShellPolicyResult(ShellDecisionEnum.DENY, "control character in command")

    # 2. Heredoc injection
    if "<<" in cmd:
        return ShellPolicyResult(ShellDecisionEnum.DENY, "heredoc injection pattern (<<)")

    # 3. Env-injection via command substitution: VAR=$(...)
    if re.search(r'\w+=\$\(', cmd):
        return ShellPolicyResult(ShellDecisionEnum.DENY, "env-injection pattern (VAR=$(...))")

    # 4. Absolute-path destruction: rm -rf / or rm -rf ~
    if re.search(r'\brm\b.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+[/~]', cmd) or \
       re.search(r'\brm\b.*-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+[/~]', cmd):
        return ShellPolicyResult(ShellDecisionEnum.DENY, "absolute-path destruction pattern (rm -rf /~)")

    # ALLOW tier — safe prefix match with arg validation
    if _is_safe_command(cmd, safe_prefixes):
        return ShellPolicyResult(ShellDecisionEnum.ALLOW, "safe prefix match")

    # Default: require user approval
    return ShellPolicyResult(ShellDecisionEnum.REQUIRE_APPROVAL, "requires user approval")
