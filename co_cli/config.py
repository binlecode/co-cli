import os
import json
from pathlib import Path
from copy import deepcopy
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator, model_validator

APP_NAME = "co-cli"

DEFAULT_OLLAMA_NOREASON_MODEL: dict[str, Any] = {
    "model": "qwen3.5:35b-a3b-think",
    "provider": "ollama-openai",
    "api_params": {
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 16384,
        "reasoning_effort": "none",
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
        "num_ctx": 131072,
        "num_predict": 16384,
    },
}


DEFAULT_OLLAMA_REASONING_MODEL: dict[str, Any] = {
    "model": "qwen3.5:35b-a3b-think",
    "provider": "ollama-openai",
    "api_params": {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 32768,
        "num_ctx": 131072,
        "num_predict": 32768,
    },
}
# Summarization reuses the think model over the OpenAI-compatible Ollama API.
# Request-level reasoning_effort="none" suppresses reasoning output while keeping
# the same weights resident, avoiding an instruct-model swap.
DEFAULT_OLLAMA_SUMMARIZATION_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_OLLAMA_ANALYSIS_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_OLLAMA_CODING_MODEL: dict[str, Any] = {
    "model": "qwen3.5:35b-a3b-code",
    "provider": "ollama-openai",
}
DEFAULT_OLLAMA_RESEARCH_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_OLLAMA_TASK_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_GEMINI_REASONING_MODEL: dict[str, Any] = {
    "model": "gemini-3-flash-preview",
    "provider": "gemini",
}

# Conservative default safe commands for auto-approval.
# UX convenience — approval is the security boundary.
DEFAULT_SHELL_SAFE_COMMANDS: list[str] = [
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
GOOGLE_TOKEN_PATH = CONFIG_DIR / "google_token.json"
ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SEARCH_DB = DATA_DIR / "co-cli-search.db"
LOGS_DB = DATA_DIR / "co-cli-logs.db"


def _ensure_dirs() -> None:
    """Create config and data directories (idempotent)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


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
    command: str | None = Field(default=None, description="Executable to launch (e.g. 'npx', 'uvx', 'python'). Required for stdio transport.")
    url: str | None = Field(default=None, description="Remote server URL for HTTP transport (StreamableHTTP or SSE). Mutually exclusive with command.")
    args: list[str] = Field(default_factory=list, description="Command-line arguments (stdio only)")
    timeout: int = Field(default=5, ge=1, le=60)
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables passed to subprocess (stdio only)")
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
# GitHub token is resolved lazily in agent.py at session start, not here at import time.
_DEFAULT_MCP_SERVERS: dict[str, MCPServerConfig] = {
    "github": MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        approval="ask",
    ),
    "context7": MCPServerConfig(
        command="npx",
        args=["-y", "@upstash/context7-mcp@latest"],
        approval="auto",
    ),
}


class ModelConfig(BaseModel):
    """A single model entry in a role chain, with optional API parameters."""

    model: str
    api_params: dict[str, Any] = Field(default_factory=dict)
    provider: Literal["ollama-openai", "gemini"]


# Canonical role name constants. Used as keys in role_models and ModelRegistry lookups.
# Add a new constant here when adding a new role — do not use string literals elsewhere.
ROLE_REASONING = "reasoning"
ROLE_SUMMARIZATION = "summarization"
ROLE_CODING = "coding"
ROLE_RESEARCH = "research"
ROLE_ANALYSIS = "analysis"
ROLE_TASK = "task"

VALID_ROLE_NAMES: frozenset[str] = frozenset({
    ROLE_REASONING, ROLE_SUMMARIZATION, ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS, ROLE_TASK,
})

# Named defaults for Settings fields — used by Settings field definitions and
# CoConfig in deps.py so both share the same ground-truth values.
DEFAULT_THEME = "light"
DEFAULT_PERSONALITY = "finch"
DEFAULT_TOOL_RETRIES = 3
DEFAULT_DOOM_LOOP_THRESHOLD = 3
DEFAULT_MAX_REFLECTIONS = 3
DEFAULT_TOOL_OUTPUT_TRIM_CHARS = 2000
DEFAULT_KNOWLEDGE_SEARCH_BACKEND = "hybrid"
DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER = "tei"
DEFAULT_KNOWLEDGE_EMBEDDING_MODEL = "embeddinggemma"
DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024
DEFAULT_KNOWLEDGE_EMBED_API_URL = "http://127.0.0.1:8283"
DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL = "http://127.0.0.1:8282"
DEFAULT_MEMORY_MAX_COUNT = 200
DEFAULT_MEMORY_DEDUP_WINDOW_DAYS = 7
DEFAULT_MEMORY_DEDUP_THRESHOLD = 85
DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30
DEFAULT_MEMORY_CONSOLIDATION_TOP_K = 5
DEFAULT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS = 20
DEFAULT_MEMORY_INJECTION_MAX_CHARS = 2000
DEFAULT_MEMORY_AUTO_SAVE_TAGS: list[str] = ["correction", "preference"]
DEFAULT_SUBAGENT_SCOPE_CHARS = 120
DEFAULT_SUBAGENT_MAX_REQUESTS_CODER = 10
DEFAULT_SUBAGENT_MAX_REQUESTS_RESEARCH = 10
DEFAULT_SUBAGENT_MAX_REQUESTS_ANALYSIS = 8
DEFAULT_SUBAGENT_MAX_REQUESTS_THINKING = 3
DEFAULT_KNOWLEDGE_CHUNK_SIZE = 600
DEFAULT_KNOWLEDGE_CHUNK_OVERLAP = 80
DEFAULT_SHELL_MAX_TIMEOUT = 600
DEFAULT_WEB_HTTP_MAX_RETRIES = 2
DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS = 8.0
DEFAULT_WEB_HTTP_JITTER_RATIO = 0.2

DEFAULT_LLM_PROVIDER = "ollama-openai"
DEFAULT_LLM_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_NUM_CTX = 262144
DEFAULT_CTX_WARN_THRESHOLD = 0.85
DEFAULT_CTX_OVERFLOW_THRESHOLD = 1.0
REASONING_DISPLAY_OFF = "off"
REASONING_DISPLAY_SUMMARY = "summary"
REASONING_DISPLAY_FULL = "full"
DEFAULT_REASONING_DISPLAY = REASONING_DISPLAY_SUMMARY
VALID_REASONING_DISPLAY_MODES: frozenset[str] = frozenset({REASONING_DISPLAY_OFF, REASONING_DISPLAY_SUMMARY, REASONING_DISPLAY_FULL})


class Settings(BaseModel):
    # Core Tools
    obsidian_vault_path: Optional[str] = Field(default=None)
    brave_search_api_key: Optional[str] = Field(default=None)
    google_credentials_path: Optional[str] = Field(default=None)
    # User-global library path (default: ~/.local/share/co-cli/library/)
    library_path: Optional[str] = Field(default=None)
    
    # Behavior
    theme: str = Field(default=DEFAULT_THEME)
    reasoning_display: Literal["off", "summary", "full"] = Field(default=DEFAULT_REASONING_DISPLAY)
    personality: str = Field(default=DEFAULT_PERSONALITY)
    tool_retries: int = Field(default=DEFAULT_TOOL_RETRIES)

    # Safety
    doom_loop_threshold: int = Field(default=DEFAULT_DOOM_LOOP_THRESHOLD, ge=2, le=10)
    max_reflections: int = Field(default=DEFAULT_MAX_REFLECTIONS, ge=1, le=10)

    # Conversation memory
    tool_output_trim_chars: int = Field(default=DEFAULT_TOOL_OUTPUT_TRIM_CHARS)

    # Knowledge search backend
    knowledge_search_backend: Literal["grep", "fts5", "hybrid"] = Field(default=DEFAULT_KNOWLEDGE_SEARCH_BACKEND)
    knowledge_embedding_provider: Literal["ollama", "gemini", "tei", "none"] = Field(default=DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER)
    knowledge_embedding_model: str = Field(default=DEFAULT_KNOWLEDGE_EMBEDDING_MODEL)
    knowledge_embedding_dims: int = Field(default=DEFAULT_KNOWLEDGE_EMBEDDING_DIMS, ge=1)
    knowledge_cross_encoder_reranker_url: str | None = Field(default=DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL)
    knowledge_llm_reranker: ModelConfig | None = Field(default=None)
    knowledge_embed_api_url: str = Field(default=DEFAULT_KNOWLEDGE_EMBED_API_URL)

    # Memory lifecycle (notes with gravity)
    memory_max_count: int = Field(default=DEFAULT_MEMORY_MAX_COUNT, ge=10)
    memory_dedup_window_days: int = Field(default=DEFAULT_MEMORY_DEDUP_WINDOW_DAYS, ge=1)
    memory_dedup_threshold: int = Field(default=DEFAULT_MEMORY_DEDUP_THRESHOLD, ge=0, le=100)
    # Temporal decay half-life for FTS5 recall scoring (days; larger = slower decay)
    memory_recall_half_life_days: int = Field(default=DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS, ge=1)
    # Consolidation: top-K related memories retrieved for contradiction resolution
    memory_consolidation_top_k: int = Field(default=DEFAULT_MEMORY_CONSOLIDATION_TOP_K, ge=1)
    # Consolidation: per-call timeout budget (seconds) for extract_facts and resolve
    memory_consolidation_timeout_seconds: int = Field(default=DEFAULT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS, ge=0)
    # Auto-save allowlist: only signals with these tags are saved without prompting
    memory_auto_save_tags: list[str] = Field(default=DEFAULT_MEMORY_AUTO_SAVE_TAGS)
    # Max characters injected into context from memory recall per turn
    memory_injection_max_chars: int = Field(default=DEFAULT_MEMORY_INJECTION_MAX_CHARS, ge=100)
    # Max chars of the delegated task/query shown in delegation result scope line
    subagent_scope_chars: int = Field(default=DEFAULT_SUBAGENT_SCOPE_CHARS, ge=10)
    # Per-role default request budgets for subagent calls
    subagent_max_requests_coder: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_CODER, ge=1)
    subagent_max_requests_research: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_RESEARCH, ge=1)
    subagent_max_requests_analysis: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_ANALYSIS, ge=1)
    subagent_max_requests_thinking: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_THINKING, ge=1)

    # Knowledge chunking
    knowledge_chunk_size: int = Field(default=DEFAULT_KNOWLEDGE_CHUNK_SIZE, ge=0)
    knowledge_chunk_overlap: int = Field(default=DEFAULT_KNOWLEDGE_CHUNK_OVERLAP, ge=0)

    # Shell limits
    shell_max_timeout: int = Field(default=DEFAULT_SHELL_MAX_TIMEOUT)

    # Shell safe commands (auto-approved without prompting)
    shell_safe_commands: list[str] = Field(default=DEFAULT_SHELL_SAFE_COMMANDS)

    # Web domain policy
    web_fetch_allowed_domains: list[str] = Field(default=[])
    web_fetch_blocked_domains: list[str] = Field(default=[])
    web_http_max_retries: int = Field(default=DEFAULT_WEB_HTTP_MAX_RETRIES, ge=0, le=10)
    web_http_backoff_base_seconds: float = Field(default=DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS, ge=0.0, le=30.0)
    web_http_backoff_max_seconds: float = Field(default=DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS, ge=0.5, le=120.0)
    web_http_jitter_ratio: float = Field(default=DEFAULT_WEB_HTTP_JITTER_RATIO, ge=0.0, le=1.0)

    # MCP servers (stdio or HTTP transport)
    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=lambda: _DEFAULT_MCP_SERVERS.copy()
    )

    @field_validator("memory_auto_save_tags", mode="before")
    @classmethod
    def _parse_memory_auto_save_tags(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

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

    @field_validator("role_models", mode="before")
    @classmethod
    def _parse_role_models(cls, v: dict[str, Any] | None) -> dict[str, dict]:
        if not v:
            return {}
        parsed: dict[str, dict] = {}
        for role, model in v.items():
            if isinstance(model, str):
                raise ValueError(
                    f"role_models.{role} must be an object with explicit 'model' and 'provider' keys"
                )
            elif isinstance(model, dict):
                parsed[str(role)] = model
            else:
                # Already a ModelConfig instance (re-validation path)
                parsed[str(role)] = model.model_dump() if hasattr(model, "model_dump") else {"model": str(model)}
        return parsed

    @field_validator("personality")
    @classmethod
    def _validate_personality_name(cls, v: str) -> str:
        from co_cli.prompts.personalities._validator import VALID_PERSONALITIES

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
        if not self.role_models.get(ROLE_REASONING):
            raise ValueError(
                "role_models.reasoning must be configured"
            )
        return self

    @model_validator(mode="after")
    def _validate_model_role_keys(self) -> "Settings":
        unknown = set(self.role_models.keys()) - VALID_ROLE_NAMES
        if unknown:
            raise ValueError(
                f"Unknown role_models keys: {sorted(unknown)}. "
                f"Valid roles: {sorted(VALID_ROLE_NAMES)}"
            )
        return self

    # Role model: one model per role. Mandatory role: reasoning (main agent).
    role_models: dict[str, ModelConfig] = Field(default_factory=dict)

    # LLM Settings (Gemini / Ollama)
    llm_api_key: Optional[str] = Field(default=None)
    llm_provider: str = Field(default=DEFAULT_LLM_PROVIDER)
    llm_host: str = Field(default=DEFAULT_LLM_HOST)
    # IMPORTANT: Use -agentic Modelfile variants for models that need custom num_ctx.
    # Ollama's OpenAI-compatible API ignores num_ctx from request params — it MUST
    # be baked into the Modelfile via PARAMETER num_ctx. Base tags default to 4096
    # tokens and silently lose multi-turn conversation history.
    # See docs/DESIGN-llm-models.md for Modelfile setup.
    # Client-side num_ctx sent with every request. Currently ignored by Ollama's
    # OpenAI API (ollama/ollama#5356) — kept for documentation and future-proofing.
    llm_num_ctx: int = Field(default=DEFAULT_OLLAMA_NUM_CTX)
    ctx_warn_threshold: float = Field(default=DEFAULT_CTX_WARN_THRESHOLD)
    ctx_overflow_threshold: float = Field(default=DEFAULT_CTX_OVERFLOW_THRESHOLD)

    @model_validator(mode='before')
    @classmethod
    def fill_from_env(cls, data: dict, info: ValidationInfo) -> dict:
        """Env vars override all file-based values (highest precedence layer)."""
        env_source: dict = (info.context or {}).get("env") if info.context else None
        if env_source is None:
            env_source = os.environ
        env_map = {
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
            "shell_max_timeout": "CO_CLI_SHELL_MAX_TIMEOUT",
            "shell_safe_commands": "CO_CLI_SHELL_SAFE_COMMANDS",
            "web_fetch_allowed_domains": "CO_CLI_WEB_FETCH_ALLOWED_DOMAINS",
            "web_fetch_blocked_domains": "CO_CLI_WEB_FETCH_BLOCKED_DOMAINS",
            "web_http_max_retries": "CO_CLI_WEB_HTTP_MAX_RETRIES",
            "web_http_backoff_base_seconds": "CO_CLI_WEB_HTTP_BACKOFF_BASE_SECONDS",
            "web_http_backoff_max_seconds": "CO_CLI_WEB_HTTP_BACKOFF_MAX_SECONDS",
            "web_http_jitter_ratio": "CO_CLI_WEB_HTTP_JITTER_RATIO",
            "tool_output_trim_chars": "CO_CLI_TOOL_OUTPUT_TRIM_CHARS",
            "knowledge_search_backend": "CO_KNOWLEDGE_SEARCH_BACKEND",
            "knowledge_embedding_provider": "CO_KNOWLEDGE_EMBEDDING_PROVIDER",
            "knowledge_embedding_model": "CO_KNOWLEDGE_EMBEDDING_MODEL",
            "knowledge_embedding_dims": "CO_KNOWLEDGE_EMBEDDING_DIMS",
            "knowledge_cross_encoder_reranker_url": "CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL",
            "knowledge_embed_api_url": "CO_KNOWLEDGE_EMBED_API_URL",
            "memory_max_count": "CO_CLI_MEMORY_MAX_COUNT",
            "memory_dedup_window_days": "CO_CLI_MEMORY_DEDUP_WINDOW_DAYS",
            "memory_dedup_threshold": "CO_CLI_MEMORY_DEDUP_THRESHOLD",
            "memory_recall_half_life_days": "CO_MEMORY_RECALL_HALF_LIFE_DAYS",
            "memory_consolidation_top_k": "CO_MEMORY_CONSOLIDATION_TOP_K",
            "memory_consolidation_timeout_seconds": "CO_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS",
            "memory_auto_save_tags": "CO_CLI_MEMORY_AUTO_SAVE_TAGS",
            "memory_injection_max_chars": "CO_CLI_MEMORY_INJECTION_MAX_CHARS",
            "subagent_scope_chars": "CO_CLI_SUBAGENT_SCOPE_CHARS",
            "subagent_max_requests_coder": "CO_CLI_SUBAGENT_MAX_REQUESTS_CODER",
            "subagent_max_requests_research": "CO_CLI_SUBAGENT_MAX_REQUESTS_RESEARCH",
            "subagent_max_requests_analysis": "CO_CLI_SUBAGENT_MAX_REQUESTS_ANALYSIS",
            "subagent_max_requests_thinking": "CO_CLI_SUBAGENT_MAX_REQUESTS_THINKING",
            "knowledge_chunk_size": "CO_CLI_KNOWLEDGE_CHUNK_SIZE",
            "knowledge_chunk_overlap": "CO_CLI_KNOWLEDGE_CHUNK_OVERLAP",

            "llm_api_key": "LLM_API_KEY",
            "llm_provider": "LLM_PROVIDER",
            "llm_host": "LLM_HOST",
            "llm_num_ctx": "LLM_NUM_CTX",
            "ctx_warn_threshold": "CO_CTX_WARN_THRESHOLD",
            "ctx_overflow_threshold": "CO_CTX_OVERFLOW_THRESHOLD",
        }
        
        for field, env_var in env_map.items():
            val = env_source.get(env_var)
            if val:
                data[field] = val

        provider = str(data.get("llm_provider", "ollama-openai")).lower()
        if provider == "gemini":
            role_defaults: dict = {ROLE_REASONING: DEFAULT_GEMINI_REASONING_MODEL}
        elif provider == "ollama-openai":
            role_defaults = {
                ROLE_REASONING: DEFAULT_OLLAMA_REASONING_MODEL,
                ROLE_SUMMARIZATION: DEFAULT_OLLAMA_SUMMARIZATION_MODEL,
                ROLE_ANALYSIS: DEFAULT_OLLAMA_ANALYSIS_MODEL,
                ROLE_CODING: DEFAULT_OLLAMA_CODING_MODEL,
                ROLE_RESEARCH: DEFAULT_OLLAMA_RESEARCH_MODEL,
                ROLE_TASK: DEFAULT_OLLAMA_TASK_MODEL,
            }
        else:
            raise ValueError(f"Unsupported llm_provider: {provider!r}")
        # Per-role model overrides — merge per-key, not whole-dict replacement.
        # Env vars use plain model-name strings, so inject the session provider explicitly.
        role_models_env: dict[str, dict[str, Any]] = {}
        for role in (ROLE_REASONING, ROLE_SUMMARIZATION, ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS, ROLE_TASK):
            env_var = f"CO_MODEL_ROLE_{role.upper()}"
            val = env_source.get(env_var)
            if val:
                role_models_env[role] = {"model": val.strip(), "provider": provider}
        # Merge: defaults supply missing roles; explicit config and env vars override.
        existing_roles = data.get("role_models", {})
        data["role_models"] = {**role_defaults, **existing_roles, **role_models_env}

        mcp_env = env_source.get("CO_CLI_MCP_SERVERS")
        if mcp_env:
            data["mcp_servers"] = json.loads(mcp_env)

        return data

    def save(self):
        """Save current settings to settings.json"""
        with open(SETTINGS_FILE, "w") as f:
            f.write(self.model_dump_json(indent=2, exclude_none=True))

def find_project_config(_project_dir: Path | None = None) -> Path | None:
    """Return .co-cli/settings.json in the project dir if it exists, else None.

    Args:
        _project_dir: Override the project directory. Defaults to Path.cwd().
    """
    candidate = (_project_dir or Path.cwd()) / ".co-cli" / "settings.json"
    return candidate if candidate.is_file() else None


def load_config(
    _user_config_path: Path | None = None,
    _project_dir: Path | None = None,
    _env: dict[str, str] | None = None,
) -> Settings:
    """Load layered configuration: user config → project config → env vars.

    Args:
        _user_config_path: Override the user settings file path. Defaults to SETTINGS_FILE.
        _project_dir: Override the project directory for project config lookup. Defaults to cwd.
        _env: Override environment variables. Defaults to os.environ. Use in tests to inject
              env vars without mutating os.environ.
    """
    data: dict[str, Any] = {}

    # Layer 1: User config (~/.config/co-cli/settings.json)
    user_config = _user_config_path if _user_config_path is not None else SETTINGS_FILE
    if user_config.exists():
        with open(user_config, "r") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Error loading settings.json: {e}. Using defaults.")

    # Layer 2: Project config (<cwd>/.co-cli/settings.json) — deep merge
    project_config = find_project_config(_project_dir)
    if project_config is not None:
        with open(project_config, "r") as f:
            try:
                data = _deep_merge_settings(data, json.load(f))
            except Exception as e:
                print(f"Error loading project config {project_config}: {e}. Skipping.")

    # Layer 3: Env vars (handled by fill_from_env model_validator)
    context = {"env": _env} if _env is not None else None
    try:
        resolved = Settings.model_validate(data, context=context)
    except ValidationError as exc:
        loaded = [str(user_config) if user_config.exists() else None,
                  str(project_config) if project_config else None]
        sources = [s for s in loaded if s]
        hint = f" — check: {', '.join(sources)}" if sources else ""
        raise ValueError(f"Invalid configuration{hint}:\n{exc}") from exc

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
        try:
            _settings = load_config()
        except ValueError as e:
            # load_config() raises ValueError for schema errors with file attribution.
            # Print cleanly and exit — consumers (display.py) access settings at import time.
            import sys
            print(f"Configuration error: {e}", file=sys.stderr)
            raise SystemExit(1)
    return _settings


def __getattr__(name: str):
    """Lazy module attribute — ``from co_cli.config import settings`` works without import-time side effects."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
