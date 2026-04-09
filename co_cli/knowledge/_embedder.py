"""Embedding provider dispatch for the knowledge index."""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def build_embedder(
    provider: str,
    ollama_host: str,
    model: str,
    embed_api_url: str,
    api_key: str | None,
) -> Callable[[str], list[float] | None]:
    """Return a callable that embeds a text string.

    Returns None on provider failure — callers should handle None gracefully.
    provider: 'ollama', 'gemini', 'tei', or 'none'
    """

    def _embed(text: str) -> list[float] | None:
        try:
            if provider == "ollama":
                import httpx

                resp = httpx.post(
                    f"{ollama_host}/api/embed",
                    json={"model": model, "input": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
                return resp.json()["embeddings"][0]

            if provider == "gemini":
                from google import genai

                client = genai.Client(api_key=api_key)
                result = client.models.embed_content(
                    model=model,
                    contents=text,
                )
                return result.embeddings[0].values

            if provider == "tei":
                import httpx

                resp = httpx.post(
                    f"{embed_api_url}/embed",
                    json={"inputs": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
                return resp.json()[0]

            # provider == "none" or unknown
            return None

        except Exception as e:
            logger.warning(f"Embedding generation failed ({provider}): {e}")
            return None

    return _embed
