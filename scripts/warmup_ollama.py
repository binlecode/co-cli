"""Pre-load Ollama models into VRAM before running the test suite.

Run this before pytest to prevent cold-start timeouts in LLM-calling tests:

    uv run python scripts/warmup_ollama.py && uv run pytest ...

Sends concurrent minimal generation requests with keep_alive=-1 so the active
test-suite models stay loaded for the duration of the run.
"""

import asyncio
import sys
import httpx

OLLAMA_HOST = "http://localhost:11434"

# Active models used by the test suite.
# Non-reason roles reuse the resident think model via reasoning_effort="none",
# so there is no separate instruct model to warm.
MODELS = [
    "qwen3.5:35b-a3b-think",
    "qwen3.5:35b-a3b-code",
]


async def warm(client: httpx.AsyncClient, model: str) -> None:
    print(f"  warming {model} ...", end=" ", flush=True)
    resp = await client.post(
        f"{OLLAMA_HOST}/api/generate",
        json={"model": model, "prompt": "hi", "stream": False, "keep_alive": -1},
        timeout=180,
    )
    resp.raise_for_status()
    print("ok")


async def main() -> None:
    print("Warming Ollama models for test suite (concurrent load)...")
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[warm(client, m) for m in MODELS])
    print("Done — models loaded into VRAM.")

    # Verify all are loaded
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{OLLAMA_HOST}/api/ps", timeout=10)
        loaded = [m["name"] for m in resp.json().get("models", [])]
    print(f"Loaded models: {loaded}")
    missing = [m for m in MODELS if m not in loaded]
    if missing:
        total_gb = len(MODELS) * 28
        print(
            f"\nERROR: model eviction detected — {len(missing)} model(s) not held in VRAM: {missing}\n"
            f"  All {len(MODELS)} models require ~{total_gb} GB VRAM simultaneously.\n"
            f"  Ollama's OLLAMA_MAX_LOADED_MODELS is likely set to 1 (default).\n"
            f"  Fix:\n"
            f"    launchctl setenv OLLAMA_MAX_LOADED_MODELS {len(MODELS)}\n"
            f"    brew services restart ollama\n"
            f"  Then re-run this script.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
