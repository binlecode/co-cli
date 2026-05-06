"""Shared model warmup helper for standalone eval runners."""

from __future__ import annotations

import httpx
from evals._timeouts import EVAL_BENCHMARK_TIMEOUT_SECS, EVAL_PROBE_TIMEOUT_SECS

from co_cli.config.core import settings
from co_cli.config.llm import DEFAULT_LLM_HOST


async def ensure_model_warm() -> None:
    """Load the configured model before running evals. No-op for cloud providers."""
    if settings.llm.provider != "ollama":
        return
    model_name = settings.llm.model
    llm_host = settings.llm.host or DEFAULT_LLM_HOST
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{llm_host}/api/ps", timeout=EVAL_PROBE_TIMEOUT_SECS)
        loaded = {m["name"] for m in resp.json().get("models", [])}
        if model_name not in loaded:
            print(
                f"\n[model] loading {model_name} into VRAM (this may take several minutes)...",
                flush=True,
            )
            await client.post(
                f"{llm_host}/api/generate",
                json={"model": model_name, "prompt": "", "stream": False, "keep_alive": -1},
                timeout=EVAL_BENCHMARK_TIMEOUT_SECS,
            )
            print(f"[model] {model_name} ready", flush=True)
        else:
            print(f"\n[model] {model_name} already loaded", flush=True)
