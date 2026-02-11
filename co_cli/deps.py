from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from co_cli.config import WebPolicy
from co_cli.sandbox import SandboxProtocol

if TYPE_CHECKING:
    from co_cli.config import Settings


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    Design: Contains runtime resources, NOT config objects.
    Settings creates these in main.py, then injects here.
    """

    sandbox: SandboxProtocol
    settings: "Settings" = field(default=None)  # Added for memory lifecycle configuration
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

    # Batch 5: Web intelligence
    brave_search_api_key: str | None = None
    web_fetch_allowed_domains: list[str] = field(default_factory=list)
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_policy: WebPolicy = field(default_factory=WebPolicy)
