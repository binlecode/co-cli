import os

import pytest
from pydantic_ai import DeferredToolRequests

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox


@pytest.mark.asyncio
async def test_agent_e2e_gemini():
    """Test a full round-trip to Gemini.
    Requires LLM_PROVIDER=gemini and GEMINI_API_KEY set.
    """
    if os.getenv("LLM_PROVIDER") != "gemini":
        return  # Not targeting Gemini this run

    agent, model_settings, _ = get_agent()
    try:
        result = await agent.run("Reply with exactly 'OK'.", model_settings=model_settings)
        assert "OK" in result.output
    except Exception as e:
        pytest.fail(f"Gemini E2E failed: {e}")


def test_gemini_api_key_overrides_env():
    """Regression: settings gemini_api_key must overwrite a pre-existing GEMINI_API_KEY env var."""
    from co_cli.config import settings

    original_env = os.environ.get("GEMINI_API_KEY")
    original_key = settings.gemini_api_key
    original_provider = settings.llm_provider
    try:
        # Simulate a stale env var and a settings-configured key
        os.environ["GEMINI_API_KEY"] = "stale-key-from-env"
        settings.gemini_api_key = "settings-key-wins"
        settings.llm_provider = "gemini"

        get_agent()

        assert os.environ["GEMINI_API_KEY"] == "settings-key-wins"
    finally:
        # Restore original state
        settings.gemini_api_key = original_key
        settings.llm_provider = original_provider
        if original_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = original_env


@pytest.mark.asyncio
async def test_agent_e2e_ollama():
    """Test a full round-trip to Ollama.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return  # Not targeting Ollama this run

    agent, model_settings, _ = get_agent()
    try:
        result = await agent.run("Reply with exactly 'OK'.", model_settings=model_settings)
        assert "OK" in result.output
    except Exception as e:
        pytest.fail(f"Ollama E2E failed: {e}")


@pytest.mark.asyncio
async def test_ollama_tool_calling():
    """Ollama model selects the correct tool and emits valid args.

    Validates that quantized models can produce structured tool-call JSON.
    This is the gate test for switching between quant levels (e.g. q8_0 → q4_k_m).
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return  # Not targeting Ollama this run

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name="test"),
        auto_confirm=False,
        session_id="test",
    )

    result = await agent.run(
        "Run this shell command: echo hello",
        deps=deps,
        model_settings=model_settings,
    )

    # run_shell_command requires approval → must return DeferredToolRequests
    assert isinstance(result.output, DeferredToolRequests), (
        f"Expected tool call, got text: {result.output!r}"
    )

    calls = list(result.output.approvals)
    assert len(calls) >= 1, "No tool calls in DeferredToolRequests"
    assert calls[0].tool_name == "run_shell_command", (
        f"Wrong tool selected: {calls[0].tool_name}"
    )
