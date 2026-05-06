"""Shared Ollama test utilities — not a pytest file."""

import time

import httpx

from co_cli.config.llm import DEFAULT_LLM_HOST

_NOREASON_WARMED: set[str] = set()
_REASONING_WARMED: set[str] = set()


async def _generate_one_token(
    client: httpx.AsyncClient,
    llm_host: str,
    model_name: str,
    *,
    think: bool = False,
) -> None:
    payload: dict = {
        "model": model_name,
        "prompt": "hi",
        "stream": False,
        "keep_alive": -1,
        "options": {"num_predict": 1},
    }
    if think:
        payload["think"] = True
    await client.post(f"{llm_host}/api/generate", json=payload, timeout=300)


async def ensure_ollama_warm(model_name: str, llm_host: str = DEFAULT_LLM_HOST) -> None:
    """Warm noreason + reasoning inference paths once per process; flush KV cache on every call.

    Tracks per-model warmup state in module-level sets _NOREASON_WARMED and _REASONING_WARMED.
    First call for a given model:
      - loads model into VRAM if absent (may take minutes for large models)
      - primes the noreason inference path with a 1-token generate
      - primes the reasoning inference path with a 1-token think=true generate
    Subsequent calls re-issue the noreason 1-token to flush stale KV cache.

    keep_alive=-1 pins the model for the test session. Always call outside any
    asyncio.timeout block — warmup duration is non-deterministic (cold model load).
    """
    async with httpx.AsyncClient() as client:
        if model_name not in _NOREASON_WARMED:
            t0 = time.monotonic()
            resp = await client.get(f"{llm_host}/api/ps", timeout=5)
            loaded = {m["name"] for m in resp.json().get("models", [])}
            if model_name not in loaded:
                print(
                    f"\n[ollama] loading {model_name} into VRAM (this may take several minutes)...",
                    flush=True,
                )
            else:
                print(f"\n[ollama] {model_name} loaded — priming noreason path...", flush=True)
            await _generate_one_token(client, llm_host, model_name, think=False)
            _NOREASON_WARMED.add(model_name)
            print(
                f"[ollama] {model_name} noreason ready ({time.monotonic() - t0:.1f}s)",
                flush=True,
            )
        else:
            await _generate_one_token(client, llm_host, model_name, think=False)

        if model_name not in _REASONING_WARMED:
            t0 = time.monotonic()
            print(f"[ollama] {model_name} priming reasoning path...", flush=True)
            await _generate_one_token(client, llm_host, model_name, think=True)
            _REASONING_WARMED.add(model_name)
            print(
                f"[ollama] {model_name} reasoning ready ({time.monotonic() - t0:.1f}s)",
                flush=True,
            )
