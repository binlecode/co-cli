import pytest
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel


def test_agent_initialization_gemini(monkeypatch):
    """
    Test that the agent is correctly initialized with Gemini.
    No mocks allowed.
    """
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-init")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")

    # Reload settings to pick up new env vars
    from co_cli import config, agent as agent_module
    test_settings = config.Settings()
    monkeypatch.setattr(config, "settings", test_settings)
    monkeypatch.setattr(agent_module, "settings", test_settings)

    from co_cli.agent import get_agent
    agent = get_agent()

    # Check internal model structure - now uses GoogleModel
    assert isinstance(agent.model, GoogleModel)
    assert agent.model.model_name == "gemini-2.0-flash"


def test_agent_initialization_ollama(monkeypatch):
    """
    Test that the agent is correctly initialized with Ollama.
    No mocks allowed.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    # Reload settings to pick up new env vars
    from co_cli import config, agent as agent_module
    test_settings = config.Settings()
    monkeypatch.setattr(config, "settings", test_settings)
    monkeypatch.setattr(agent_module, "settings", test_settings)

    from co_cli.agent import get_agent
    agent = get_agent()

    # Check internal model structure
    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "llama3"
