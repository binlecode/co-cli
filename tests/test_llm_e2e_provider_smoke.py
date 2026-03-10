import asyncio
import os
from dataclasses import replace

import pytest

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.main import create_deps


def _make_deps(session_id: str = "test", personality: str = "finch") -> CoDeps:
    deps = create_deps()
    return replace(deps, config=replace(deps.config, session_id=session_id, personality=personality))


@pytest.mark.asyncio
async def test_agent_e2e_gemini():
    """Test a full round-trip to Gemini."""
    if os.getenv("LLM_PROVIDER") != "gemini":
        return

    agent, model_settings, _, _ = get_agent()
    async with asyncio.timeout(60):
        result = await agent.run("Reply with exactly 'OK'.", model_settings=model_settings)
    assert "OK" in result.output


def test_gemini_api_key_overrides_env():
    """Regression: settings gemini_api_key must overwrite a pre-existing GEMINI_API_KEY env var."""
    original_env = os.environ.get("GEMINI_API_KEY")
    original_key = settings.gemini_api_key
    original_provider = settings.llm_provider
    try:
        os.environ["GEMINI_API_KEY"] = "stale-key-from-env"
        settings.gemini_api_key = "settings-key-wins"
        settings.llm_provider = "gemini"

        get_agent()

        assert os.environ["GEMINI_API_KEY"] == "settings-key-wins"
    finally:
        settings.gemini_api_key = original_key
        settings.llm_provider = original_provider
        if original_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = original_env


@pytest.mark.asyncio
async def test_agent_e2e_ollama():
    """Test a full round-trip to Ollama."""
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    agent, model_settings, _, _ = get_agent()
    deps = _make_deps("test-e2e")
    async with asyncio.timeout(60):
        result = await agent.run(
            "Reply with exactly 'OK'.",
            deps=deps,
            model_settings=model_settings,
        )
    assert "OK" in result.output
