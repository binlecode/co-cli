"""Shell command policy engine: DENY / REQUIRE_APPROVAL / ALLOW classification."""

import re
from dataclasses import dataclass
from enum import Enum

from co_cli.tools._approval import _is_safe_command


class ShellDecision(Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class ShellPolicyResult:
    decision: ShellDecision
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
            return ShellPolicyResult(ShellDecision.DENY, "control character in command")

    # 2. Heredoc injection
    if "<<" in cmd:
        return ShellPolicyResult(ShellDecision.DENY, "heredoc injection pattern (<<)")

    # 3. Env-injection via command substitution: VAR=$(...)
    if re.search(r'\w+=\$\(', cmd):
        return ShellPolicyResult(ShellDecision.DENY, "env-injection pattern (VAR=$(...))")

    # 4. Absolute-path destruction: rm -rf / or rm -rf ~
    if re.search(r'\brm\b.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+[/~]', cmd) or \
       re.search(r'\brm\b.*-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+[/~]', cmd):
        return ShellPolicyResult(ShellDecision.DENY, "absolute-path destruction pattern (rm -rf /~)")

    # ALLOW tier — safe prefix match with arg validation
    if _is_safe_command(cmd, safe_prefixes):
        return ShellPolicyResult(ShellDecision.ALLOW, "safe prefix match")

    # Default: require user approval
    return ShellPolicyResult(ShellDecision.REQUIRE_APPROVAL, "requires user approval")
