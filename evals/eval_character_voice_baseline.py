#!/usr/bin/env python3
"""Capture pre-change voice baselines for personality roles (tars, finch, jeff).

Run this script while the static ## Character block is still present in the
system prompt to record per-role response metrics. Baselines are written to
evals/_baselines/character-voice-{role}.json and checked in for regression use.

Each role: 3 tasks (technical, debugging, exploration).
Per task: response text, sentence-length distribution, Never-list violation count,
top-20 content tokens.

Usage:
    uv run python evals/eval_character_voice_baseline.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelResponse, TextPart

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.memory._stopwords import STOPWORDS

_BASELINES_DIR = Path(__file__).parent / "_baselines"

_ROLES = ("tars", "finch", "jeff")

_TASKS: list[dict[str, str]] = [
    {
        "kind": "technical",
        "prompt": "What's your take on error handling patterns in Python?",
    },
    {
        "kind": "debugging",
        "prompt": "I'm getting a KeyError in my dict lookup — walk me through diagnosis.",
    },
    {
        "kind": "exploration",
        "prompt": "Tell me about an interesting pattern you've observed in how engineers approach testing.",
    },
]

_SOULS_DIR = Path(__file__).parent.parent / "co_cli" / "personality" / "prompts" / "souls"

# Sentence boundary pattern: split after ". ", "! ", "? " (or end of string)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _load_never_bullets(role: str) -> list[str]:
    """Extract Never-list bullet text from the role's seed.md."""
    seed_path = _SOULS_DIR / role / "seed.md"
    text = seed_path.read_text(encoding="utf-8")
    bullets: list[str] = []
    in_never = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("never:"):
            in_never = True
            continue
        if in_never:
            if stripped.startswith("-"):
                # Strip leading "- " and capture the rule text
                bullets.append(stripped[1:].strip())
            elif stripped == "":
                continue
            else:
                # Non-bullet, non-blank line ends the Never block
                in_never = False
    return bullets


def _extract_response_text(messages: list[Any]) -> str:
    """Concatenate all TextPart content from ModelResponse messages."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
    return "".join(parts)


def _sentence_lengths(text: str) -> list[int]:
    """Return word counts per sentence, splitting on sentence boundaries."""
    if not text.strip():
        return []
    sentences = _SENTENCE_SPLIT.split(text.strip())
    lengths: list[int] = []
    for s in sentences:
        s = s.strip()
        if s:
            lengths.append(len(s.split()))
    return lengths


def _percentile(values: list[float], pct: float) -> float:
    """Compute the p-th percentile (0-100) via linear interpolation."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    rank = pct / 100.0 * (n - 1)
    lo = int(rank)
    hi = lo + 1
    if hi >= n:
        return float(sorted_vals[-1])
    frac = rank - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _compute_sentence_stats(text: str) -> dict[str, float]:
    """Compute sentence length distribution over the response text."""
    lengths = _sentence_lengths(text)
    if not lengths:
        return {
            "sentence_length_mean": 0.0,
            "sentence_length_p25": 0.0,
            "sentence_length_p75": 0.0,
            "sentence_length_p95": 0.0,
        }
    float_lengths = [float(x) for x in lengths]
    mean = sum(float_lengths) / len(float_lengths)
    return {
        "sentence_length_mean": round(mean, 2),
        "sentence_length_p25": round(_percentile(float_lengths, 25), 2),
        "sentence_length_p75": round(_percentile(float_lengths, 75), 2),
        "sentence_length_p95": round(_percentile(float_lengths, 95), 2),
    }


def _count_never_violations(text: str, never_bullets: list[str]) -> int:
    """Count Never-list bullets whose key phrase appears in the response.

    Uses simple lowercased substring match against each bullet's first 6 words
    to avoid false positives from long rule text.
    """
    text_lower = text.lower()
    count = 0
    for bullet in never_bullets:
        # Use first 6 words of the bullet as the match key
        key_words = bullet.lower().split()[:6]
        key_phrase = " ".join(key_words)
        if key_phrase and key_phrase in text_lower:
            count += 1
    return count


def _top_tokens(text: str, n: int = 20) -> list[str]:
    """Return top-n content tokens by frequency, filtering stopwords."""
    tokens = re.findall(r"[a-z]{2,}", text.lower())
    filtered = [t for t in tokens if t not in STOPWORDS]
    counter = Counter(filtered)
    return [token for token, _ in counter.most_common(n)]


def _build_agent_and_deps(role: str) -> tuple[Any, Any]:
    """Build agent and deps for the given role with MCP servers disabled."""
    config = settings.model_copy(update={"personality": role, "mcp_servers": {}})
    reg = build_tool_registry(config)
    agent = build_agent(config=config, tool_registry=reg)
    deps = make_eval_deps()
    deps.tool_index = reg.tool_index
    deps.tool_registry = reg
    return agent, deps


async def _run_task(role: str, task: dict[str, str], never_bullets: list[str]) -> dict[str, Any]:
    """Run one task through the live agent and compute voice metrics."""
    t0 = time.monotonic()
    print(f"  [{role}] {task['kind']}: {task['prompt'][:60]}...")

    agent, deps = _build_agent_and_deps(role)

    try:
        async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=agent,
                user_input=task["prompt"],
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(),
            )
    except TimeoutError:
        print(f"  [{role}] {task['kind']} TIMEOUT after {EVAL_TURN_TIMEOUT_SECS}s")
        raise
    except Exception as e:
        print(f"  [{role}] {task['kind']} ERROR: {e}")
        raise

    elapsed_ms = (time.monotonic() - t0) * 1000
    response_text = _extract_response_text(result.messages)
    stats = _compute_sentence_stats(response_text)
    never_count = _count_never_violations(response_text, never_bullets)
    tokens = _top_tokens(response_text)

    print(f"  [{role}] {task['kind']} done ({elapsed_ms:.0f}ms, {len(response_text)} chars)")

    return {
        "task": task["prompt"],
        "response": response_text,
        **stats,
        "never_violations": never_count,
        "top_tokens": tokens,
    }


async def _capture_role(role: str) -> dict[str, Any]:
    """Capture baseline for all 3 tasks for a single role."""
    print(f"\n[{role}] capturing baseline ({len(_TASKS)} tasks)...")
    never_bullets = _load_never_bullets(role)
    print(f"  [{role}] loaded {len(never_bullets)} Never bullets")

    task_results: list[dict[str, Any]] = []
    for task in _TASKS:
        task_result = await _run_task(role, task, never_bullets)
        task_results.append(task_result)

    return {
        "role": role,
        "captured_at": datetime.now(UTC).isoformat(),
        "tasks": task_results,
    }


def _write_baseline(role: str, data: dict[str, Any]) -> Path:
    """Write baseline JSON to evals/_baselines/character-voice-{role}.json."""
    _BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _BASELINES_DIR / f"character-voice-{role}.json"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


async def main() -> None:
    print(f"[eval_character_voice_baseline] starting at {datetime.now(UTC).strftime('%H:%M:%S')}")
    print(f"[eval_character_voice_baseline] roles: {', '.join(_ROLES)}")
    print(f"[eval_character_voice_baseline] tasks per role: {len(_TASKS)}")

    errors: list[str] = []
    for role in _ROLES:
        try:
            baseline = await _capture_role(role)
            out_path = _write_baseline(role, baseline)
            task_means = [t["sentence_length_mean"] for t in baseline["tasks"]]
            print(f"  [{role}] written to {out_path}")
            print(f"  [{role}] sentence_length_mean per task: {task_means}")
        except Exception as e:
            errors.append(f"{role}: {e}")
            print(f"  [{role}] FAILED: {e}")

    if errors:
        print(f"\n[eval_character_voice_baseline] FAILED — {len(errors)} error(s):")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)

    print(
        f"\n[eval_character_voice_baseline] DONE — {len(_ROLES)} baselines written to {_BASELINES_DIR}"
    )
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
