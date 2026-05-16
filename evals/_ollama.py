"""Eval-side Ollama warm-up — model load + KV-cache flush.

Wraps ``tests._ollama.ensure_ollama_warm`` with auto-resolution of the model
name from production settings. Must be called outside any ``asyncio.timeout``
block — cold model load is non-deterministic infrastructure prep, not behavior
under test (``feedback_ensure_ollama_warm.md``,
``feedback_call_timeout_no_cold_start.md``).
"""

from __future__ import annotations

from tests._ollama import ensure_ollama_warm as _ensure_ollama_warm_by_name

from co_cli.config.core import load_config


async def ensure_ollama_warm() -> None:
    """Warm the configured model from production ``~/.co-cli/`` settings.

    Resolves model name + host once via ``load_config()``; delegates to the
    shared ``tests._ollama.ensure_ollama_warm`` to load the model into VRAM,
    prime noreason + reasoning paths, and flush KV cache on subsequent calls.
    """
    config = load_config()
    model_name = config.llm.model
    host = config.llm.host
    await _ensure_ollama_warm_by_name(model_name, host)
