import os
import json
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

APP_NAME = "co-cli"
DEFAULT_OLLAMA_REASONING_MODEL = "qwen3:30b-a3b-thinking-2507-q8_0-agentic"
DEFAULT_GEMINI_REASONING_MODEL = "gemini-3-flash-preview"

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
    "wc", "sort", "uniq", "cut", "tr", "jq",
    # Output
    "echo", "printf",
    # System info
    "pwd", "whoami", "hostname", "uname", "date",
    "env", "which", "file", "stat", "id", "du", "df",
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


def _validate_personality(personality: str) -> list[str]:
    """Return startup warnings for missing personality files."""
    from co_cli.prompts.personalities._composer import validate_personality_files

    return validate_personality_files(personality)


WebDecision = Literal["allow", "ask", "deny"]


class WebPolicy(BaseModel):
    search: WebDecision = Field(default="allow")
    fetch: WebDecision = Field(default="allow")


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server (stdio or HTTP transport).

    Stdio: set ``command`` (required). Subprocess launched by pydantic-ai.
    HTTP:  set ``url`` instead. No subprocess — connects to a remote server.
    Exactly one of ``command`` or ``url`` must be provided.
    """
    command: str | None = Field(default=None, description="Executable to launch (e.g. 'npx', 'uvx', 'python'). Required for stdio transport.")
    url: str | None = Field(default=None, description="Remote server URL for HTTP transport (StreamableHTTP or SSE). Mutually exclusive with command.")
    args: list[str] = Field(default_factory=list, description="Command-line arguments (stdio only)")
    timeout: int = Field(default=5, ge=1, le=60)
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables passed to subprocess (stdio only)")
    approval: Literal["auto", "never"] = Field(default="auto")
    prefix: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_command_or_url(self) -> "MCPServerConfig":
        if self.url and self.command:
            raise ValueError("MCPServerConfig: 'url' and 'command' are mutually exclusive")
        if not self.url and not self.command:
            raise ValueError("MCPServerConfig requires either 'command' or 'url'")
        return self


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


VALID_MODEL_ROLES: frozenset[str] = frozenset(
    {"reasoning", "summarization", "coding", "research", "analysis"}
)


def get_role_head(model_roles: dict[str, list[str]], role: str) -> str:
    """Return the head model for a role chain, or empty string if absent/empty."""
    chain = model_roles.get(role, [])
    return chain[0] if chain else ""


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
    max_request_limit: int = Field(default=50)

    # Safety
    doom_loop_threshold: int = Field(default=3, ge=2, le=10)
    max_reflections: int = Field(default=3, ge=1, le=10)

    # Conversation memory
    tool_output_trim_chars: int = Field(default=2000)
    max_history_messages: int = Field(default=40)

    # Knowledge search backend
    knowledge_search_backend: Literal["grep", "fts5", "hybrid"] = Field(default="fts5")
    knowledge_embedding_provider: Literal["ollama", "gemini", "none"] = Field(default="ollama")
    knowledge_embedding_model: str = Field(default="embeddinggemma")
    knowledge_embedding_dims: int = Field(default=256, ge=1)
    knowledge_hybrid_vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    knowledge_hybrid_text_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    knowledge_reranker_provider: Literal["none", "ollama", "gemini", "local"] = Field(default="local")
    knowledge_reranker_model: str = Field(default="")

    # Memory lifecycle (notes with gravity)
    memory_max_count: int = Field(default=200, ge=10)
    memory_dedup_window_days: int = Field(default=7, ge=1)
    memory_dedup_threshold: int = Field(default=85, ge=0, le=100)
    # Temporal decay half-life for FTS5 recall scoring (days; larger = slower decay)
    memory_recall_half_life_days: int = Field(default=30, ge=1)
    # Consolidation: top-K related memories retrieved for contradiction resolution
    memory_consolidation_top_k: int = Field(default=5, ge=1)
    # Consolidation: per-call timeout budget (seconds) for extract_facts and resolve
    memory_consolidation_timeout_seconds: int = Field(default=20, ge=0)

    # Shell limits
    shell_max_timeout: int = Field(default=600)

    # Shell safe commands (auto-approved without prompting)
    shell_safe_commands: list[str] = Field(default=_DEFAULT_SAFE_COMMANDS)

    # Background task execution
    background_max_concurrent: int = Field(default=5, ge=1)
    background_task_retention_days: int = Field(default=7, ge=1)
    background_auto_cleanup: bool = Field(default=True)
    # Auto-cancel task if no output for N seconds; 0 = disabled
    background_task_inactivity_timeout: int = Field(default=0, ge=0)

    # Web domain policy
    web_fetch_allowed_domains: list[str] = Field(default=[])
    web_fetch_blocked_domains: list[str] = Field(default=[])
    web_policy: WebPolicy = Field(default_factory=WebPolicy)
    web_http_max_retries: int = Field(default=2, ge=0, le=10)
    web_http_backoff_base_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    web_http_backoff_max_seconds: float = Field(default=8.0, ge=0.5, le=120.0)
    web_http_jitter_ratio: float = Field(default=0.2, ge=0.0, le=1.0)

    # MCP servers (stdio or HTTP transport)
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

    @field_validator("model_roles", mode="before")
    @classmethod
    def _parse_model_roles(cls, v: dict[str, str | list[str]] | None) -> dict[str, list[str]]:
        if not v:
            return {}
        parsed: dict[str, list[str]] = {}
        for role, models in v.items():
            if isinstance(models, str):
                chain = [m.strip() for m in models.split(",") if m.strip()]
            else:
                chain = [str(m).strip() for m in models if str(m).strip()]
            parsed[str(role)] = chain
        return parsed

    @field_validator("personality")
    @classmethod
    def _validate_personality_name(cls, v: str) -> str:
        from co_cli.prompts.personalities._composer import VALID_PERSONALITIES

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

    @model_validator(mode="after")
    def _validate_reasoning_role(self) -> "Settings":
        chain = self.model_roles.get("reasoning", [])
        if not chain:
            raise ValueError(
                "model_roles.reasoning must contain at least one model"
            )
        return self

    @model_validator(mode="after")
    def _validate_model_role_keys(self) -> "Settings":
        unknown = set(self.model_roles.keys()) - VALID_MODEL_ROLES
        if unknown:
            raise ValueError(
                f"Unknown model_roles keys: {sorted(unknown)}. "
                f"Valid roles: {sorted(VALID_MODEL_ROLES)}"
            )
        return self

    # Session persistence TTL
    session_ttl_minutes: int = Field(default=60, ge=1)

    # Approval risk classifier
    approval_risk_enabled: bool = Field(default=False)
    approval_auto_low_risk: bool = Field(default=False)

    # Role model chains (ordered by preference within provider).
    # Mandatory role: reasoning (main agent).
    model_roles: dict[str, list[str]] = Field(default_factory=dict)

    # LLM Settings (Gemini / Ollama)
    gemini_api_key: Optional[str] = Field(default=None)
    llm_provider: str = Field(default="ollama")
    ollama_host: str = Field(default="http://localhost:11434")
    # IMPORTANT: Use -agentic Modelfile variants for models that need custom num_ctx.
    # Ollama's OpenAI-compatible API ignores num_ctx from request params — it MUST
    # be baked into the Modelfile via PARAMETER num_ctx. Base tags default to 4096
    # tokens and silently lose multi-turn conversation history.
    # See docs/DESIGN-llm-models.md for Modelfile setup.
    # Client-side num_ctx sent with every request. Currently ignored by Ollama's
    # OpenAI API (ollama/ollama#5356) — kept for documentation and future-proofing.
    ollama_num_ctx: int = Field(default=262144)
    ctx_warn_threshold: float = Field(default=0.85)
    ctx_overflow_threshold: float = Field(default=1.0)

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
            "doom_loop_threshold": "CO_CLI_DOOM_LOOP_THRESHOLD",
            "max_reflections": "CO_CLI_MAX_REFLECTIONS",
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
            "knowledge_search_backend": "CO_KNOWLEDGE_SEARCH_BACKEND",
            "knowledge_embedding_provider": "CO_KNOWLEDGE_EMBEDDING_PROVIDER",
            "knowledge_embedding_model": "CO_KNOWLEDGE_EMBEDDING_MODEL",
            "knowledge_embedding_dims": "CO_KNOWLEDGE_EMBEDDING_DIMS",
            "knowledge_reranker_provider": "CO_KNOWLEDGE_RERANKER_PROVIDER",
            "knowledge_reranker_model": "CO_KNOWLEDGE_RERANKER_MODEL",
            "memory_max_count": "CO_CLI_MEMORY_MAX_COUNT",
            "memory_dedup_window_days": "CO_CLI_MEMORY_DEDUP_WINDOW_DAYS",
            "memory_dedup_threshold": "CO_CLI_MEMORY_DEDUP_THRESHOLD",
            "memory_recall_half_life_days": "CO_MEMORY_RECALL_HALF_LIFE_DAYS",
            "memory_consolidation_top_k": "CO_MEMORY_CONSOLIDATION_TOP_K",
            "memory_consolidation_timeout_seconds": "CO_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS",
            "session_ttl_minutes": "CO_SESSION_TTL_MINUTES",
            "gemini_api_key": "GEMINI_API_KEY",
            "llm_provider": "LLM_PROVIDER",
            "ollama_host": "OLLAMA_HOST",
            "ollama_num_ctx": "OLLAMA_NUM_CTX",
            "approval_risk_enabled": "CO_CLI_APPROVAL_RISK_ENABLED",
            "approval_auto_low_risk": "CO_CLI_APPROVAL_AUTO_LOW_RISK",
            "ctx_warn_threshold": "CO_CTX_WARN_THRESHOLD",
            "ctx_overflow_threshold": "CO_CTX_OVERFLOW_THRESHOLD",
            "background_max_concurrent": "CO_BACKGROUND_MAX_CONCURRENT",
            "background_task_retention_days": "CO_BACKGROUND_TASK_RETENTION_DAYS",
            "background_auto_cleanup": "CO_BACKGROUND_AUTO_CLEANUP",
            "background_task_inactivity_timeout": "CO_BACKGROUND_TASK_INACTIVITY_TIMEOUT",
        }
        
        for field, env_var in env_map.items():
            val = os.getenv(env_var)
            if val:
                data[field] = val

        # Per-role model overrides — merge per-key, not whole-dict replacement.
        model_roles_env: dict[str, list[str]] = {}
        for role, env_var in [
            ("reasoning", "CO_MODEL_ROLE_REASONING"),
            ("summarization", "CO_MODEL_ROLE_SUMMARIZATION"),
            ("coding", "CO_MODEL_ROLE_CODING"),
            ("research", "CO_MODEL_ROLE_RESEARCH"),
            ("analysis", "CO_MODEL_ROLE_ANALYSIS"),
        ]:
            val = os.getenv(env_var)
            if val:
                model_roles_env[role] = [m.strip() for m in val.split(",") if m.strip()]
        if model_roles_env:
            existing_roles = data.get("model_roles", {})
            data["model_roles"] = {**existing_roles, **model_roles_env}
        elif "model_roles" not in data:
            provider = str(data.get("llm_provider", "ollama")).lower()
            default_model = (
                DEFAULT_GEMINI_REASONING_MODEL
                if provider == "gemini"
                else DEFAULT_OLLAMA_REASONING_MODEL
            )
            data["model_roles"] = {"reasoning": [default_model]}

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
    resolved = Settings.model_validate(data)

    # Non-blocking personality file diagnostics surfaced at startup.
    for warning in _validate_personality(resolved.personality):
        print(f"Warning: {warning}")

    return resolved


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
