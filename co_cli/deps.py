from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from co_cli.sandbox import Sandbox


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    Design: Contains runtime resources, NOT config objects.
    Settings creates these in main.py, then injects here.
    """

    sandbox: Sandbox
    auto_confirm: bool = False  # Session-yolo: set True when user picks "a" in approval prompt
    session_id: str = ""
    obsidian_vault_path: Path | None = None  # Batch 2: Obsidian vault

    # Google credentials — resolved lazily on first Google tool call via google_auth
    google_credentials_path: str | None = None
    google_creds: Any | None = field(default=None, repr=False)
    _google_creds_resolved: bool = field(default=False, repr=False, init=False)

    # Shell safe commands — auto-approved without prompting
    shell_safe_commands: list[str] = field(default_factory=list)

    # Mutable per-session state
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)

    # Sandbox limits
    sandbox_max_timeout: int = 600  # Hard ceiling for per-command timeout (seconds)

    # Batch 4: Slack client
    slack_client: Any | None = None  # slack_sdk.WebClient at runtime
