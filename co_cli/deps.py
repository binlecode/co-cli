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
    auto_confirm: bool = False  # For human-in-the-loop (until we adopt DeferredToolRequests)
    session_id: str = ""
    obsidian_vault_path: Path | None = None  # Batch 2: Obsidian vault

    # Batch 3: Google API clients (Any because googleapiclient has no typed stubs)
    google_drive: Any | None = None
    google_gmail: Any | None = None
    google_calendar: Any | None = None

    # Batch 4: Slack client
    slack_client: Any | None = None  # slack_sdk.WebClient at runtime
