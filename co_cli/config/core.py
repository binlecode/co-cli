"""Core Settings model, config loading, and path constants."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from co_cli.config.compaction import COMPACTION_ENV_MAP, CompactionSettings
from co_cli.config.knowledge import KNOWLEDGE_ENV_MAP, KnowledgeSettings
from co_cli.config.llm import LLM_ENV_MAP, LlmSettings, resolve_api_key_from_env
from co_cli.config.mcp import DEFAULT_MCP_SERVERS, MCPServerSettings, parse_mcp_servers_from_env
from co_cli.config.memory import MEMORY_ENV_MAP, MemorySettings
from co_cli.config.observability import OBSERVABILITY_ENV_MAP, ObservabilitySettings
from co_cli.config.shell import SHELL_ENV_MAP, ShellSettings
from co_cli.config.tools import TOOLS_ENV_MAP, ToolsSettings
from co_cli.config.web import WEB_ENV_MAP, WebSettings

APP_NAME = "co-cli"

# Canonical user-global root: ~/.co-cli (overridable via CO_HOME).
# Module-level constant: USER_DIR must resolve before Settings is constructed
# (it sets SETTINGS_FILE, SEARCH_DB, etc.) — cannot be moved into fill_from_env.
USER_DIR = Path(os.getenv("CO_HOME", Path.home() / ".co-cli"))
GOOGLE_TOKEN_PATH = USER_DIR / "google_token.json"
ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
SETTINGS_FILE = USER_DIR / "settings.json"
SEARCH_DB = USER_DIR / "co-cli-search.db"
LOGS_DB = USER_DIR / "co-cli-logs.db"
LOGS_DIR = USER_DIR / "logs"
KNOWLEDGE_DIR = USER_DIR / "knowledge"
SESSIONS_DIR = USER_DIR / "sessions"
TOOL_RESULTS_DIR = USER_DIR / "tool-results"

# Flat defaults (Settings-level, not grouped)
DEFAULT_THEME = "light"
DEFAULT_PERSONALITY = "tars"
DEFAULT_TOOL_RETRIES = 3
DEFAULT_DOOM_LOOP_THRESHOLD = 3
DEFAULT_MAX_REFLECTIONS = 3
REASONING_DISPLAY_OFF = "off"
REASONING_DISPLAY_SUMMARY = "summary"
REASONING_DISPLAY_FULL = "full"
DEFAULT_REASONING_DISPLAY = REASONING_DISPLAY_SUMMARY
VALID_REASONING_DISPLAY_MODES: frozenset[str] = frozenset(
    {
        REASONING_DISPLAY_OFF,
        REASONING_DISPLAY_SUMMARY,
        REASONING_DISPLAY_FULL,
    }
)


def _ensure_dirs() -> None:
    """Create the canonical user-global directory and subdirectories (idempotent)."""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _validate_personality(personality: str) -> list[str]:
    """Return startup warnings for missing personality files."""
    from co_cli.personality.prompts.validator import validate_personality_files

    return validate_personality_files(personality)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Nested sub-model groups
    llm: LlmSettings = Field(default_factory=LlmSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    shell: ShellSettings = Field(default_factory=ShellSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    compaction: CompactionSettings = Field(default_factory=CompactionSettings)

    # Flat — integration paths
    obsidian_vault_path: str | None = Field(default=None)
    brave_search_api_key: str | None = Field(default=None)
    google_credentials_path: str | None = Field(default=None)
    knowledge_path: str | None = Field(default=None)

    # Flat — behavior
    theme: str = Field(default=DEFAULT_THEME)
    reasoning_display: Literal["off", "summary", "full"] = Field(default=DEFAULT_REASONING_DISPLAY)
    personality: str = Field(default=DEFAULT_PERSONALITY)
    tool_retries: int = Field(default=DEFAULT_TOOL_RETRIES)

    # Flat — safety
    doom_loop_threshold: int = Field(default=DEFAULT_DOOM_LOOP_THRESHOLD, ge=2, le=10)
    max_reflections: int = Field(default=DEFAULT_MAX_REFLECTIONS, ge=1, le=10)

    # Flat — single-field group
    mcp_servers: dict[str, MCPServerSettings] = Field(
        default_factory=lambda: {k: v.model_copy() for k, v in DEFAULT_MCP_SERVERS.items()}
    )

    @field_validator("personality")
    @classmethod
    def _validate_personality_name(cls, v: str) -> str:
        from co_cli.personality.prompts.validator import VALID_PERSONALITIES

        if v not in VALID_PERSONALITIES:
            raise ValueError(f"personality must be one of {VALID_PERSONALITIES}, got: {v}")
        return v

    @model_validator(mode="before")
    @classmethod
    def fill_from_env(cls, data: dict, info: ValidationInfo) -> dict:
        """Env vars override all file-based values (highest precedence layer).

        Flat env vars are mapped into the nested sub-model structure.
        """
        env_source: Mapping[str, str] | None = info.context.get("env") if info.context else None
        if env_source is None:
            env_source = os.environ

        # Flat fields
        flat_env_map = {
            "obsidian_vault_path": "OBSIDIAN_VAULT_PATH",
            "brave_search_api_key": "BRAVE_SEARCH_API_KEY",
            "google_credentials_path": "GOOGLE_CREDENTIALS_PATH",
            "knowledge_path": "CO_KNOWLEDGE_PATH",
            "theme": "CO_THEME",
            "reasoning_display": "CO_REASONING_DISPLAY",
            "personality": "CO_PERSONALITY",
            "tool_retries": "CO_TOOL_RETRIES",
            "doom_loop_threshold": "CO_DOOM_LOOP_THRESHOLD",
            "max_reflections": "CO_MAX_REFLECTIONS",
        }
        for field, env_var in flat_env_map.items():
            val = env_source.get(env_var)
            if val:
                data[field] = val

        # Nested fields — each group owns its env-var map in its own module
        nested_env_map: dict[str, dict[str, str]] = {
            "llm": LLM_ENV_MAP,
            "knowledge": KNOWLEDGE_ENV_MAP,
            "memory": MEMORY_ENV_MAP,
            "shell": SHELL_ENV_MAP,
            "web": WEB_ENV_MAP,
            "observability": OBSERVABILITY_ENV_MAP,
            "tools": TOOLS_ENV_MAP,
            "compaction": COMPACTION_ENV_MAP,
        }
        for group, fields in nested_env_map.items():
            for field, env_var in fields.items():
                val = env_source.get(env_var)
                if val:
                    data.setdefault(group, {})[field] = val

        # Provider-aware API key resolution lives in llm.py.
        api_key = resolve_api_key_from_env(env_source, data.get("llm", {}))
        if api_key:
            data.setdefault("llm", {})["api_key"] = api_key

        # MCP servers (flat — env override)
        mcp_servers = parse_mcp_servers_from_env(env_source)
        if mcp_servers is not None:
            data["mcp_servers"] = mcp_servers

        return data


def load_config(
    _user_config_path: Path | None = None,
    _env: dict[str, str] | None = None,
) -> Settings:
    """Load configuration: user settings → env vars."""
    data: dict[str, Any] = {}

    user_config = _user_config_path if _user_config_path is not None else SETTINGS_FILE
    if user_config.exists():
        with open(user_config) as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Malformed settings file {user_config}: {e}") from e

    # Pre-flight: validate raw file data before env overrides so fill_from_env
    # cannot mask invalid values by replacing them with env-sourced ones.
    if user_config.exists() and data:
        try:
            Settings.model_validate(data, context={"env": {}})
        except ValidationError as exc:
            raise ValueError(f"Invalid configuration — check: {user_config}:\n{exc}") from exc

    context = {"env": _env} if _env is not None else None
    try:
        resolved = Settings.model_validate(data, context=context)
    except ValidationError as exc:
        hint = f" — check: {user_config}" if user_config.exists() else ""
        raise ValueError(f"Invalid configuration{hint}:\n{exc}") from exc

    for warning in _validate_personality(resolved.personality):
        print(f"Warning: {warning}")  # noqa: T201 — personality validation warning at bootstrap, logger not yet configured

    return resolved


# Lazy settings singleton
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the global Settings instance, creating it on first call."""
    global _settings
    if _settings is None:
        _ensure_dirs()
        try:
            _settings = load_config()
        except ValueError as e:
            import sys

            print(f"Configuration error: {e}", file=sys.stderr)  # noqa: T201 — fatal startup error to stderr, logger not yet configured
            raise SystemExit(1) from e
    return _settings


def __getattr__(name: str):
    """Lazy module attribute — ``from co_cli.config.core import settings`` works."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
