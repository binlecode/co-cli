"""LLM-based listwise reranker dispatch for the knowledge index."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-2.0-flash",
    "ollama": "qwen2.5:3b",
}


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


@dataclass
class _RerankerCallable:
    """Callable returned by build_llm_reranker. Dispatches to the configured provider."""

    provider: str
    ollama_host: str
    model: str
    api_key: str | None

    def __call__(self, prompt: str, n: int) -> list[int]:
        if self.provider == "ollama":
            return self._call_ollama(prompt, n)
        if self.provider == "gemini":
            return self._call_gemini(prompt, n)
        return list(range(1, n + 1))

    def _call_ollama(self, prompt: str, n: int) -> list[int]:
        import httpx

        resp = httpx.post(
            f"{self.ollama_host}/api/generate",
            json={"model": self.model, "prompt": prompt, "format": "json", "stream": False},
            timeout=60.0,
        )
        resp.raise_for_status()
        return _parse_ranked_indices(json.loads(resp.json()["response"]), n)

    def _call_gemini(self, prompt: str, n: int) -> list[int]:
        from google import (
            genai,  # type: ignore[attr-defined]  # google-generativeai lacks pyright stubs
        )
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return _parse_ranked_indices(json.loads(response.text), n)


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
    effective_model = model or _DEFAULT_MODELS.get(provider, "qwen2.5:3b")
    return _RerankerCallable(
        provider=provider,
        ollama_host=ollama_host,
        model=effective_model,
        api_key=api_key,
    )
