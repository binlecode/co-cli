"""Core Settings model, config loading, and path constants."""

import json
import os
from collections.abc import Mapping
from copy import deepcopy
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

from co_cli.config._knowledge import KnowledgeSettings
from co_cli.config._llm import LlmSettings
from co_cli.config._memory import MemorySettings
from co_cli.config._observability import ObservabilityConfig
from co_cli.config._shell import ShellSettings
from co_cli.config._subagent import SubagentSettings
from co_cli.config._web import WebSettings

APP_NAME = "co-cli"

# Canonical user-global root: ~/.co-cli (overridable via CO_CLI_HOME)
USER_DIR = Path(os.getenv("CO_CLI_HOME", Path.home() / ".co-cli"))
GOOGLE_TOKEN_PATH = USER_DIR / "google_token.json"
ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
SETTINGS_FILE = USER_DIR / "settings.json"
SEARCH_DB = USER_DIR / "co-cli-search.db"
LOGS_DB = USER_DIR / "co-cli-logs.db"
LOGS_DIR = USER_DIR / "logs"

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


def _deep_merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge nested settings dicts.

    Scalar and list values are replaced wholesale by ``override``.
    Dict values are merged key-by-key so partial nested overrides work.
    """
    merged = deepcopy(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = _deep_merge_settings(base_value, override_value)
            continue
        merged[key] = deepcopy(override_value)
    return merged


def _validate_personality(personality: str) -> list[str]:
    """Return startup warnings for missing personality files."""
    from co_cli.prompts.personalities._validator import validate_personality_files

    return validate_personality_files(personality)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server (stdio or HTTP transport).

    Stdio: set ``command`` (required). Subprocess launched by pydantic-ai.
    HTTP:  set ``url`` instead. No subprocess — connects to a remote server.
    Exactly one of ``command`` or ``url`` must be provided.
    """

    command: str | None = Field(
        default=None,
        description="Executable to launch (e.g. 'npx', 'uvx', 'python'). Required for stdio transport.",
    )
    url: str | None = Field(
        default=None,
        description="Remote server URL for HTTP transport (StreamableHTTP or SSE). Mutually exclusive with command.",
    )
    args: list[str] = Field(
        default_factory=list, description="Command-line arguments (stdio only)"
    )
    timeout: int = Field(default=5, ge=1, le=60)
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to subprocess (stdio only)",
    )
    approval: Literal["ask", "auto"] = Field(default="ask")
    prefix: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_command_or_url(self) -> "MCPServerConfig":
        if self.url and self.command:
            raise ValueError("MCPServerConfig: 'url' and 'command' are mutually exclusive")
        if not self.url and not self.command:
            raise ValueError("MCPServerConfig requires either 'command' or 'url'")
        return self


# Default MCP servers — shipped out-of-the-box, skip gracefully when npx absent.
_DEFAULT_MCP_SERVERS: dict[str, MCPServerConfig] = {
    "context7": MCPServerConfig(
        command="npx",
        args=["-y", "@upstash/context7-mcp@latest"],
        approval="auto",
    ),
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Nested sub-model groups
    llm: LlmSettings = Field(default_factory=LlmSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    subagent: SubagentSettings = Field(default_factory=SubagentSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    shell: ShellSettings = Field(default_factory=ShellSettings)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    # Flat — integration paths
    obsidian_vault_path: str | None = Field(default=None)
    brave_search_api_key: str | None = Field(default=None)
    google_credentials_path: str | None = Field(default=None)
    library_path: str | None = Field(default=None)

    # Flat — behavior
    theme: str = Field(default=DEFAULT_THEME)
    reasoning_display: Literal["off", "summary", "full"] = Field(default=DEFAULT_REASONING_DISPLAY)
    personality: str = Field(default=DEFAULT_PERSONALITY)
    tool_retries: int = Field(default=DEFAULT_TOOL_RETRIES)

    # Flat — safety
    doom_loop_threshold: int = Field(default=DEFAULT_DOOM_LOOP_THRESHOLD, ge=2, le=10)
    max_reflections: int = Field(default=DEFAULT_MAX_REFLECTIONS, ge=1, le=10)

    # Flat — single-field group
    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=lambda: _DEFAULT_MCP_SERVERS.copy()
    )

    @field_validator("personality")
    @classmethod
    def _validate_personality_name(cls, v: str) -> str:
        from co_cli.prompts.personalities._validator import VALID_PERSONALITIES

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
            "library_path": "CO_LIBRARY_PATH",
            "theme": "CO_CLI_THEME",
            "reasoning_display": "CO_CLI_REASONING_DISPLAY",
            "personality": "CO_CLI_PERSONALITY",
            "tool_retries": "CO_CLI_TOOL_RETRIES",
            "doom_loop_threshold": "CO_CLI_DOOM_LOOP_THRESHOLD",
            "max_reflections": "CO_CLI_MAX_REFLECTIONS",
        }
        for field, env_var in flat_env_map.items():
            val = env_source.get(env_var)
            if val:
                data[field] = val

        # Nested fields
        nested_env_map: dict[str, dict[str, str]] = {
            "llm": {
                "api_key": "LLM_API_KEY",
                "provider": "LLM_PROVIDER",
                "host": "LLM_HOST",
                "model": "CO_LLM_MODEL",
                "num_ctx": "LLM_NUM_CTX",
                "ctx_warn_threshold": "CO_CTX_WARN_THRESHOLD",
                "ctx_overflow_threshold": "CO_CTX_OVERFLOW_THRESHOLD",
            },
            "knowledge": {
                "search_backend": "CO_KNOWLEDGE_SEARCH_BACKEND",
                "embedding_provider": "CO_KNOWLEDGE_EMBEDDING_PROVIDER",
                "embedding_model": "CO_KNOWLEDGE_EMBEDDING_MODEL",
                "embedding_dims": "CO_KNOWLEDGE_EMBEDDING_DIMS",
                "cross_encoder_reranker_url": "CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL",
                "embed_api_url": "CO_KNOWLEDGE_EMBED_API_URL",
                "chunk_size": "CO_CLI_KNOWLEDGE_CHUNK_SIZE",
                "chunk_overlap": "CO_CLI_KNOWLEDGE_CHUNK_OVERLAP",
            },
            "memory": {
                "recall_half_life_days": "CO_MEMORY_RECALL_HALF_LIFE_DAYS",
                "injection_max_chars": "CO_CLI_MEMORY_INJECTION_MAX_CHARS",
            },
            "subagent": {
                "scope_chars": "CO_CLI_SUBAGENT_SCOPE_CHARS",
                "max_requests_coder": "CO_CLI_SUBAGENT_MAX_REQUESTS_CODER",
                "max_requests_research": "CO_CLI_SUBAGENT_MAX_REQUESTS_RESEARCH",
                "max_requests_analysis": "CO_CLI_SUBAGENT_MAX_REQUESTS_ANALYSIS",
                "max_requests_thinking": "CO_CLI_SUBAGENT_MAX_REQUESTS_THINKING",
            },
            "shell": {
                "max_timeout": "CO_CLI_SHELL_MAX_TIMEOUT",
                "safe_commands": "CO_CLI_SHELL_SAFE_COMMANDS",
            },
            "web": {
                "fetch_allowed_domains": "CO_CLI_WEB_FETCH_ALLOWED_DOMAINS",
                "fetch_blocked_domains": "CO_CLI_WEB_FETCH_BLOCKED_DOMAINS",
                "http_max_retries": "CO_CLI_WEB_HTTP_MAX_RETRIES",
                "http_backoff_base_seconds": "CO_CLI_WEB_HTTP_BACKOFF_BASE_SECONDS",
                "http_backoff_max_seconds": "CO_CLI_WEB_HTTP_BACKOFF_MAX_SECONDS",
                "http_jitter_ratio": "CO_CLI_WEB_HTTP_JITTER_RATIO",
            },
            "observability": {
                "log_level": "CO_CLI_LOG_LEVEL",
                "log_max_size_mb": "CO_CLI_LOG_MAX_SIZE_MB",
                "log_backup_count": "CO_CLI_LOG_BACKUP_COUNT",
            },
        }
        for group, fields in nested_env_map.items():
            for field, env_var in fields.items():
                val = env_source.get(env_var)
                if val:
                    data.setdefault(group, {})[field] = val

        # MCP servers (flat — env override)
        mcp_env = env_source.get("CO_CLI_MCP_SERVERS")
        if mcp_env:
            data["mcp_servers"] = json.loads(mcp_env)

        return data

    def save(self):
        """Save current settings to settings.json"""
        with open(SETTINGS_FILE, "w") as f:
            f.write(self.model_dump_json(indent=2, exclude_none=True))


def find_project_config(_project_dir: Path | None = None) -> Path | None:
    """Return .co-cli/settings.json in the project dir if it exists, else None."""
    candidate = (_project_dir or Path.cwd()) / ".co-cli" / "settings.json"
    return candidate if candidate.is_file() else None


def load_config(
    _user_config_path: Path | None = None,
    _project_dir: Path | None = None,
    _env: dict[str, str] | None = None,
) -> Settings:
    """Load layered configuration: user config → project config → env vars."""
    data: dict[str, Any] = {}

    user_config = _user_config_path if _user_config_path is not None else SETTINGS_FILE
    if user_config.exists():
        with open(user_config) as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Error loading settings.json: {e}. Using defaults.")  # noqa: T201 — bootstrap error before logging is configured

    project_config = find_project_config(_project_dir)
    if project_config is not None:
        with open(project_config) as f:
            try:
                data = _deep_merge_settings(data, json.load(f))
            except Exception as e:
                print(f"Error loading project config {project_config}: {e}. Skipping.")  # noqa: T201 — bootstrap error before logging is configured

    context = {"env": _env} if _env is not None else None
    try:
        resolved = Settings.model_validate(data, context=context)
    except ValidationError as exc:
        loaded = [
            str(user_config) if user_config.exists() else None,
            str(project_config) if project_config else None,
        ]
        sources = [s for s in loaded if s]
        hint = f" — check: {', '.join(sources)}" if sources else ""
        raise ValueError(f"Invalid configuration{hint}:\n{exc}") from exc

    for warning in _validate_personality(resolved.personality):
        print(f"Warning: {warning}")  # noqa: T201 — personality validation warning at bootstrap, logger not yet configured

    return resolved


# Resolved project config path (None when no .co-cli/settings.json in cwd)
project_config_path: Path | None = find_project_config()

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
    """Lazy module attribute — ``from co_cli.config._core import settings`` works."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
