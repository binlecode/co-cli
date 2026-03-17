"""LLM-based listwise reranker dispatch for the knowledge index."""

from __future__ import annotations

import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def build_llm_reranker(
    provider: str,
    ollama_host: str,
    model: str,
    api_key: str | None,
) -> Callable[[str, int], list[int]]:
    """Return a callable for LLM listwise rerank.

    Returns identity order [1..n] for unknown providers.
    provider: 'ollama' or 'gemini'
    model: reranker model name; if empty, filled by provider default here.
    """
    effective_model = model
    if not effective_model:
        if provider == "gemini":
            effective_model = "gemini-2.0-flash"
        elif provider == "local":
            effective_model = "BAAI/bge-reranker-base"
        else:
            effective_model = "qwen2.5:3b"

    def _parse_ranked_indices(parsed: object, n: int) -> list[int]:
        """Extract 1-based ranked indices from JSON output."""
        if isinstance(parsed, list):
            ints = [int(x) for x in parsed if isinstance(x, (int, float))]
            if ints:
                return ints
        if isinstance(parsed, dict):
            for key in ("ranking", "relevancy_order", "order", "result", "indices", "ranked"):
                val = parsed.get(key)
                if isinstance(val, list):
                    ints = [int(x) for x in val if isinstance(x, (int, float))]
                    if ints:
                        return ints
            for val in parsed.values():
                if isinstance(val, list) and all(isinstance(x, (int, float)) for x in val):
                    return [int(x) for x in val]
        return list(range(1, n + 1))

    def _call(prompt: str, n: int) -> list[int]:
        if provider == "ollama":
            import httpx
            resp = httpx.post(
                f"{ollama_host}/api/generate",
                json={"model": effective_model, "prompt": prompt, "format": "json", "stream": False},
                timeout=60.0,
            )
            resp.raise_for_status()
            raw = resp.json()["response"]
            return _parse_ranked_indices(json.loads(raw), n)

        if provider == "gemini":
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=effective_model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return _parse_ranked_indices(json.loads(response.text), n)

        return list(range(1, n + 1))

    return _call
