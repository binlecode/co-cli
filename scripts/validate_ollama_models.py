#!/usr/bin/env python3
"""Validate co-cli custom Ollama models.

Checks that every co-cli custom model is installed and that its key inference
parameters match the expected values defined in the ollama/ Modelfiles.

Usage:
    uv run python scripts/validate_ollama_models.py
    uv run python scripts/validate_ollama_models.py --host http://localhost:11434
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

# Expected co-cli custom Ollama models and their key inference parameters.
# Source of truth: the shipped ollama/Modelfile.* files in this repo.
CUSTOM_MODELS: list[dict[str, Any]] = [
    {
        "name": "qwen3:30b-q4_k_m-agentic",
        "role": "reasoning",
        "modelfile": "ollama/Modelfile.qwen3-30b-q4_k_m-agentic",
        "params": {
            "num_ctx": 131072,
            "num_predict": 32768,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "repeat_penalty": 1.0,
        },
    },
    {
        "name": "qwen3.5:35b-a3b-q4_k_m-nothink",
        "role": "nothink",
        "modelfile": "ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-nothink",
        "no_think": True,
        "params": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
            "num_predict": 32768,
            "num_ctx": 32768,
        },
    },
    {
        "name": "qwen3.5:35b-a3b-q4_k_m-agentic",
        "role": "reasoning-fallback",
        "modelfile": "ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-agentic",
        "params": {
            "num_ctx": 131072,
            "num_predict": 32768,
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        },
    },
    {
        "name": "qwen3:30b-q4_k_m-nothink",
        "role": "nothink-alt",
        "modelfile": "ollama/Modelfile.qwen3-30b-q4_k_m-nothink",
        "no_think": True,
        "params": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0,
            "num_predict": 32768,
            "num_ctx": 32768,
        },
    },
    {
        "name": "qwen3.5:35b-a3b-q4_k_m-research",
        "role": "research",
        "modelfile": "ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-research",
        "no_think": True,
        "params": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 1.5,
            "num_predict": 2048,
            "num_ctx": 32768,
        },
    },
    {
        "name": "qwen3.5:35b-a3b-q4_k_m-summarize",
        "role": "summarize",
        "modelfile": "ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-summarize",
        "no_think": True,
        "params": {
            "temperature": 0.1,
            "top_p": 0.9,
            "top_k": 20,
            "repeat_penalty": 1.1,
            "presence_penalty": 1.5,
            "num_predict": 2048,
            "num_ctx": 32768,
        },
    },
    {
        "name": "qwen3-coder-next:q4_k_m-code",
        "role": "coding",
        "modelfile": "ollama/Modelfile.qwen3-coder-next-q4_k_m-code",
        "params": {
            "num_ctx": 262144,
            "num_predict": 65536,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.01,
            "repeat_penalty": 1.05,
            "num_keep": 256,
        },
    },
]


def _installed_models(host: str) -> set[str]:
    resp = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=10)
    resp.raise_for_status()
    return {
        m["name"] for m in resp.json().get("models", []) if isinstance(m, dict) and "name" in m
    }


def _show_params(host: str, model: str) -> dict[str, Any]:
    """Fetch model parameters via ollama /api/show and parse into a flat dict."""
    resp = httpx.post(f"{host.rstrip('/')}/api/show", json={"name": model}, timeout=10)
    resp.raise_for_status()
    raw_params: str = resp.json().get("parameters", "") or ""
    result: dict[str, Any] = {}
    for line in raw_params.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts
        # Cast to int or float where appropriate; keep as string otherwise.
        try:
            result[key] = int(val)
        except ValueError:
            try:
                result[key] = float(val)
            except ValueError:
                result[key] = val
    return result


def _show_system(host: str, model: str) -> str:
    """Fetch the baked system prompt for a model via /api/show."""
    resp = httpx.post(f"{host.rstrip('/')}/api/show", json={"name": model}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("system", "") or ""


def _check_params(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Return a list of mismatch descriptions, empty if all match."""
    mismatches: list[str] = []
    for key, exp_val in expected.items():
        act_val = actual.get(key)
        if act_val is None:
            mismatches.append(f"{key}: missing (expected {exp_val})")
        elif isinstance(exp_val, float):
            if abs(float(act_val) - exp_val) > 1e-6:
                mismatches.append(f"{key}: {act_val} != {exp_val}")
        elif act_val != exp_val:
            mismatches.append(f"{key}: {act_val} != {exp_val}")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate co-cli custom Ollama models against expected Modelfile params."
    )
    parser.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama host URL (default: http://localhost:11434).",
    )
    args = parser.parse_args()

    try:
        installed = _installed_models(args.host)
    except Exception as exc:
        print(f"error: cannot reach Ollama at {args.host}: {type(exc).__name__}: {exc}")
        return 2

    print(f"ollama_host={args.host}")
    print(f"installed_models={len(installed)}")
    print()

    col_name = max(len(m["name"]) for m in CUSTOM_MODELS) + 2
    col_role = 14
    header = f"{'model':<{col_name}} {'role':<{col_role}} status"
    print(header)
    print("-" * len(header))

    exit_code = 0

    for spec in CUSTOM_MODELS:
        name: str = spec["name"]
        role: str = spec["role"]

        if name not in installed:
            print(
                f"{name:<{col_name}} {role:<{col_role}} MISSING — run: ollama create {name} -f {spec['modelfile']}"
            )
            exit_code = 1
            continue

        try:
            actual_params = _show_params(args.host, name)
        except Exception as exc:
            print(f"{name:<{col_name}} {role:<{col_role}} ERROR fetching params: {exc}")
            exit_code = 1
            continue

        mismatches = _check_params(actual_params, spec["params"])

        no_think_ok = True
        if spec.get("no_think"):
            try:
                system_prompt = _show_system(args.host, name)
                if "/no_think" not in system_prompt:
                    mismatches.append("system prompt: /no_think directive missing")
                    no_think_ok = False
            except Exception as exc:
                mismatches.append(f"system prompt: ERROR fetching: {exc}")
                no_think_ok = False

        if mismatches:
            label = "PARAM MISMATCH" if no_think_ok else "MISMATCH"
            print(f"{name:<{col_name}} {role:<{col_role}} {label}")
            for m in mismatches:
                print(f"  {'':>{col_name + col_role}}  ! {m}")
            exit_code = 1
        else:
            print(f"{name:<{col_name}} {role:<{col_role}} ok")

    print()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
