import os
import json
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

APP_NAME = "co-cli"

# Conservative default safe commands for auto-approval.
# UX convenience — approval is the security boundary.
_DEFAULT_SAFE_COMMANDS: list[str] = [
    # Filesystem listing
    "ls", "tree", "find", "fd",
    # File reading
    "cat", "head", "tail",
    # Search
    "grep", "rg", "ag",
    # Text processing (read-only)
    "wc", "sort", "uniq", "cut", "jq",
    # Output
    "echo", "printf",
    # System info
    "pwd", "whoami", "hostname", "uname", "date",
    "env", "which", "file", "id", "du", "df",
    # Git read-only (prefix match: "git status", "git diff", etc.)
    "git status", "git diff", "git log", "git show",
    "git branch", "git tag", "git blame",
]

# XDG Paths - Explicit XDG resolution so ~/.config/ is used even on macOS
# (platformdirs would resolve to ~/Library/Application Support/ on macOS)
CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
DATA_DIR = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
SETTINGS_FILE = CONFIG_DIR / "settings.json"


def _ensure_dirs() -> None:
    """Create config and data directories (idempotent)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


WebDecision = Literal["allow", "ask", "deny"]


class WebPolicy(BaseModel):
    search: WebDecision = Field(default="allow")
    fetch: WebDecision = Field(default="allow")


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server (stdio transport)."""
    command: str = Field(description="Executable to launch (e.g. 'npx', 'uvx', 'python')")
    args: list[str] = Field(default_factory=list)
    timeout: int = Field(default=10, ge=1, le=60)
    env: dict[str, str] = Field(default_factory=dict)
    approval: Literal["auto", "never"] = Field(default="auto")
    prefix: str | None = Field(default=None)


# Default MCP servers — shipped out-of-the-box, skip gracefully when npx absent.
# GitHub token is resolved lazily in agent.py at session start, not here at import time.
_DEFAULT_MCP_SERVERS: dict[str, MCPServerConfig] = {
    "github": MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        approval="auto",
    ),
    "thinking": MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        approval="never",
    ),
    "context7": MCPServerConfig(
        command="npx",
        args=["-y", "@upstash/context7-mcp@latest"],
        approval="never",
    ),
}


class Settings(BaseModel):
    # Core Tools
    obsidian_vault_path: Optional[str] = Field(default=None)
    brave_search_api_key: Optional[str] = Field(default=None)
    google_credentials_path: Optional[str] = Field(default=None)
    
    # Behavior
    theme: str = Field(default="light")
    personality: str = Field(default="finch")
    tool_retries: int = Field(default=3)
    model_http_retries: int = Field(default=2)
    max_request_limit: int = Field(default=25)

    # Conversation memory
    tool_output_trim_chars: int = Field(default=2000)
    max_history_messages: int = Field(default=40)
    # Empty = fall back to agent's primary model
    summarization_model: str = Field(default="")

    # Memory lifecycle (notes with gravity)
    memory_max_count: int = Field(default=200, ge=10)
    memory_dedup_window_days: int = Field(default=7, ge=1)
    memory_dedup_threshold: int = Field(default=85, ge=0, le=100)
    memory_decay_strategy: Literal["summarize", "cut"] = Field(default="summarize")
    memory_decay_percentage: float = Field(default=0.2, ge=0.0, le=1.0)

    # Shell limits
    shell_max_timeout: int = Field(default=600)

    # Shell safe commands (auto-approved without prompting)
    shell_safe_commands: list[str] = Field(default=_DEFAULT_SAFE_COMMANDS)

    # Web domain policy
    web_fetch_allowed_domains: list[str] = Field(default=[])
    web_fetch_blocked_domains: list[str] = Field(default=[])
    web_policy: WebPolicy = Field(default_factory=WebPolicy)
    web_http_max_retries: int = Field(default=2, ge=0, le=10)
    web_http_backoff_base_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    web_http_backoff_max_seconds: float = Field(default=8.0, ge=0.5, le=120.0)
    web_http_jitter_ratio: float = Field(default=0.2, ge=0.0, le=1.0)

    # MCP servers (stdio transport)
    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=lambda: _DEFAULT_MCP_SERVERS.copy()
    )

    @field_validator("shell_safe_commands", mode="before")
    @classmethod
    def _parse_safe_commands(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("web_fetch_allowed_domains", "web_fetch_blocked_domains", mode="before")
    @classmethod
    def _parse_web_domains(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        return [s.lower() for s in v]

    @field_validator("personality")
    @classmethod
    def _validate_personality(cls, v: str) -> str:
        from co_cli.prompts.personalities._registry import VALID_PERSONALITIES

        if v not in VALID_PERSONALITIES:
            raise ValueError(f"personality must be one of {VALID_PERSONALITIES}, got: {v}")
        return v

    @model_validator(mode="after")
    def _validate_web_retry_bounds(self) -> "Settings":
        if self.web_http_backoff_base_seconds > self.web_http_backoff_max_seconds:
            raise ValueError(
                "web_http_backoff_base_seconds must be <= web_http_backoff_max_seconds"
            )
        return self

    # LLM Settings (Gemini / Ollama)
    gemini_api_key: Optional[str] = Field(default=None)
    llm_provider: str = Field(default="gemini")
    ollama_host: str = Field(default="http://localhost:11434")
    # IMPORTANT: Use -agentic Modelfile variants for models that need custom num_ctx.
    # Ollama's OpenAI-compatible API ignores num_ctx from request params — it MUST
    # be baked into the Modelfile via PARAMETER num_ctx. Base tags default to 2048
    # tokens and silently lose multi-turn conversation history.
    # See docs/GUIDE-ollama-local-setup.md for Modelfile setup.
    ollama_model: str = Field(default="qwen3:30b-a3b-thinking-2507-q8_0-agentic")
    # Client-side num_ctx sent with every request. Currently ignored by Ollama's
    # OpenAI API (ollama/ollama#5356) — kept for documentation and future-proofing.
    ollama_num_ctx: int = Field(default=262144)
    gemini_model: str = Field(default="gemini-2.0-flash")

    @model_validator(mode='before')
    @classmethod
    def fill_from_env(cls, data: dict) -> dict:
        """Env vars override all file-based values (highest precedence layer)."""
        env_map = {
            "obsidian_vault_path": "OBSIDIAN_VAULT_PATH",
            "brave_search_api_key": "BRAVE_SEARCH_API_KEY",
            "google_credentials_path": "GOOGLE_CREDENTIALS_PATH",
            "theme": "CO_CLI_THEME",
            "personality": "CO_CLI_PERSONALITY",
            "tool_retries": "CO_CLI_TOOL_RETRIES",
            "model_http_retries": "CO_CLI_MODEL_HTTP_RETRIES",
            "max_request_limit": "CO_CLI_MAX_REQUEST_LIMIT",
            "shell_max_timeout": "CO_CLI_SHELL_MAX_TIMEOUT",
            "shell_safe_commands": "CO_CLI_SHELL_SAFE_COMMANDS",
            "web_fetch_allowed_domains": "CO_CLI_WEB_FETCH_ALLOWED_DOMAINS",
            "web_fetch_blocked_domains": "CO_CLI_WEB_FETCH_BLOCKED_DOMAINS",
            "web_http_max_retries": "CO_CLI_WEB_HTTP_MAX_RETRIES",
            "web_http_backoff_base_seconds": "CO_CLI_WEB_HTTP_BACKOFF_BASE_SECONDS",
            "web_http_backoff_max_seconds": "CO_CLI_WEB_HTTP_BACKOFF_MAX_SECONDS",
            "web_http_jitter_ratio": "CO_CLI_WEB_HTTP_JITTER_RATIO",
            "tool_output_trim_chars": "CO_CLI_TOOL_OUTPUT_TRIM_CHARS",
            "max_history_messages": "CO_CLI_MAX_HISTORY_MESSAGES",
            "summarization_model": "CO_CLI_SUMMARIZATION_MODEL",
            "memory_max_count": "CO_CLI_MEMORY_MAX_COUNT",
            "memory_dedup_window_days": "CO_CLI_MEMORY_DEDUP_WINDOW_DAYS",
            "memory_dedup_threshold": "CO_CLI_MEMORY_DEDUP_THRESHOLD",
            "memory_decay_strategy": "CO_CLI_MEMORY_DECAY_STRATEGY",
            "memory_decay_percentage": "CO_CLI_MEMORY_DECAY_PERCENTAGE",
            "gemini_api_key": "GEMINI_API_KEY",
            "llm_provider": "LLM_PROVIDER",
            "ollama_host": "OLLAMA_HOST",
            "ollama_model": "OLLAMA_MODEL",
            "ollama_num_ctx": "OLLAMA_NUM_CTX",
            "gemini_model": "GEMINI_MODEL",
        }
        
        for field, env_var in env_map.items():
            val = os.getenv(env_var)
            if val:
                data[field] = val

        mcp_env = os.getenv("CO_CLI_MCP_SERVERS")
        if mcp_env:
            data["mcp_servers"] = json.loads(mcp_env)

        web_policy_search = os.getenv("CO_CLI_WEB_POLICY_SEARCH")
        web_policy_fetch = os.getenv("CO_CLI_WEB_POLICY_FETCH")
        if web_policy_search or web_policy_fetch:
            policy_data = data.get("web_policy", {})
            if not isinstance(policy_data, dict):
                policy_data = {}
            if web_policy_search:
                policy_data["search"] = web_policy_search
            if web_policy_fetch:
                policy_data["fetch"] = web_policy_fetch
            data["web_policy"] = policy_data
        return data

    def save(self):
        """Save current settings to settings.json"""
        with open(SETTINGS_FILE, "w") as f:
            f.write(self.model_dump_json(indent=2, exclude_none=True))

def find_project_config() -> Path | None:
    """Return .co-cli/settings.json in cwd if it exists, else None."""
    candidate = Path.cwd() / ".co-cli" / "settings.json"
    return candidate if candidate.is_file() else None


def load_config() -> Settings:
    data: dict = {}

    # Layer 1: User config (~/.config/co-cli/settings.json)
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Error loading settings.json: {e}. Using defaults.")

    # Layer 2: Project config (<cwd>/.co-cli/settings.json) — shallow merge
    project_config = find_project_config()
    if project_config is not None:
        with open(project_config, "r") as f:
            try:
                data |= json.load(f)
            except Exception as e:
                print(f"Error loading project config {project_config}: {e}. Skipping.")

    # Layer 3: Env vars (handled by fill_from_env model_validator)
    return Settings.model_validate(data)


# Resolved project config path (None when no .co-cli/settings.json in cwd)
project_config_path: Path | None = find_project_config()

# Lazy settings singleton — directories created on first access, not at import time.
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the global Settings instance, creating it on first call."""
    global _settings
    if _settings is None:
        _ensure_dirs()
        _settings = load_config()
    return _settings


def __getattr__(name: str):
    """Lazy module attribute — ``from co_cli.config import settings`` works without import-time side effects."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
