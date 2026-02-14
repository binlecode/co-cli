from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from co_cli.config import WebPolicy
from co_cli.shell_backend import ShellBackend


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    Flat fields only — no config objects. main.py reads Settings once and
    injects scalar values here. Tools access ctx.deps.field_name directly.
    """

    shell: ShellBackend
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

    # Shell limits
    shell_max_timeout: int = 600  # Hard ceiling for per-command timeout (seconds)

    # Batch 5: Web intelligence
    brave_search_api_key: str | None = None
    web_fetch_allowed_domains: list[str] = field(default_factory=list)
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_policy: WebPolicy = field(default_factory=WebPolicy)
    web_http_max_retries: int = 2
    web_http_backoff_base_seconds: float = 1.0
    web_http_backoff_max_seconds: float = 8.0
    web_http_jitter_ratio: float = 0.2

    # Memory lifecycle
    memory_max_count: int = 200
    memory_dedup_window_days: int = 7
    memory_dedup_threshold: int = 85
    memory_decay_strategy: str = "summarize"
    memory_decay_percentage: float = 0.2

    # Personality / role
    personality: str | None = None

    # History governance
    max_history_messages: int = 40
    tool_output_trim_chars: int = 2000
    summarization_model: str = ""
