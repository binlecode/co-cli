"""Shared test settings — real config from real load_config(), cached and overridable."""

from co_cli.config._core import Settings, load_config

_BASE: Settings | None = None


def make_settings(**overrides) -> Settings:
    """Return real production settings with optional surgical overrides.

    Loads once from load_config() (user + project + env vars, fully validated).
    Subsequent calls return the cached instance or a model_copy with overrides.
    """
    global _BASE
    if _BASE is None:
        _BASE = load_config()
    if not overrides:
        return _BASE
    return _BASE.model_copy(update=overrides)
