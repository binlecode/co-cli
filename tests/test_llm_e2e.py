import os
import pytest
from co_cli.agent import get_agent

# Check for LLM Credentials
HAS_GEMINI = bool(os.getenv("GEMINI_API_KEY"))
HAS_OLLAMA = True # Assumed, but will fail connection if not running

@pytest.mark.asyncio
async def test_agent_e2e_gemini():
    """
    Test a full round-trip to Gemini.
    """
    if not HAS_GEMINI and os.getenv("LLM_PROVIDER") == "gemini":
        pytest.skip("GEMINI_API_KEY missing")
    
    if os.getenv("LLM_PROVIDER") != "gemini":
        pytest.skip("Skipping Gemini E2E (Provider not set to gemini)")

    agent = get_agent()
    # Simple query to verify connectivity
    try:
        result = await agent.run("Reply with exactly 'OK'.")
        assert "OK" in result.output
    except Exception as e:
        pytest.fail(f"Gemini E2E failed: {e}")

@pytest.mark.asyncio
async def test_agent_e2e_ollama():
    """
    Test a full round-trip to Ollama.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        pytest.skip("Skipping Ollama E2E (Provider not set to ollama)")
        
    agent = get_agent()
    try:
        result = await agent.run("Reply with exactly 'OK'.")
        assert "OK" in result.output
    except Exception as e:
        # If connection refused, skip instead of fail? 
        # No, strict policy says real tests. If it fails, functionality is broken.
        pytest.fail(f"Ollama E2E failed: {e}")
