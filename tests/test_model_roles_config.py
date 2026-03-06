"""Config + status tests for role-based model chains."""

import pytest

from co_cli.config import Settings
from co_cli.status import get_status


def test_settings_reject_empty_reasoning_role() -> None:
    """model_roles.reasoning must contain at least one model."""
    with pytest.raises(ValueError, match="model_roles.reasoning"):
        Settings.model_validate({"model_roles": {"reasoning": []}})


def test_settings_parses_role_model_env_lists(monkeypatch) -> None:
    """Per-role env vars parse as ordered model chains."""
    monkeypatch.setenv("CO_MODEL_ROLE_REASONING", "model-a, model-b")
    monkeypatch.setenv("CO_MODEL_ROLE_CODING", "coder-a,coder-b")
    parsed = Settings.model_validate({})
    assert parsed.model_roles["reasoning"] == ["model-a", "model-b"]
    assert parsed.model_roles["coding"] == ["coder-a", "coder-b"]


def test_settings_reject_unknown_role_key() -> None:
    """Unknown model_roles keys are rejected at validation."""
    with pytest.raises(ValueError, match="bogus"):
        Settings.model_validate({"model_roles": {"reasoning": ["x"], "bogus": ["y"]}})


def test_settings_parses_summarization_role_env(monkeypatch) -> None:
    """CO_MODEL_ROLE_SUMMARIZATION parsed as a role chain."""
    monkeypatch.setenv("CO_MODEL_ROLE_REASONING", "main-model")
    monkeypatch.setenv("CO_MODEL_ROLE_SUMMARIZATION", "sum-a,sum-b")
    parsed = Settings.model_validate({})
    assert parsed.model_roles["summarization"] == ["sum-a", "sum-b"]


def test_create_deps_derives_summarization_model_from_role() -> None:
    """create_deps() populates summarization_model from model_roles[summarization] head."""
    from co_cli.main import create_deps, settings

    original_roles = {k: list(v) for k, v in settings.model_roles.items()}
    original_backend = settings.knowledge_search_backend
    try:
        settings.model_roles = {
            "reasoning": ["main-model"],
            "summarization": ["sum-head", "sum-fallback"],
        }
        # Deterministic no-index mode for this test.
        settings.knowledge_search_backend = "grep"
        deps = create_deps()
        assert deps.summarization_model == "sum-head"
        assert deps.knowledge_index is None
        assert deps.knowledge_search_backend == "grep"
    finally:
        settings.model_roles = original_roles
        settings.knowledge_search_backend = original_backend


def test_status_fast_fails_without_reasoning_models() -> None:
    """Healthcheck reports misconfigured when reasoning role is empty."""
    from co_cli.status import settings as status_settings

    original_roles = {k: list(v) for k, v in status_settings.model_roles.items()}
    try:
        status_settings.model_roles = {}
        info = get_status()
        assert info.llm_status == "misconfigured"
        assert "no reasoning model configured" in info.llm_provider
    finally:
        status_settings.model_roles = original_roles


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
        assert deps.knowledge_search_backend in allowed_resolved
        if deps.knowledge_search_backend == "grep":
            assert deps.knowledge_index is None
        else:
            assert deps.knowledge_index is not None
            deps.knowledge_index.close()
    finally:
        settings.knowledge_search_backend = original_backend
