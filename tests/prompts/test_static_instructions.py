"""Tests for build_static_instructions — static system prompt assembly."""

import pytest
from tests._settings import make_settings

from co_cli.prompts._assembly import build_static_instructions


@pytest.fixture(autouse=True)
def _clear_personality_cache():
    """Reset the process-scoped personality memory cache before and after each test."""
    import co_cli.prompts.personalities._loader as _loader_mod

    _loader_mod._personality_cache = None
    yield
    _loader_mod._personality_cache = None


def test_static_instructions_includes_personality_memories():
    """When personality memories are present, they appear in the static instructions string."""
    import co_cli.prompts.personalities._loader as _loader_mod

    sentinel = "## Learned Context\n\n- personality-static-sentinel-XYZ789"
    _loader_mod._personality_cache = sentinel

    config = make_settings().model_copy(update={"personality": "finch"})
    result = build_static_instructions(config)

    assert "personality-static-sentinel-XYZ789" in result, (
        f"personality memories missing from static instructions; got excerpt: {result[:200]!r}"
    )
