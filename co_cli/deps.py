from dataclasses import dataclass
from pathlib import Path

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
