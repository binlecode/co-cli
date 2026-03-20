"""Shared Ollama helpers for standalone eval runners."""

from __future__ import annotations

import httpx

from co_cli.config import DEFAULT_LLM_HOST


async def ensure_ollama_warm(model_name: str, llm_host: str = DEFAULT_LLM_HOST) -> None:
    """Load a model into VRAM before running a latency-sensitive eval step."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{llm_host}/api/ps", timeout=5)
        loaded = {m["name"] for m in resp.json().get("models", [])}
        if model_name not in loaded:
            print(
                f"\n[ollama] loading {model_name} into VRAM (this may take several minutes)...",
                flush=True,
            )
            await client.post(
                f"{llm_host}/api/generate",
                json={"model": model_name, "prompt": "", "stream": False, "keep_alive": -1},
                timeout=300,
            )
            print(f"[ollama] {model_name} ready", flush=True)
        else:
            print(f"\n[ollama] {model_name} already loaded", flush=True)
