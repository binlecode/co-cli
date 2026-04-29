#!/usr/bin/env python3
"""Eval: character memory recall — voice delta, canon recall, no-bleed, M sweep.

Validates the canon channel wiring after the static-block removal:
  1. Voice delta gate: per-role sentence-length and vocabulary metrics vs. pre-change baseline.
  2. Canon recall: canonical-vocab queries (hard pass ≥3/3) + paraphrase queries (calibration).
  3. No-bleed: technical queries must not produce spurious canon pollution.
  4. M sweep: calibration scorecard over CO_CHARACTER_RECALL_LIMIT ∈ {1,2,3,5}.

Prerequisites: LLM provider configured, evals/_baselines/character-voice-{role}.json exist.

Usage:
    uv run python evals/eval_character_memory_recall.py
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
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend
from co_cli.memory._stopwords import STOPWORDS
from co_cli.tools.memory._canon_recall import search_canon

_BASELINES_DIR = Path(__file__).parent / "_baselines"
_OUTPUTS_DIR = Path(__file__).parent / "_outputs"
_SOULS_DIR = Path(__file__).parent.parent / "co_cli" / "personality" / "prompts" / "souls"

_ROLES = ("tars", "finch", "jeff")

# Voice delta tasks — must match eval_character_voice_baseline.py tasks.
_VOICE_TASKS: list[str] = [
    "What's your take on error handling patterns in Python?",
    "I'm getting a KeyError in my dict lookup — walk me through diagnosis.",
    "Tell me about an interesting pattern you've observed in how engineers approach testing.",
]

# Canon recall queries per role: 3 canonical-vocab + 3 paraphrase.
_CANON_QUERIES: dict[str, dict[str, list[str]]] = {
    "tars": {
        "canonical": [
            "explain TARS's stance on humor",
            "what's TARS's deference and loyalty rule",
            "how does TARS show warmth through callbacks",
        ],
        "paraphrase": [
            "is TARS funny?",
            "does TARS joke around?",
            "how does TARS handle difficult moments",
        ],
    },
    "finch": {
        "canonical": [
            "how does finch prepare before relationships exist",
            "how does finch deliver hard truths",
            "how does finch teach and show by doing",
        ],
        "paraphrase": [
            "is finch a planner?",
            "does finch soften bad news?",
            "how does finch mentor people?",
        ],
    },
    "jeff": {
        "canonical": [
            "how does jeff use we language even alone",
            "how does jeff share uncertainty plainly",
            "how does jeff stay hopeful about people",
        ],
        "paraphrase": [
            "does jeff work well with others?",
            "is jeff honest about what he doesn't know?",
            "is jeff optimistic despite evidence against it?",
        ],
    },
}

# No-bleed queries — purely technical, no character vocabulary.
_NO_BLEED_QUERIES: list[str] = [
    "show me how to write a pytest fixture",
    "how do I parse JSON in Python",
    "how do I read a file line by line in Python",
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _sentence_lengths(text: str) -> list[int]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return [len(s.split()) for s in sentences]


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = p / 100 * (len(sorted_data) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (idx - lo) * (sorted_data[hi] - sorted_data[lo])


def _top_tokens(text: str, n: int = 20) -> list[str]:
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]
    return [tok for tok, _ in Counter(tokens).most_common(n)]


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _build_agent_for_role(role: str):
    config = settings.model_copy(update={"personality": role, "mcp_servers": []})
    reg = build_tool_registry(config)
    agent = build_agent(config=config, tool_registry=reg)
    deps = make_eval_deps()
    deps.tool_index = reg.tool_index
    deps.tool_registry = reg
    return agent, deps


def _extract_response_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    return part.content
    return ""


def _extract_tool_calls(messages: list[Any]) -> list[str]:
    calls: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(part.tool_name)
    return calls


def _extract_tool_return(messages: list[Any], tool_name: str) -> list[str]:
    """Return text content of all ToolReturnPart for the given tool_name."""
    results: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                    results.append(str(part.content))
    return results


async def _run_turn_safe(
    role: str,
    prompt: str,
) -> tuple[list[Any], float]:
    """Run one turn; return (messages, duration_ms)."""
    t0 = time.monotonic()
    agent, deps = _build_agent_for_role(role)
    try:
        async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=agent,
                user_input=prompt,
                deps=deps,
                message_history=[],
                frontend=HeadlessFrontend(),
            )
        return result.messages, (time.monotonic() - t0) * 1000
    except Exception as e:
        print(f"  [ERROR] role={role} prompt='{prompt[:50]}': {e}", file=sys.stderr)
        return [], (time.monotonic() - t0) * 1000


# ---------------------------------------------------------------------------
# Phase 1: Voice delta gate
# ---------------------------------------------------------------------------


async def run_voice_delta(role: str, baseline: dict) -> dict:
    """Compare post-change voice metrics against pre-change baseline."""
    print(f"  [voice] role={role}")
    task_results = []
    all_pass = True
    for task_record in baseline["tasks"]:
        prompt = task_record["task"]
        messages, duration_ms = await _run_turn_safe(role, prompt)
        response = _extract_response_text(messages)

        lengths = _sentence_lengths(response)
        f_lengths = [float(x) for x in lengths]
        mean = sum(f_lengths) / len(f_lengths) if f_lengths else 0.0
        p25 = _percentile(f_lengths, 25)
        p75 = _percentile(f_lengths, 75)
        p95 = _percentile(f_lengths, 95)
        top_tok = _top_tokens(response, 20)

        base_mean = task_record["sentence_length_mean"]
        mean_ok = abs(mean - base_mean) / max(base_mean, 1) <= 0.15 if base_mean > 0 else True
        jaccard = _jaccard(top_tok, task_record.get("top_tokens", []))
        jaccard_ok = jaccard >= 0.4

        verdict = "PASS" if mean_ok and jaccard_ok else "FAIL"
        if verdict == "FAIL":
            all_pass = False
        print(
            f"    task='{prompt[:40]}' mean={mean:.1f}±15%={mean_ok} jaccard={jaccard:.2f}≥0.4={jaccard_ok} → {verdict}"
        )
        task_results.append(
            {
                "task": prompt,
                "mean": mean,
                "p25": p25,
                "p75": p75,
                "p95": p95,
                "base_mean": base_mean,
                "mean_ok": mean_ok,
                "jaccard": jaccard,
                "jaccard_ok": jaccard_ok,
                "verdict": verdict,
                "duration_ms": duration_ms,
            }
        )

    return {"role": role, "verdict": "PASS" if all_pass else "FAIL", "tasks": task_results}


# ---------------------------------------------------------------------------
# Phase 2: Canon recall
# ---------------------------------------------------------------------------


async def run_canon_recall(role: str) -> dict:
    """Check that canon-invoking queries surface canon hits via memory_search."""
    print(f"  [canon-recall] role={role}")
    queries = _CANON_QUERIES[role]
    canonical_hits = 0
    paraphrase_hits = 0
    details: list[dict] = []

    for kind, qlist in [
        ("canonical", queries["canonical"]),
        ("paraphrase", queries["paraphrase"]),
    ]:
        for q in qlist:
            messages, duration_ms = await _run_turn_safe(role, q)
            tool_calls = _extract_tool_calls(messages)
            memory_called = "memory_search" in tool_calls
            canon_in_result = False
            if memory_called:
                returns = _extract_tool_return(messages, "memory_search")
                canon_in_result = any("Character canon:" in r for r in returns)

            hit = memory_called and canon_in_result
            if kind == "canonical":
                canonical_hits += int(hit)
            else:
                paraphrase_hits += int(hit)
            verdict = "PASS" if hit else "FAIL"
            print(
                f"    [{kind}] '{q[:45]}' memory_called={memory_called} canon_hit={canon_in_result} → {verdict}"
            )
            details.append(
                {
                    "kind": kind,
                    "query": q,
                    "memory_called": memory_called,
                    "canon_hit": canon_in_result,
                    "verdict": verdict,
                    "duration_ms": duration_ms,
                }
            )

    canonical_pass = canonical_hits >= 3
    paraphrase_warn = paraphrase_hits < 2
    print(
        f"  canonical: {canonical_hits}/3 {'PASS' if canonical_pass else 'FAIL'} | "
        f"paraphrase: {paraphrase_hits}/3 {'OK' if not paraphrase_warn else 'WARN <2/3'}"
    )
    return {
        "role": role,
        "canonical_hits": canonical_hits,
        "canonical_pass": canonical_pass,
        "paraphrase_hits": paraphrase_hits,
        "paraphrase_warn": paraphrase_warn,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Phase 3: No-bleed
# ---------------------------------------------------------------------------


async def run_no_bleed() -> dict:
    """Verify technical queries don't produce spurious canon hits."""
    print("  [no-bleed]")
    failures = 0
    details: list[dict] = []
    for q in _NO_BLEED_QUERIES:
        messages, duration_ms = await _run_turn_safe("tars", q)
        tool_calls = _extract_tool_calls(messages)
        canon_poisoned = False
        if "memory_search" in tool_calls:
            returns = _extract_tool_return(messages, "memory_search")
            canon_poisoned = any("Character canon:" in r for r in returns)
        verdict = "FAIL" if canon_poisoned else "PASS"
        if canon_poisoned:
            failures += 1
        print(f"    '{q[:50]}' canon_poisoned={canon_poisoned} → {verdict}")
        details.append(
            {
                "query": q,
                "memory_called": "memory_search" in tool_calls,
                "canon_poisoned": canon_poisoned,
                "verdict": verdict,
                "duration_ms": duration_ms,
            }
        )
    return {
        "verdict": "PASS" if failures == 0 else "FAIL",
        "failures": failures,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Phase 4: M sweep (direct search_canon, no LLM)
# ---------------------------------------------------------------------------


def run_m_sweep() -> dict:
    """Sweep CO_CHARACTER_RECALL_LIMIT over canon-recall + no-bleed query sets."""
    print("  [m-sweep]")
    all_queries: list[tuple[str, str, str]] = []
    for role in _ROLES:
        for q in _CANON_QUERIES[role]["canonical"] + _CANON_QUERIES[role]["paraphrase"]:
            all_queries.append((role, q, "canon"))
    for q in _NO_BLEED_QUERIES:
        all_queries.append(("tars", q, "no-bleed"))

    sweep_results = {}
    for m in (1, 2, 3, 5):
        canon_hits = 0
        canon_total = 0
        bleed_count = 0
        bleed_total = 0
        for role, q, kind in all_queries:
            hits = search_canon(q, role=role, limit=m)
            if kind == "canon":
                canon_total += 1
                if hits:
                    canon_hits += 1
            else:
                bleed_total += 1
                if hits:
                    bleed_count += 1
        pass_rate = canon_hits / canon_total if canon_total else 0.0
        sweep_results[str(m)] = {
            "m": m,
            "canon_recall_pass_rate": round(pass_rate, 3),
            "no_bleed_false_positives": bleed_count,
        }
        print(
            f"    M={m}: canon_recall={canon_hits}/{canon_total}={pass_rate:.1%} bleed_fps={bleed_count}/{bleed_total}"
        )
    return sweep_results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=== eval_character_memory_recall ===\n")

    # Pre-flight: baselines must exist
    for role in _ROLES:
        baseline_path = _BASELINES_DIR / f"character-voice-{role}.json"
        if not baseline_path.exists():
            print(
                f"TASK-7 baseline not found — capture before running: {baseline_path}",
                file=sys.stderr,
            )
            return 1

    # Load baselines
    baselines = {}
    for role in _ROLES:
        with open(_BASELINES_DIR / f"character-voice-{role}.json") as f:
            baselines[role] = json.load(f)

    # Phase 1: Voice delta
    print("--- Phase 1: Voice delta gate ---")
    voice_results = []
    for role in _ROLES:
        voice_results.append(await run_voice_delta(role, baselines[role]))
    voice_pass = all(r["verdict"] == "PASS" for r in voice_results)
    print(f"Voice delta: {'PASS' if voice_pass else 'FAIL'}\n")

    # Phase 2: Canon recall
    print("--- Phase 2: Canon recall ---")
    canon_results = []
    for role in _ROLES:
        canon_results.append(await run_canon_recall(role))
    canon_pass = all(r["canonical_pass"] for r in canon_results)
    paraphrase_warns = [r for r in canon_results if r["paraphrase_warn"]]
    print(f"Canon recall: {'PASS' if canon_pass else 'FAIL'}\n")

    # Phase 3: No-bleed
    print("--- Phase 3: No-bleed ---")
    bleed_result = await run_no_bleed()
    bleed_pass = bleed_result["verdict"] == "PASS"
    print(f"No-bleed: {'PASS' if bleed_pass else 'FAIL'}\n")

    # Phase 4: M sweep
    print("--- Phase 4: M sweep ---")
    sweep = run_m_sweep()
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    sweep_path = _OUTPUTS_DIR / f"character-recall-m-sweep-{ts}.json"
    with open(sweep_path, "w") as f:
        json.dump({"timestamp": datetime.now(UTC).isoformat(), "sweep": sweep}, f, indent=2)
    print(f"M sweep recorded → {sweep_path}\n")

    # Final verdict
    overall_pass = voice_pass and canon_pass and bleed_pass
    canonical_summary = " / ".join(f"{r['canonical_hits']}/3" for r in canon_results)
    paraphrase_summary = " / ".join(f"{r['paraphrase_hits']}/3" for r in canon_results)
    sweep_path_str = str(sweep_path.relative_to(Path(__file__).parent.parent))

    if overall_pass:
        print(
            f"PASS — voice / canon-recall (canonical {canonical_summary}, paraphrase {paraphrase_summary}) / no-bleed all green; M sweep recorded → {sweep_path_str}"
        )
    else:
        failures = []
        if not voice_pass:
            failures.append("voice delta")
        if not canon_pass:
            failures.append("canon recall")
        if not bleed_pass:
            failures.append("no-bleed")
        print(f"FAIL — {', '.join(failures)}")

    for r in paraphrase_warns:
        print(
            f"WARN — paraphrase recall {r['paraphrase_hits']}/3 for {r['role']}, consider algorithm upgrade"
        )

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
