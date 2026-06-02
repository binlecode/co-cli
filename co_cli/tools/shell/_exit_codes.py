"""Exit-code interpretation for shell_exec results.

Small models frequently misread a non-zero exit as a hard failure and retry in
a loop. But grep with no matches and diff with differences both exit 1 while
running correctly, and shell-level codes like 127 (command not found) carry a
standard meaning the bare number does not convey. These helpers turn the raw
exit code into a short interpretation so the model reacts correctly instead of
treating a benign result as a failure.
"""

import os

# First-token commands whose exit 1 means "ran fine, found nothing / found a
# difference" — a successful result, not a failure. Keyed by command basename.
_BENIGN_EXIT_1: dict[str, str] = {
    "grep": "no matches found",
    "egrep": "no matches found",
    "fgrep": "no matches found",
    "rg": "no matches found",
    "ag": "no matches found",
    "diff": "files differ",
    "cmp": "files differ",
}

# Shell-level exit codes with a standard POSIX meaning, independent of command.
_SHELL_EXIT_MEANINGS: dict[int, str] = {
    126: "command found but not executable (permission denied or not a binary)",
    127: "command not found — check the spelling, the binary name, or PATH",
    130: "interrupted (SIGINT)",
    137: "killed (SIGKILL — often the OS out-of-memory killer)",
    139: "segmentation fault (SIGSEGV)",
    143: "terminated (SIGTERM)",
}


def benign_exit_note(cmd: str, exit_code: int) -> str | None:
    """Return a note when a non-zero exit is actually a successful result.

    Covers the grep/diff family's exit 1 (no matches / files differ). Returns
    None for every other (cmd, exit_code) pair — including grep exit 2, which
    is a real error (bad regex, unreadable file).
    """
    if exit_code != 1:
        return None
    first_token = cmd.split()[0] if cmd.strip() else ""
    return _BENIGN_EXIT_1.get(os.path.basename(first_token))


def shell_exit_meaning(exit_code: int) -> str | None:
    """Return the standard meaning of a shell-level exit code, or None when the
    bare code is self-explanatory."""
    return _SHELL_EXIT_MEANINGS.get(exit_code)
