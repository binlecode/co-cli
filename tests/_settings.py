"""Shared test settings — real config from real load_config(), cached and overridable."""

from co_cli.config._core import Settings, load_config
from co_cli.config.llm import DEFAULT_LLM_HOST, DEFAULT_LLM_MODEL, LlmSettings

# Suite-level LLM — explicit, not read from user's settings.json.
# Change here to switch all LLM-bearing tests to a different model.
TEST_LLM = LlmSettings(provider="ollama", model=DEFAULT_LLM_MODEL, host=DEFAULT_LLM_HOST)

_BASE: Settings | None = None


def make_settings(**overrides) -> Settings:
    """Return real production settings with TEST_LLM injected and optional surgical overrides.

    Loads once from load_config() (user + project + env vars, fully validated).
    Always injects TEST_LLM as the llm settings unless the caller explicitly passes llm=...
    """
    global _BASE
    if _BASE is None:
        _BASE = load_config()
    return _BASE.model_copy(update={"llm": TEST_LLM, **overrides})


# Suite-level singletons for direct import — avoids repeated make_settings() calls across modules.
SETTINGS = make_settings()

# Excludes MCP servers so agent.run() calls don't spawn their processes inline per call.
SETTINGS_NO_MCP = make_settings(mcp_servers={})
