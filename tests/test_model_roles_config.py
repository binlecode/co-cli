"""Config + status tests for role-based model pref lists."""

import pytest

from co_cli.agents._factory import ModelRegistry, ResolvedModel
from co_cli.config import Settings, ModelEntry
from co_cli._status import get_status


def test_settings_reject_empty_reasoning_role() -> None:
    """role_models.reasoning must contain at least one model."""
    with pytest.raises(ValueError, match="role_models.reasoning"):
        Settings.model_validate({"role_models": {"reasoning": []}})


def test_settings_parses_role_model_env_lists(monkeypatch) -> None:
    """Per-role env vars parse as ordered model pref lists."""
    monkeypatch.setenv("CO_MODEL_ROLE_REASONING", "model-a, model-b")
    monkeypatch.setenv("CO_MODEL_ROLE_CODING", "coder-a,coder-b")
    parsed = Settings.model_validate({})
    assert parsed.role_models["reasoning"][0].model == "model-a"
    assert parsed.role_models["reasoning"][1].model == "model-b"
    assert parsed.role_models["coding"][0].model == "coder-a"
    assert parsed.role_models["coding"][1].model == "coder-b"


def test_settings_reject_unknown_role_key() -> None:
    """Unknown role_models keys are rejected at validation."""
    with pytest.raises(ValueError, match="bogus"):
        Settings.model_validate({"role_models": {"reasoning": ["x"], "bogus": ["y"]}})


def test_settings_parses_summarization_role_env(monkeypatch) -> None:
    """CO_MODEL_ROLE_SUMMARIZATION parsed as a role pref list."""
    monkeypatch.setenv("CO_MODEL_ROLE_REASONING", "main-model")
    monkeypatch.setenv("CO_MODEL_ROLE_SUMMARIZATION", "sum-a,sum-b")
    parsed = Settings.model_validate({})
    assert parsed.role_models["summarization"][0].model == "sum-a"
    assert parsed.role_models["summarization"][1].model == "sum-b"


def test_create_deps_role_models_has_model_entry() -> None:
    """create_deps() populates role_models with ModelEntry objects."""
    from co_cli.main import create_deps, settings

    original_roles = {k: list(v) for k, v in settings.role_models.items()}
    original_backend = settings.knowledge_search_backend
    try:
        settings.role_models = {
            "reasoning": [ModelEntry(model="main-model")],
            "summarization": [ModelEntry(model="sum-head"), ModelEntry(model="sum-fallback")],
        }
        settings.knowledge_search_backend = "grep"
        deps = create_deps()
        assert isinstance(deps.config.role_models["reasoning"][0], ModelEntry)
        assert deps.config.role_models["summarization"][0].model == "sum-head"
        assert deps.services.knowledge_index is None
        assert deps.config.knowledge_search_backend == "grep"
    finally:
        settings.role_models = original_roles
        settings.knowledge_search_backend = original_backend


def test_status_fast_fails_without_reasoning_models() -> None:
    """Healthcheck reports misconfigured when reasoning role is empty."""
    from co_cli._status import settings as status_settings

    original_roles = {k: list(v) for k, v in status_settings.role_models.items()}
    try:
        status_settings.role_models = {}
        info = get_status()
        assert info.llm_status == "misconfigured"
        assert "no reasoning model configured" in info.llm_provider
    finally:
        status_settings.role_models = original_roles


def test_model_entry_api_params_coercion() -> None:
    """Plain string coerces to ModelEntry; dict with api_params parses correctly."""
    # Plain string coercion
    s = Settings.model_validate({"role_models": {"reasoning": ["x"]}})
    assert isinstance(s.role_models["reasoning"][0], ModelEntry)
    assert s.role_models["reasoning"][0].model == "x"
    assert s.role_models["reasoning"][0].api_params == {}

    # Explicit ModelEntry dict with api_params
    s2 = Settings.model_validate({
        "role_models": {"reasoning": [{"model": "y", "api_params": {"think": False}}]}
    })
    assert s2.role_models["reasoning"][0].model == "y"
    assert s2.role_models["reasoning"][0].api_params == {"think": False}


@pytest.mark.parametrize(
    ("configured_backend", "allowed_resolved"),
    [
        ("fts5", {"fts5", "grep"}),
        ("hybrid", {"hybrid", "fts5", "grep"}),
    ],
)
def test_create_deps_backend_resolution_real_runtime(
    configured_backend: str,
    allowed_resolved: set[str],
) -> None:
    """Wake-up resolves to a real usable backend without mocks/stubs."""
    from co_cli.main import create_deps, settings

    original_backend = settings.knowledge_search_backend
    try:
        settings.knowledge_search_backend = configured_backend
        deps = create_deps()
        assert deps.config.knowledge_search_backend in allowed_resolved
        if deps.config.knowledge_search_backend == "grep":
            assert deps.services.knowledge_index is None
        else:
            assert deps.services.knowledge_index is not None
            deps.services.knowledge_index.close()
    finally:
        settings.knowledge_search_backend = original_backend


def test_model_registry_builds_from_config() -> None:
    """ModelRegistry.from_config() populates entries for configured roles."""
    from co_cli.deps import CoConfig
    from co_cli.config import settings as _settings

    config = CoConfig(
        role_models={"reasoning": [ModelEntry(model=_settings.role_models["reasoning"][0].model)]},
        llm_provider=_settings.llm_provider,
        ollama_host=_settings.ollama_host,
        ollama_num_ctx=_settings.ollama_num_ctx,
    )
    fallback_rm = ResolvedModel(model="fallback", settings=None)
    registry = ModelRegistry.from_config(config)

    result = registry.get("reasoning", fallback_rm)
    # Entry was built — not the fallback
    assert result is not fallback_rm
    # Has a non-None model
    assert result.model is not None
    assert registry.is_configured("reasoning") is True


def test_model_registry_get_fallback_when_unconfigured() -> None:
    """ModelRegistry.get() returns fallback when role is absent."""
    config_empty = type("Config", (), {"role_models": {}, "llm_provider": "ollama", "ollama_host": "", "ollama_num_ctx": 4096})()
    registry = ModelRegistry.from_config(config_empty)

    fallback_rm = ResolvedModel(model="fallback", settings=None)
    result = registry.get("analysis", fallback_rm)
    assert result is fallback_rm
    assert registry.is_configured("analysis") is False
