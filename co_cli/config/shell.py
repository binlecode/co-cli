"""Shell execution limits and safe command list."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_SHELL_MAX_TIMEOUT = 300

# Foreground shell_exec auto-yield window: a command still running after this
# many seconds is promoted to a background task and the turn is freed. Sits
# above typical bounded-command durations (most builds/test runs finish well
# under it) so only genuinely-stuck or unbounded commands (mpv, tail -f, dev
# servers) yield. 0 disables auto-yield.
DEFAULT_SHELL_YIELD_WINDOW_SECONDS = 20

SHELL_ENV_MAP: dict[str, str] = {
    "max_timeout": "CO_SHELL_MAX_TIMEOUT",
    "safe_commands": "CO_SHELL_SAFE_COMMANDS",
    "yield_window_seconds": "CO_SHELL_YIELD_WINDOW_SECONDS",
}

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

    model_config = ConfigDict(extra="forbid")

    max_timeout: int = Field(default=DEFAULT_SHELL_MAX_TIMEOUT)
    safe_commands: list[str] = Field(default=DEFAULT_SHELL_SAFE_COMMANDS)
    yield_window_seconds: int = Field(default=DEFAULT_SHELL_YIELD_WINDOW_SECONDS)

    @field_validator("safe_commands", mode="before")
    @classmethod
    def _parse_safe_commands(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _check_yield_window(self) -> "ShellSettings":
        if self.yield_window_seconds < 0:
            raise ValueError("yield_window_seconds must be >= 0 (0 disables auto-yield)")
        if self.yield_window_seconds >= self.max_timeout:
            raise ValueError(
                f"yield_window_seconds ({self.yield_window_seconds}) must be below "
                f"max_timeout ({self.max_timeout})"
            )
        return self
