"""Shared Ollama test utilities — not a pytest file."""

import time

import httpx

from co_cli.config._llm import DEFAULT_LLM_HOST


async def ensure_ollama_warm(model_name: str, llm_host: str = DEFAULT_LLM_HOST) -> None:
    """Load model into GPU VRAM and flush stale KV cache before the test timeout window starts.

    Always issues a minimal /api/generate request regardless of load state:
    - If model is absent: loads it into VRAM (may take several minutes for large models).
    - If model is present: flushes the stale KV cache from previous inference so the
      next real call starts from a clean slot and is not delayed by cache eviction.

    Uses keep_alive=-1 to pin the model for the duration of the test session.
    Call this before asyncio.timeout for any LLM-calling test. The warmup itself is
    intentionally outside the timeout window — it is infrastructure prep, not the
    behavior under test.
    """
    t0 = time.monotonic()
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{llm_host}/api/ps", timeout=5)
        loaded = {m["name"] for m in resp.json().get("models", [])}
        if model_name not in loaded:
            print(
                f"\n[ollama] loading {model_name} into VRAM (this may take several minutes)...",
                flush=True,
            )
        else:
            print(f"\n[ollama] {model_name} loaded — priming inference path...", flush=True)
        # Generate exactly 1 token to fully prime GPU compute paths and flush stale KV cache.
        # An empty-prompt call does not generate tokens and leaves compute paths cold;
        # a 1-token generation runs the full forward pass, ensuring subsequent timed calls
        # are not delayed by first-call GPU state initialization.
        await client.post(
            f"{llm_host}/api/generate",
            json={
                "model": model_name,
                "prompt": "hi",
                "stream": False,
                "keep_alive": -1,
                "options": {"num_predict": 1},
            },
            timeout=300,
        )
    elapsed = time.monotonic() - t0
    print(f"[ollama] {model_name} ready ({elapsed:.1f}s)", flush=True)
