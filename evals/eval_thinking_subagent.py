#!/usr/bin/env python3
"""Eval: run_thinking_subagent — verify the thinking subagent is called and returns structured reasoning.

Gates:
  tool_call_rate     >= 1.00  (run_thinking_subagent called on every case)
  plan_nonempty      >= 1.00  (plan field non-empty on every case)
  steps_sufficient   >= 1.00  (steps list has >= 2 entries on every case)
  conclusion_nonempty >= 1.00 (conclusion field non-empty on every case)
  final_text_rate    >= 1.00  (agent produces a final text response on every case)

Cases are designed to demand genuine multi-step decomposition — trivially answerable
prompts would not exercise the thinking model's reasoning capability.

Exit codes:
  0 — all gates pass
  1 — one or more gates fail
"""

import asyncio
import pathlib
import sys
import time
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from co_cli._model_factory import ModelRegistry
from co_cli.agent import build_agent
from co_cli.config import settings, ROLE_REASONING
from pydantic_ai.messages import ToolReturnPart
from co_cli.context._history import SafetyState
from co_cli.context._orchestrate import run_turn
from co_cli.deps import CoConfig

from evals._common import make_eval_deps, make_eval_settings
from evals._frontend import SilentFrontend
from evals._tools import extract_tool_calls


# ---------------------------------------------------------------------------
# Cases — each requires deep structured reasoning, not surface recall
# ---------------------------------------------------------------------------


@dataclass
class ThinkCase:
    id: str
    prompt: str


CASES: list[ThinkCase] = [
    ThinkCase(
        id="rca-ci-timeout",
        prompt=(
            "Use run_thinking_subagent to reason through this: "
            "A production CI pipeline runs integration tests that pass locally every time "
            "but fail with a 30-second timeout on exactly one step — `docker exec` into a "
            "running container — only on the second test run within the same CI job, never "
            "the first. The container is healthy. The command is identical. "
            "Decompose the possible root causes and produce a ranked diagnosis plan."
        ),
    ),
    ThinkCase(
        id="architecture-tradeoff",
        prompt=(
            "Use run_thinking_subagent to reason through this: "
            "co-cli currently stores all knowledge as flat markdown files with FTS5 full-text "
            "search in SQLite. A user asks whether to migrate to a vector database for semantic "
            "search. Decompose the tradeoff: what does FTS5 do well that vectors don't, what "
            "does vector search do well that FTS5 can't, and under what concrete usage conditions "
            "would migration be worth the operational cost? Produce a structured recommendation."
        ),
    ),
    ThinkCase(
        id="context-window-degradation",
        prompt=(
            "Use run_thinking_subagent to reason through this: "
            "A user reports that after approximately 40 turns in a single co-cli session, "
            "responses become noticeably slower and less accurate — the agent starts repeating "
            "itself, misses context from earlier in the conversation, and occasionally calls "
            "the wrong tool. The LLM provider and model have not changed. "
            "Identify the root causes systematically and propose a remediation strategy "
            "with concrete steps ordered by implementation effort."
        ),
    ),
]

THRESHOLDS = {
    "tool_call_rate": 1.00,
    "plan_nonempty": 1.00,
    "steps_sufficient": 1.00,
    "conclusion_nonempty": 1.00,
    "final_text_rate": 1.00,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(
    case: ThinkCase,
    agent: Any,
    deps: Any,
    model_settings: Any,
) -> dict[str, Any]:
    frontend = SilentFrontend(approval_response="y")
    deps.runtime.safety_state = SafetyState()

    t0 = time.monotonic()
    async with asyncio.timeout(180):
        result = await run_turn(
            agent=agent,
            user_input=case.prompt,
            deps=deps,
            message_history=[],
            model_settings=model_settings,
            max_request_limit=15,
            verbose=False,
            frontend=frontend,
        )
    elapsed = time.monotonic() - t0

    calls = extract_tool_calls(result.messages)
    tool_names = [name for name, _ in calls]
    think_calls = [(name, args) for name, args in calls if name == "run_thinking_subagent"]

    # Extract ThinkingResult fields from the ToolReturnPart in message history.
    # ToolReturnPart.content is the dict returned by run_thinking_subagent.
    plan = ""
    steps: list[str] = []
    conclusion = ""
    for msg in result.messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name != "run_thinking_subagent":
                continue
            content = part.content
            if isinstance(content, dict):
                plan = content.get("plan", "")
                steps = content.get("steps", [])
                conclusion = content.get("conclusion", "")

    return {
        "id": case.id,
        "tool_names": tool_names,
        "run_thinking_subagent_called": len(think_calls) > 0,
        "plan_nonempty": bool(plan),
        "steps_sufficient": len(steps) >= 2,
        "conclusion_nonempty": bool(conclusion),
        "final_text": isinstance(result.output, str) and len(result.output) > 10,
        "elapsed": elapsed,
        "outcome": result.outcome,
        "plan_preview": plan[:120] if plan else "",
        "steps_count": len(steps),
        "conclusion_preview": conclusion[:120] if conclusion else "",
    }


# ---------------------------------------------------------------------------
# Metrics + gates
# ---------------------------------------------------------------------------


def compute_metrics(results: list[dict]) -> dict[str, float]:
    n = len(results)
    if n == 0:
        return {k: 0.0 for k in THRESHOLDS}
    return {
        "tool_call_rate": sum(1 for r in results if r["run_thinking_subagent_called"]) / n,
        "plan_nonempty": sum(1 for r in results if r["plan_nonempty"]) / n,
        "steps_sufficient": sum(1 for r in results if r["steps_sufficient"]) / n,
        "conclusion_nonempty": sum(1 for r in results if r["conclusion_nonempty"]) / n,
        "final_text_rate": sum(1 for r in results if r["final_text"]) / n,
    }


def check_gates(metrics: dict) -> list[str]:
    return [
        f"{name}: {val:.2f} < {THRESHOLDS[name]:.2f}"
        for name, val in metrics.items()
        if val < THRESHOLDS[name]
    ]


def print_report(results: list[dict], metrics: dict, failures: list[str]) -> None:
    print("\n=== Thinking Subagent Eval ===\n")
    for r in results:
        status = "PASS" if (
            r["run_thinking_subagent_called"]
            and r["plan_nonempty"]
            and r["steps_sufficient"]
            and r["conclusion_nonempty"]
            and r["final_text"]
        ) else "FAIL"
        print(f"  [{status}] {r['id']} ({r['elapsed']:.1f}s)")
        print(f"    tools called : {r['tool_names']}")
        print(f"    steps        : {r['steps_count']}")
        print(f"    plan         : {r['plan_preview']}")
        print(f"    conclusion   : {r['conclusion_preview']}")
        print()

    print("Gates:")
    for name, val in metrics.items():
        threshold = THRESHOLDS[name]
        status = "PASS" if val >= threshold else "FAIL"
        print(f"  [{status}] {name}: {val:.2f} (threshold: {threshold:.2f})")

    if failures:
        print(f"\nFailed gates: {len(failures)}")
        for f in failures:
            print(f"  x {f}")
    else:
        print("\nAll gates passed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Thinking Subagent (run_thinking_subagent)")
    print("=" * 60)

    config = CoConfig.from_settings(settings, cwd=pathlib.Path.cwd())
    registry = ModelRegistry.from_config(config)

    if not registry.is_configured(ROLE_REASONING):
        print(f"\nSKIP: ROLE_REASONING not configured — run_thinking_subagent cannot run.")
        return 0

    agent = build_agent(config=config).agent
    deps = make_eval_deps(session_id="eval-thinking-subagent", model_registry=registry)
    deps.runtime.safety_state = SafetyState()

    resolved = registry.get(ROLE_REASONING, None)
    model_settings = make_eval_settings(resolved.settings if resolved else None)

    results: list[dict] = []
    for i, case in enumerate(CASES, 1):
        print(f"\n[{i}/{len(CASES)}] {case.id} ...", flush=True)
        try:
            r = await run_case(case, agent, deps, model_settings)
            results.append(r)
            status = "PASS" if (
                r["run_thinking_subagent_called"] and r["plan_nonempty"]
                and r["steps_sufficient"] and r["conclusion_nonempty"]
                and r["final_text"]
            ) else "FAIL"
            print(f"  → {status} ({r['elapsed']:.1f}s, {r['steps_count']} steps)")
        except asyncio.TimeoutError:
            print("  → TIMEOUT (180s)")
            results.append({
                "id": case.id, "tool_names": [], "run_thinking_subagent_called": False,
                "plan_nonempty": False, "steps_sufficient": False,
                "conclusion_nonempty": False, "final_text": False,
                "elapsed": 120.0, "outcome": "timeout",
                "plan_preview": "", "steps_count": 0, "conclusion_preview": "",
            })
        except Exception as exc:
            print(f"  → ERROR: {exc}")
            results.append({
                "id": case.id, "tool_names": [], "run_thinking_subagent_called": False,
                "plan_nonempty": False, "steps_sufficient": False,
                "conclusion_nonempty": False, "final_text": False,
                "elapsed": 0.0, "outcome": f"error: {exc}",
                "plan_preview": "", "steps_count": 0, "conclusion_preview": "",
            })

    metrics = compute_metrics(results)
    failures = check_gates(metrics)
    print_report(results, metrics, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
