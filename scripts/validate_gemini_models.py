#!/usr/bin/env python3
"""Validate Gemini provider configuration and model availability.

Probes the Gemini API with the configured key and checks that the target
model is visible. Optionally lists all available models with specs.

Usage:
    uv run python scripts/validate_gemini_models.py
    uv run python scripts/validate_gemini_models.py --list-models
    uv run python scripts/validate_gemini_models.py --model gemini-2.0-flash
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from co_cli.config import get_settings


def _extract_model_name(raw_name: str) -> str:
    """Convert 'models/gemini-2.0-flash' to 'gemini-2.0-flash'."""
    if raw_name.startswith("models/"):
        return raw_name.split("/", 1)[1]
    return raw_name


def _list_gemini_models(api_key: str) -> list[dict[str, Any]]:
    from google import genai

    # Prefer GEMINI_API_KEY; clear GOOGLE_API_KEY to avoid SDK ambiguity.
    os.environ["GEMINI_API_KEY"] = api_key
    os.environ.pop("GOOGLE_API_KEY", None)

    client = genai.Client(api_key=api_key)
    specs: list[dict[str, Any]] = []
    for model in client.models.list(config={"page_size": 1000}):
        raw_name = getattr(model, "name", "") or ""
        if not raw_name:
            continue
        name = _extract_model_name(raw_name)
        supported_actions = getattr(model, "supported_actions", None) or []
        specs.append(
            {
                "name": name,
                "display_name": getattr(model, "display_name", None),
                "version": getattr(model, "version", None),
                "ctx_size": getattr(model, "input_token_limit", None),
                "output_token_limit": getattr(model, "output_token_limit", None),
                "thinking": getattr(model, "thinking", None),
                "supported_actions": supported_actions,
                "temperature": getattr(model, "temperature", None),
                "max_temperature": getattr(model, "max_temperature", None),
                "top_p": getattr(model, "top_p", None),
                "top_k": getattr(model, "top_k", None),
            }
        )
    specs.sort(key=lambda m: m["name"])
    return specs


def _print_specs(specs: list[dict[str, Any]]) -> None:
    print("gemini_models:")
    for item in specs:
        actions = ",".join(item["supported_actions"]) if item["supported_actions"] else ""
        print(
            "- "
            f"name={item['name']}; "
            f"display_name={item.get('display_name') or ''}; "
            f"version={item.get('version') or ''}; "
            f"ctx_size={item.get('ctx_size')}; "
            f"output_token_limit={item.get('output_token_limit')}; "
            f"thinking={item.get('thinking')}; "
            f"supported_actions={actions}; "
            f"temperature={item.get('temperature')}; "
            f"max_temperature={item.get('max_temperature')}; "
            f"top_p={item.get('top_p')}; "
            f"top_k={item.get('top_k')}"
        )


def main() -> int:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Validate Gemini API key and model availability."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GEMINI_API_KEY") or settings.gemini_api_key or "",
        help="Gemini API key (default: GEMINI_API_KEY env var or settings.gemini_api_key).",
    )
    parser.add_argument(
        "--model",
        default=settings.gemini_model,
        help="Gemini model name to validate (default: settings.gemini_model).",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List all Gemini models visible to the API key with specs.",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("error: Gemini API key required. Pass --api-key or set GEMINI_API_KEY.")
        return 2

    print(f"gemini_model={args.model}")
    print()

    try:
        specs = _list_gemini_models(args.api_key)
    except Exception as exc:
        print(f"error: failed to reach Gemini API: {type(exc).__name__}: {exc}")
        return 2

    if args.list_models:
        _print_specs(specs)
        print()

    available = {m["name"] for m in specs}
    if args.model in available:
        print(f"ok: model available: {args.model}")
        return 0
    else:
        print(f"error: model not available to this key: {args.model}")
        print(f"hint: run with --list-models to see available models")
        return 1


if __name__ == "__main__":
    sys.exit(main())
