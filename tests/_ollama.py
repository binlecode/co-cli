"""Shared Ollama test utilities — not a pytest file."""
import httpx

from co_cli.config import DEFAULT_LLM_HOST


async def ensure_ollama_warm(model_name: str, llm_host: str = DEFAULT_LLM_HOST) -> None:
    """Load model into GPU VRAM before the test timeout window starts.

    Checks /api/ps first; only loads if the model is not already resident.
    Uses keep_alive=-1 to pin the model for the duration of the test session.
    Loading a 28GB+ model takes several minutes — call this before asyncio.timeout.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{llm_host}/api/ps", timeout=5)
        loaded = {m["name"] for m in resp.json().get("models", [])}
        if model_name not in loaded:
            print(f"\n[ollama] loading {model_name} into VRAM (this may take several minutes)...", flush=True)
            await client.post(
                f"{llm_host}/api/generate",
                json={"model": model_name, "prompt": "", "stream": False, "keep_alive": -1},
                timeout=300,
            )
            print(f"[ollama] {model_name} ready", flush=True)
        else:
            print(f"\n[ollama] {model_name} already loaded", flush=True)
