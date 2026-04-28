"""Shared test settings — real config from real load_config(), cached and overridable."""

from co_cli.config.core import Settings, load_config

_BASE: Settings | None = None


def _load_base() -> Settings:
    global _BASE
    if _BASE is None:
        _BASE = load_config()
    return _BASE


# Suite-level LLM — read from user's real config (provider, model, host from settings.json).
# To override the model used by @pytest.mark.local tests, set llm.model in ~/.co-cli/settings.json.
TEST_LLM = _load_base().llm


def make_settings(**overrides) -> Settings:
    """Return real production settings with optional surgical overrides.

    Loads once from load_config() (user + project + env vars, fully validated).
    """
    return _load_base().model_copy(update=overrides)


# Suite-level singletons for direct import — avoids repeated make_settings() calls across modules.
SETTINGS = make_settings()

# Excludes MCP servers so agent.run() calls don't spawn their processes inline per call.
SETTINGS_NO_MCP = make_settings(mcp_servers={})
