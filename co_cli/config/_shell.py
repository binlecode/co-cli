"""Shell execution limits and safe command list."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_SHELL_MAX_TIMEOUT = 600

# Conservative default safe commands for auto-approval.
# UX convenience — approval is the security boundary.
DEFAULT_SHELL_SAFE_COMMANDS: list[str] = [
    # Filesystem listing
    "ls",
    "tree",
    "find",
    "fd",
    # File reading
    "cat",
    "head",
    "tail",
    # Search
    "grep",
    "rg",
    "ag",
    # Text processing (read-only)
    "wc",
    "sort",
    "uniq",
    "cut",
    "tr",
    "jq",
    # Output
    "echo",
    "printf",
    # System info
    "pwd",
    "whoami",
    "hostname",
    "uname",
    "date",
    "env",
    "which",
    "file",
    "stat",
    "id",
    "du",
    "df",
    # Git read-only (prefix match: "git status", "git diff", etc.)
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git tag",
    "git blame",
]


class ShellSettings(BaseModel):
    """Shell execution limits and safe command list."""

    model_config = ConfigDict(extra="ignore")

    max_timeout: int = Field(default=DEFAULT_SHELL_MAX_TIMEOUT)
    safe_commands: list[str] = Field(default=DEFAULT_SHELL_SAFE_COMMANDS)

    @field_validator("safe_commands", mode="before")
    @classmethod
    def _parse_safe_commands(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
