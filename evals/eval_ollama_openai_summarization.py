"""Eval the default ollama-openai non-reasoning summarization path.

Purpose:
- prove that co can use a single Ollama transport (`ollama-openai`)
- validate the supported non-reason summarization role on the think model

This eval targets the supported default path, not the legacy native path.
Runs against the real configured system and skips gracefully when Ollama is not available.
"""

from __future__ import annotations

import asyncio
from evals._timeouts import EVAL_PROBE_TIMEOUT_SECS

from evals._timeouts import EVAL_SUMMARIZATION_TIMEOUT_SECS

import json
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

from pydantic_ai.messages import ModelMessage, ModelRequest, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import ROLE_SUMMARIZATION, ModelConfig, settings as _settings
from co_cli.context._history import _run_summarization_with_policy
from co_cli.deps import CoConfig

from evals._ollama import ensure_ollama_warm


_CONFIG = CoConfig.from_settings(_settings, cwd=Path.cwd())
_OLLAMA_HOST = _CONFIG.llm_host
_OPENAI_PROVIDER = "ollama-openai"
_CANDIDATES = [
    "qwen3.5:35b-a3b-think",
    "qwen3.5:35b-a3b-q4_k_m-summarize",
    "qwen3.5:35b-a3b-q4_k_m-nothink",
]


def _installed_ollama_models() -> set[str]:
    with urlopen(f"{_OLLAMA_HOST.rstrip('/')}/api/tags", timeout=EVAL_PROBE_TIMEOUT_SECS) as resp:
        data = json.load(resp)
    return {
        m["name"]
        for m in data.get("models", [])
        if isinstance(m, dict) and isinstance(m.get("name"), str)
    }


def _pick_replacement_model() -> str | None:
    try:
        installed = _installed_ollama_models()
    except Exception:
        return None
    for candidate in _CANDIDATES:
        if candidate in installed:
            return candidate
    return None


_REPLACEMENT_MODEL = _pick_replacement_model()


def _replacement_config() -> CoConfig | None:
    if _REPLACEMENT_MODEL is None:
        return None
    role_models = dict(_CONFIG.role_models)
    api_params = {
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 256,
    }
    if _REPLACEMENT_MODEL == "qwen3.5:35b-a3b-think":
        api_params["reasoning_effort"] = "none"
    role_models[ROLE_SUMMARIZATION] = ModelConfig(
        model=_REPLACEMENT_MODEL,
        api_params=api_params,
    )
    return replace(
        _CONFIG,
        llm_provider=_OPENAI_PROVIDER,
        role_models=role_models,
    )


def _replacement_resolved() -> ResolvedModel | None:
    config = _replacement_config()
    if config is None:
        return None
    registry = ModelRegistry.from_config(config)
    return registry.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))


def _sample_messages() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content="Docker is a container runtime and packaging tool.")]),
        ModelRequest(parts=[UserPromptPart(content="Summarize in one short sentence.")]),
    ]


def _require_ollama_provider() -> bool:
    if _CONFIG.llm_provider != "ollama-openai":
        print("SKIP: Ollama not configured")
        return False
    return True


def _check_replacement_model_available() -> None:
    assert _REPLACEMENT_MODEL is not None, (
        "No installed summarization-path model found. "
        f"Checked: {_CANDIDATES}"
    )


def _check_model_registry_builds_openai_compatible_summarization_model() -> None:
    resolved = _replacement_resolved()
    assert resolved is not None and resolved.model is not None
    assert type(resolved.model).__name__ == "OpenAIChatModel"
    assert getattr(resolved.model, "system", None) == "openai"


async def _check_openai_compatible_replacement_model_returns_content() -> None:
    assert _REPLACEMENT_MODEL is not None, f"No replacement model from {_CANDIDATES} is installed"
    await ensure_ollama_warm(_REPLACEMENT_MODEL, _OLLAMA_HOST)

    resolved = _replacement_resolved()
    assert resolved is not None and resolved.model is not None

    async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS):
        response = await resolved.model.request(
            [ModelRequest(parts=[UserPromptPart(content="Reply with exactly: hello")])],
            resolved.settings,
            ModelRequestParameters(),
        )

    text_parts = [p for p in response.parts if isinstance(p, TextPart)]
    assert text_parts, "Expected at least one TextPart from ollama-openai replacement path"
    assert text_parts[0].content.strip(), "Replacement model returned empty content"


async def _check_openai_compatible_summarization_pipeline_returns_summary() -> None:
    assert _REPLACEMENT_MODEL is not None, f"No replacement model from {_CANDIDATES} is installed"
    await ensure_ollama_warm(_REPLACEMENT_MODEL, _OLLAMA_HOST)

    resolved = _replacement_resolved()
    assert resolved is not None

    async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS):
        summary = await _run_summarization_with_policy(
            _sample_messages(),
            resolved,
            max_retries=1,
        )

    assert summary is not None, "Summarization pipeline returned None"
    assert summary.strip(), "Summarization pipeline returned empty content"


async def _main() -> int:
    print("=" * 60)
    print("  Eval: Ollama OpenAI Summarization Path")
    print("=" * 60)

    if not _require_ollama_provider():
        return 0
    checks: list[tuple[str, object]] = [
        ("replacement model available", _check_replacement_model_available),
        (
            "registry builds openai-compatible summarization model",
            _check_model_registry_builds_openai_compatible_summarization_model,
        ),
        (
            "replacement model returns non-empty content",
            _check_openai_compatible_replacement_model_returns_content,
        ),
        (
            "summarization pipeline returns non-empty summary",
            _check_openai_compatible_summarization_pipeline_returns_summary,
        ),
    ]

    try:
        for idx, (label, check) in enumerate(checks, start=1):
            print(f"\n[{idx}/{len(checks)}] {label}...", flush=True)
            result = check()
            if asyncio.iscoroutine(result):
                await result
            print("PASS", flush=True)
    except AssertionError as exc:
        print(f"FAIL: {exc}", flush=True)
        return 1
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", flush=True)
        return 1

    print("\nVERDICT: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
