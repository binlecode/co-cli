"""Eval: personality-behavior — personality consistency across single and multi-turn cases.

Runs golden JSONL cases through the real agent with personality-specific deps.
Each case is a sequence of turns; single-turn cases are the degenerate case.

Dimensions:    finch, jeff

Prerequisites: LLM provider configured (ollama running, or gemini_api_key set).
               Set LLM_PROVIDER env var if not using the default.

Usage:
    uv run python evals/eval_personality_behavior.py
    uv run python evals/eval_personality_behavior.py --personalities finch jeff
    uv run python evals/eval_personality_behavior.py --case-id finch-no-sycophancy --runs 1
    uv run python evals/eval_personality_behavior.py --case-id jeff-codebase-structure --runs 1
    uv run python evals/eval_personality_behavior.py --runs 5 --threshold 0.70

Configuration — all params, values, and rationale:

  LLM model: read from settings at runtime via get_agent().
    Set LLM_PROVIDER=ollama|gemini and OLLAMA_MODEL / GEMINI_MODEL env vars.
    Default: ollama qwen3:30b-a3b-thinking-2507-q8_0-agentic.
    This is the single most critical reproducibility variable — different models
    produce different personality adherence profiles. The model tag is stamped
    into every result file so runs are always attributable.

  temperature: not overridden — inherits from model quirk file.
    qwen3 quirk: temperature=1.0, top_p=0.95. Never override to 0: thinking
    models produce degenerate tool-call loops at temperature=0 (Google and
    Ollama both document this). Evals must use the same sampling params as
    live sessions to measure real production behavior.

  max_tokens: not overridden — inherits from model quirk file.
    qwen3=32768, glm=16384. Thinking models spend output tokens on
    chain-of-thought before the response; an explicit cap (formerly 2048)
    truncates responses mid-output and corrupts personality signal.

  request_limit: not set — no cap on sequential tool calls per turn.
    Wall-clock time is the only constraint: each case is wrapped in
    asyncio.timeout(max(runs * 150, 180)s). Capping request count
    would silently cut off multi-step tool chains before they complete,
    producing artificially truncated responses and misleading failures.

  web_policy / brave_search_api_key: read from settings, not overridden.
    If web_policy.search == "deny" or key is absent, web_search raises
    ModelRetry and the model skips the tool. This changes behavior for cases
    that trigger web search (e.g. jeff-uncertainty). Run with a live key for
    full fidelity; without it, the tool-use path is not exercised.

  tool_output_trim_chars: read from settings default (2000 chars).
    Trims tool result text before injecting into context. At 2000 chars,
    web_search snippets are cut to ~4-5 results worth of text. This affects
    how much evidence the model sees when synthesizing a response.

  max_history_messages: read from settings default (40 messages).
    Sliding window for multi-turn context. At 40 messages, all 3-turn eval
    cases fit comfortably. Only relevant if eval cases grow to many turns.

  doom_loop_threshold: read from settings default (3 identical tool calls).
    If the model repeats the same tool call 3 times in sequence, the agent
    aborts the turn. Can cause unexpected ERROR results in eval runs if the
    model loops on recall_memory. Visible as error_msg in the output JSON.

  --run-timeout default=120s: per-turn wall-clock cap. Each agent.run() call
    is individually wrapped — a 3-turn case gets 3 independent 120s windows.
    A timed-out turn aborts the run (history is incomplete); next run starts.
    Observed worst-case: ~67s/turn; 120s gives ~2× headroom.

  case timeout: runs * turn_timeout * n_turns + 60 seconds.
    Outer safety net wrapping ALL runs of one case.

  --runs default=1: fast local default. Use --runs 3 (or any odd number) for
    majority-vote stability: a case passes if pass_count > len(valid_runs) / 2.
    Odd counts prevent ties. --runs 3 requires 2/3 majority, reducing noise
    from thinking-model stochasticity at the cost of ~3× wall time.

  --threshold default=0.80: 80% of cases must reach majority-pass. At the
    current 4-case set this is effectively a 4/4 hard gate (3/4 = 75% < 80%).
    Becomes a genuine tolerance once the case set grows to ≥10.

  session_id scoped to personality+case.id: prevents cross-case memory
    contamination. If the model calls save_memory during a case, a shared
    session lets that memory influence later cases of the same personality.
    Full isolation requires a tmp knowledge dir override in deps (not yet
    implemented).
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import DeferredToolRequests
from pydantic_ai.settings import ModelSettings

from co_cli.agent import get_agent
from co_cli.config import DATA_DIR

from evals._common import (
    detect_model_tag,
    make_eval_deps,
    make_eval_settings,
    EvalCase,
    load_cases,
    score_turn,
    TurnTrace,
    bootstrap_telemetry,
    collect_spans_for_run,
    analyze_turn_spans,
    _md_cell,
    _check_display,
    _check_result,
    _check_match_detail,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TurnRun:
    """Result of one turn within a single multi-turn run."""
    turn_idx: int
    passed: bool
    elapsed_s: float | None = None
    response_text: str | None = None
    failed_checks: list[str] | None = None
    prompt: str | None = None
    trace: TurnTrace | None = None
    # judge_details: check_index → "PASS: reasoning" or "FAIL: reasoning"
    # populated for every llm_judge check regardless of outcome
    judge_details: dict[int, str] | None = None


@dataclass
class RunResult:
    """Result of one complete multi-turn run (all turns executed in sequence)."""
    passed: bool
    error: bool
    turn_runs: list[TurnRun] = field(default_factory=list)
    error_msg: str | None = None
    # turn index where the run stopped (on error)
    stopped_at_turn: int | None = None
    # drift: turn 0 passed, a later turn failed
    drift: bool = False
    # tool_leakage: any turn returned DeferredToolRequests
    tool_leakage: bool = False


@dataclass
class CaseResult:
    case: EvalCase
    runs: list[RunResult] = field(default_factory=list)

    @property
    def valid_runs(self) -> list[RunResult]:
        return [r for r in self.runs if not r.error]

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.valid_runs if r.passed)

    @property
    def majority_pass(self) -> bool:
        valid = self.valid_runs
        if not valid:
            return False
        return self.pass_count > len(valid) / 2

    @property
    def all_errors(self) -> bool:
        return len(self.valid_runs) == 0 and len(self.runs) > 0

    @property
    def status(self) -> str:
        if self.all_errors:
            return "ERROR"
        return "PASS" if self.majority_pass else "FAIL"

    @property
    def drift_count(self) -> int:
        return sum(1 for r in self.valid_runs if r.drift)

    @property
    def leakage_count(self) -> int:
        return sum(1 for r in self.valid_runs if r.tool_leakage)


# ---------------------------------------------------------------------------
# Transient error detection
# ---------------------------------------------------------------------------

TRANSIENT_PATTERNS = [
    "rate limit",
    "429",
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
]


def is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in TRANSIENT_PATTERNS)


def _case_timeout_s(runs: int, turn_timeout: int, n_turns: int) -> int:
    # Outer safety net: wraps all runs of one case.
    return runs * turn_timeout * n_turns + 60


# ---------------------------------------------------------------------------
# Multi-turn runner (handles 1-turn and N-turn cases uniformly)
# ---------------------------------------------------------------------------


async def run_single(
    agent: Any,
    deps: Any,
    model_settings: ModelSettings | None,
    case: EvalCase,
    *,
    turn_timeout: int = 120,
    provider: Any = None,
    db_path: str | None = None,
) -> RunResult:
    """Run one complete multi-turn conversation and return the result.

    Single-turn cases (len(turns)==1) go through the same loop with one
    iteration. Multi-turn cases pass accumulated history to each subsequent
    turn. A RunResult passes only if every turn passes its checks.

    Each turn is individually capped at ``turn_timeout`` seconds. A timed-out
    turn aborts the run — history is incomplete so continuing would be invalid.

    Pass ``provider`` and ``db_path`` to enable per-turn span collection.
    """
    # max_tokens: not overridden — inherits from model quirk file.
    # Thinking models spend output tokens on chain-of-thought before the response;
    # an explicit cap truncates output and corrupts personality signal.
    eval_settings = make_eval_settings(model_settings)

    try:
        history: list[Any] = []
        turn_runs: list[TurnRun] = []
        tool_leakage = False

        for turn_idx, (prompt, checks) in enumerate(
            zip(case.turns, case.checks_per_turn)
        ):
            start_ns = time.time_ns()
            t_turn = time.monotonic()
            try:
                async with asyncio.timeout(turn_timeout):
                    result = await agent.run(
                        prompt,
                        deps=deps,
                        model_settings=eval_settings,
                        message_history=history,
                    )
            except asyncio.TimeoutError:
                return RunResult(
                    passed=False,
                    error=True,
                    turn_runs=turn_runs,
                    error_msg=f"turn {turn_idx + 1} timeout: exceeded {turn_timeout}s",
                    stopped_at_turn=turn_idx,
                )
            elapsed_s = time.monotonic() - t_turn

            # If the model returned DeferredToolRequests, do not advance history —
            # passing unresolved tool calls causes pydantic-ai to error on the
            # next turn. Track as leakage and continue with prior history.
            if isinstance(result.output, DeferredToolRequests):
                tool_leakage = True
                turn_runs.append(TurnRun(
                    turn_idx=turn_idx,
                    passed=False,
                    elapsed_s=elapsed_s,
                    failed_checks=[f"turn {turn_idx + 1}: tool calls instead of text"],
                    prompt=prompt,
                ))
                continue

            history = result.all_messages()
            text = str(result.output)
            failures, judge_details = await score_turn(text, checks, agent, deps, eval_settings)

            # Collect OTel spans for this turn if telemetry is enabled
            turn_trace: TurnTrace | None = None
            if provider is not None and db_path is not None:
                provider.force_flush()
                await asyncio.sleep(0.2)
                spans = collect_spans_for_run(start_ns, db_path)
                if spans:
                    turn_trace = analyze_turn_spans(prompt, checks, spans, elapsed_s)

            turn_runs.append(TurnRun(
                turn_idx=turn_idx,
                passed=len(failures) == 0,
                elapsed_s=elapsed_s,
                response_text=text,
                failed_checks=failures if failures else None,
                prompt=prompt,
                trace=turn_trace,
                judge_details=judge_details if judge_details else None,
            ))

        all_passed = all(tr.passed for tr in turn_runs)
        drift = (
            len(turn_runs) > 1
            and turn_runs[0].passed
            and not all_passed
        )
        return RunResult(
            passed=all_passed,
            error=False,
            turn_runs=turn_runs,
            drift=drift,
            tool_leakage=tool_leakage,
        )

    except asyncio.TimeoutError:
        raise  # propagate to case-level timeout handler in run_eval
    except Exception as exc:
        if is_transient_error(exc):
            return RunResult(
                passed=False,
                error=True,
                error_msg=f"transient: {exc}",
            )
        return RunResult(passed=False, error=False, error_msg=str(exc))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_case_table(results: list[CaseResult]) -> None:
    header = f"{'CASE':<36} {'PERSONALITY':<14} {'RESULT':<8} {'TURNS':<7} {'RUNS':<6} {'TURN TIMES (s)'}"
    print(header)
    print("-" * len(header))
    for cr in results:
        turn_count = len(cr.case.turns)
        pass_str = f"{cr.pass_count}/{len(cr.runs)}"
        # Collect per-turn elapsed from the first valid run that has timing
        turn_times = ""
        for r in cr.valid_runs:
            times = [tr.elapsed_s for tr in r.turn_runs if tr.elapsed_s is not None]
            if times:
                turn_times = " + ".join(f"{t:.1f}" for t in times)
                break
        print(f"{cr.case.id:<36} {cr.case.personality:<14} {cr.status:<8} {turn_count:<7} {pass_str:<6} {turn_times}")
    print()


def compute_dim_stats(results: list[CaseResult]) -> dict[str, dict[str, Any]]:
    """Compute per-personality stats, excluding ERROR cases from gate calculations."""
    dims: dict[str, list[CaseResult]] = {}
    for cr in results:
        dims.setdefault(cr.case.personality, []).append(cr)

    stats: dict[str, dict[str, Any]] = {}
    for dim, cases in sorted(dims.items()):
        scorable = [c for c in cases if not c.all_errors]
        passed = sum(1 for c in scorable if c.majority_pass)
        total = len(scorable)
        accuracy = passed / total if total > 0 else 0.0
        total_valid_runs = sum(len(c.valid_runs) for c in scorable)
        drift_count = sum(c.drift_count for c in scorable)
        leakage_count = sum(c.leakage_count for c in scorable)
        stats[dim] = {
            "cases": len(cases),
            "scorable": total,
            "passed": passed,
            "accuracy": accuracy,
            "errors": len(cases) - total,
            "total_valid_runs": total_valid_runs,
            "drift_count": drift_count,
            "drift_rate": drift_count / total_valid_runs if total_valid_runs > 0 else 0.0,
            "leakage_count": leakage_count,
            "leakage_rate": leakage_count / total_valid_runs if total_valid_runs > 0 else 0.0,
        }
    return stats


def print_dim_summary(stats: dict[str, dict[str, Any]]) -> float:
    header = f"{'PERSONALITY':<14} {'CASES':<8} {'PASSED':<8} {'ACCURACY':<10} {'DRIFT':<8} {'ERRORS'}"
    print(header)
    print("-" * len(header))

    total_scorable = 0
    total_passed = 0
    total_errors = 0
    total_drift = 0
    for dim, s in stats.items():
        acc_str = f"{s['accuracy']:.1%}"
        err_str = str(s["errors"]) if s["errors"] > 0 else ""
        drift_str = str(s["drift_count"]) if s["drift_count"] > 0 else ""
        print(f"{dim:<14} {s['scorable']:<8} {s['passed']:<8} {acc_str:<10} {drift_str:<8} {err_str}")
        total_scorable += s["scorable"]
        total_passed += s["passed"]
        total_errors += s["errors"]
        total_drift += s["drift_count"]

    print("-" * len(header))
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    err_str = str(total_errors) if total_errors > 0 else ""
    drift_str = str(total_drift) if total_drift > 0 else ""
    print(f"{'OVERALL':<14} {total_scorable:<8} {total_passed:<8} {overall_acc:.1%}{'':<5} {drift_str:<8} {err_str}")
    print()

    return overall_acc


def check_gates(
    stats: dict[str, dict[str, Any]],
    threshold: float,
    max_drift_rate: float | None = None,
    max_tool_leakage: float | None = None,
) -> int:
    """Check gates. Returns exit code (0=pass, 1=fail)."""
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0

    abs_pass = overall_acc >= threshold
    status = "PASS" if abs_pass else "FAIL"
    print(f"Absolute gate:  {status} ({overall_acc:.1%} {'≥' if abs_pass else '<'} {threshold:.1%})")
    exit_code = 0 if abs_pass else 1

    if max_drift_rate is not None:
        total_valid = sum(s["total_valid_runs"] for s in stats.values())
        total_drift = sum(s["drift_count"] for s in stats.values())
        drift_rate = total_drift / total_valid if total_valid > 0 else 0.0
        drift_pass = drift_rate <= max_drift_rate
        drift_status = "PASS" if drift_pass else "FAIL"
        print(f"Drift gate:     {drift_status} ({drift_rate:.1%} {'≤' if drift_pass else '>'} {max_drift_rate:.1%})")
        if not drift_pass:
            exit_code = 1

    if max_tool_leakage is not None:
        total_valid = sum(s["total_valid_runs"] for s in stats.values())
        total_leakage = sum(s["leakage_count"] for s in stats.values())
        leakage_rate = total_leakage / total_valid if total_valid > 0 else 0.0
        leakage_pass = leakage_rate <= max_tool_leakage
        leakage_status = "PASS" if leakage_pass else "FAIL"
        print(f"Leakage gate:   {leakage_status} ({leakage_rate:.1%} {'≤' if leakage_pass else '>'} {max_tool_leakage:.1%})")
        if not leakage_pass:
            exit_code = 1

    return exit_code


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

_OUTPUT_DIR = Path(__file__).parent


def _build_result_data(
    results: list[CaseResult],
    stats: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    model_tag: str,
) -> dict[str, Any]:
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0

    return {
        "model": model_tag,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "runs": args.runs,
            "threshold": args.threshold,
            "personality_filter": args.personalities,
            "case_id_filter": args.case_id,
        },
        "overall_accuracy": overall_acc,
        "dim_stats": stats,
        "cases": [
            {
                "id": cr.case.id,
                "personality": cr.case.personality,
                "turn_count": len(cr.case.turns),
                "status": cr.status,
                "pass_count": cr.pass_count,
                "total_runs": len(cr.runs),
                "valid_runs": len(cr.valid_runs),
                "runs": [
                    {
                        "passed": r.passed,
                        "error": r.error,
                        "error_msg": r.error_msg,
                        "drift": r.drift,
                        "tool_leakage": r.tool_leakage,
                        "turns": [
                            {
                                "turn_idx": tr.turn_idx,
                                "passed": tr.passed,
                                "elapsed_s": round(tr.elapsed_s, 2) if tr.elapsed_s is not None else None,
                                "response_text": tr.response_text,
                                "failed_checks": tr.failed_checks,
                            }
                            for tr in r.turn_runs
                        ],
                    }
                    for r in cr.runs
                ],
            }
            for cr in results
        ],
    }


def save_data_json(
    results: list[CaseResult],
    stats: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    model_tag: str,
    path: Path | None = None,
) -> Path:
    path = path or _OUTPUT_DIR / "personality_behavior-data.json"
    data = _build_result_data(results, stats, args, model_tag)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def save_result_md(
    results: list[CaseResult],
    stats: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    exit_code: int,
    elapsed: float,
    model_tag: str,
    path: Path | None = None,
) -> Path:
    path = path or _OUTPUT_DIR / "personality_behavior-result.md"
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    total_errors = sum(s["errors"] for s in stats.values())
    total_drift = sum(s["drift_count"] for s in stats.values())

    gate_status = "PASS" if exit_code == 0 else "FAIL"
    lines: list[str] = []
    w = lines.append

    w(f"# Eval: personality-behavior — {gate_status}")
    w("")
    w(f"**Model**: {model_tag}  ")
    w(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"**Runs per case**: {args.runs}  ")
    w(f"**Threshold**: {args.threshold:.0%}  ")
    if args.personalities:
        w(f"**Personality filter**: {', '.join(args.personalities)}  ")
    if args.case_id:
        w(f"**Case filter**: {args.case_id}  ")
    w(f"**Elapsed**: {elapsed:.1f}s  ")
    w(f"**Overall accuracy**: {overall_acc:.1%} ({total_passed}/{total_scorable})")
    if total_errors > 0:
        w(f"**Transient errors**: {total_errors} case(s) excluded")
    if total_drift > 0:
        w(f"**Drift events**: {total_drift} (turn 0 passed, later turn failed)")
    w("")

    w("## Per-Case Results")
    w("")
    w("| Case | Personality | Turns | Result | Runs |")
    w("|------|-------------|-------|--------|------|")
    for cr in results:
        pass_str = f"{cr.pass_count}/{len(cr.runs)}"
        turn_count = len(cr.case.turns)
        w(f"| {cr.case.id} | {cr.case.personality} | {turn_count} | **{cr.status}** | {pass_str} |")
    w("")

    w("## Per-Personality Summary")
    w("")
    w("| Personality | Cases | Passed | Accuracy | Drift | Errors |")
    w("|-------------|-------|--------|----------|-------|--------|")
    for dim, s in stats.items():
        acc_str = f"{s['accuracy']:.1%}"
        err_str = str(s["errors"]) if s["errors"] > 0 else "-"
        drift_str = str(s["drift_count"]) if s["drift_count"] > 0 else "-"
        w(f"| {dim} | {s['scorable']} | {s['passed']} | {acc_str} | {drift_str} | {err_str} |")
    err_total = str(total_errors) if total_errors > 0 else "-"
    drift_total = str(total_drift) if total_drift > 0 else "-"
    w(f"| **OVERALL** | **{total_scorable}** | **{total_passed}** | **{overall_acc:.1%}** | {drift_total} | {err_total} |")
    w("")

    w("## Gates")
    w("")
    abs_pass = overall_acc >= args.threshold
    w(f"- **Absolute gate**: {'PASS' if abs_pass else 'FAIL'} "
      f"({overall_acc:.1%} {'≥' if abs_pass else '<'} {args.threshold:.1%})")
    if args.max_drift_rate is not None:
        total_valid = sum(s["total_valid_runs"] for s in stats.values())
        drift_rate = total_drift / total_valid if total_valid > 0 else 0.0
        drift_pass = drift_rate <= args.max_drift_rate
        w(f"- **Drift gate**: {'PASS' if drift_pass else 'FAIL'} "
          f"({drift_rate:.1%} {'≤' if drift_pass else '>'} {args.max_drift_rate:.1%})")
    if args.max_tool_leakage is not None:
        total_valid = sum(s["total_valid_runs"] for s in stats.values())
        total_leakage = sum(s["leakage_count"] for s in stats.values())
        leakage_rate = total_leakage / total_valid if total_valid > 0 else 0.0
        leakage_pass = leakage_rate <= args.max_tool_leakage
        w(f"- **Leakage gate**: {'PASS' if leakage_pass else 'FAIL'} "
          f"({leakage_rate:.1%} {'≤' if leakage_pass else '>'} {args.max_tool_leakage:.1%})")
    w("")

    failed = [cr for cr in results if cr.status in ("FAIL", "ERROR")]
    if failed:
        w("## Failed / Error Cases")
        w("")
        for cr in failed:
            w(f"### {cr.case.id} — {cr.status}")
            w(f"- **Personality**: {cr.case.personality}")
            w(f"- **Turns**: {len(cr.case.turns)}")
            for run_i, r in enumerate(cr.runs, 1):
                if r.error:
                    w(f"- Run {run_i}: ERROR — {r.error_msg}")
                    continue
                run_label = "PASS" if r.passed else "FAIL"
                drift_tag = " [DRIFT]" if r.drift else ""
                leakage_tag = " [LEAKAGE]" if r.tool_leakage else ""
                w(f"- Run {run_i}: {run_label}{drift_tag}{leakage_tag}")
                for tr in r.turn_runs:
                    if not tr.passed:
                        prompt_preview = cr.case.turns[tr.turn_idx][:60]
                        response_preview = (tr.response_text or "")[:200]
                        w(f"  - Turn {tr.turn_idx + 1} FAIL: {', '.join(tr.failed_checks or [])}")
                        w(f"    - Prompt: {prompt_preview!r}")
                        if tr.response_text:
                            w(f"    - Response: {response_preview!r}")
            w("")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Trace report helpers
# ---------------------------------------------------------------------------


def _extract_system_text(instructions: list[dict[str, Any]]) -> str:
    """Extract plain text from a system instructions list.

    Handles both a flat {"content": "..."} format and the pydantic-ai OTel format
    {"role": "system", "parts": [{"type": "text", "content": "..."}]}.
    """
    collected: list[str] = []
    for item in instructions:
        if isinstance(item, str):
            collected.append(item)
            continue
        if not isinstance(item, dict):
            continue
        # Flat content key
        content = item.get("content") or item.get("text") or ""
        if isinstance(content, str) and content:
            collected.append(content)
            continue
        # pydantic-ai OTel format: parts list
        for part in item.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("content") or part.get("text") or ""
                if text:
                    collected.append(text)
    return "\n\n".join(collected)


def _format_msg_content(content: Any, max_chars: int = 300) -> str:
    """Format a message content value for display in the input messages table."""
    if isinstance(content, str):
        truncated = content[:max_chars]
        if len(content) > max_chars:
            truncated += f"… [{len(content):,} chars]"
        return truncated
    if isinstance(content, list):
        rendered_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                rendered_parts.append(str(part)[:80])
                continue
            ptype = part.get("type", "")
            if ptype == "text":
                text = str(part.get("text", "") or part.get("content", ""))
                rendered_parts.append(text[:100])
            elif ptype in ("tool_call", "tool-call"):
                name = part.get("name") or part.get("tool_name", "?")
                args = part.get("arguments") or part.get("args", {})
                args_str = (args if isinstance(args, str) else json.dumps(args))[:60]
                call_id = part.get("id") or part.get("tool_call_id") or ""
                id_str = f" [{call_id}]" if call_id else ""
                rendered_parts.append(f"[tool_call: {name}({args_str}){id_str}]")
            elif ptype == "tool_call_response":
                name = part.get("name") or "?"
                result = part.get("result", "")
                result_str = (result if isinstance(result, str) else json.dumps(result))[:80]
                call_id = part.get("id") or part.get("tool_call_id") or ""
                id_str = f" [{call_id}]" if call_id else ""
                rendered_parts.append(f"[tool_result: {name}{id_str} → {result_str}]")
            elif ptype == "thinking":
                thinking = str(part.get("content", "") or part.get("thinking", ""))
                rendered_parts.append(f"[thinking: {thinking[:50]}…]")
            else:
                rendered_parts.append(f"[{ptype}]")
        result = " | ".join(rendered_parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "…"
        return result
    return str(content)[:max_chars]


# ---------------------------------------------------------------------------
# Trace report
# ---------------------------------------------------------------------------


def save_trace_md(
    results: list[CaseResult],
    cases_stem: str,
    model_tag: str,
    path: Path | None = None,
) -> Path:
    """Write a per-case, per-turn surgical trace report.

    Shows every model request with full input messages, full thinking, tool calls,
    token counts, and cache breakout. Every tool execution shows full args and
    untruncated result. Check scoring shows which phrase matched.
    """
    path = path or _OUTPUT_DIR / f"{cases_stem}-trace-{time.strftime('%Y%m%d-%H%M%S')}.md"
    lines: list[str] = []
    w = lines.append

    w("# Personality Eval Trace Report")
    w("")
    w(f"**Model**: {model_tag}  ")
    w(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w("")
    w("---")

    for cr in results:
        case = cr.case
        # Use the first valid run with traces; fall back to first valid run; fall back to first run
        trace_run = None
        for r in cr.valid_runs:
            if any(tr.trace is not None for tr in r.turn_runs):
                trace_run = r
                break
        if trace_run is None and cr.valid_runs:
            trace_run = cr.valid_runs[0]
        if trace_run is None and cr.runs:
            trace_run = cr.runs[0]

        # Total wall time across turns for the trace run
        if trace_run and not trace_run.error:
            total_wall = sum(
                tr.elapsed_s for tr in trace_run.turn_runs
                if tr.elapsed_s is not None
            )
            total_wall_str = f"{total_wall:.1f}s"
        else:
            total_wall_str = "—"

        w("")
        w(f"## Case: {case.id} — {case.personality}  {cr.status}")
        w("")

        if trace_run is None:
            w("*(no runs recorded)*")
            w("")
            w("---")
            continue

        if trace_run.error:
            w(f"**Error**: {trace_run.error_msg}")
            w("")
            w("---")
            continue

        n_turns = len(trace_run.turn_runs)
        w(f"**Turns:** {n_turns}  **Total wall time:** {total_wall_str}")
        w("")

        for tr in trace_run.turn_runs:
            turn_pass = "PASS" if tr.passed else "FAIL"
            full_prompt = tr.prompt or ""
            elapsed_str = f"{tr.elapsed_s:.1f}s" if tr.elapsed_s is not None else "—"
            w(f"### Turn {tr.turn_idx + 1} — \"{full_prompt}\"  →  {turn_pass}  ({elapsed_str})")
            w("")

            t = tr.trace
            if t is None:
                w("*(no OTel span data — response and checks shown below)*")
                w("")
            elif t.error:
                w(f"**Trace error**: {t.error}")
                w("")

            if t is None or t.error:
                # No span data: skip model-request details but still show response + checks
                response_text = tr.response_text or ""
                w(f"#### Response (Turn {tr.turn_idx + 1})")
                w("")
                w(response_text if response_text else "*(no text response captured)*")
                w("")
                w("**Checks:**")
                w("")
                w("| Check | Criteria | Judgment / Matched | Result |")
                w("|-------|----------|--------------------|--------|")
                checks_for_turn = case.checks_per_turn[tr.turn_idx]
                failed_for_turn = tr.failed_checks or []
                judge_deets = tr.judge_details or {}
                for check_i, check in enumerate(checks_for_turn):
                    display = _check_display(check)
                    check_type = check.get("type", "")
                    if check_type == "llm_judge":
                        note = judge_deets.get(check_i, "")
                        if note:
                            verdict, _, reasoning = note.partition(": ")
                            matched = reasoning
                            result_str = "PASS" if verdict == "PASS" else f"FAIL — {reasoning}"
                        else:
                            matched = "(not evaluated)"
                            result_str = "PASS"
                    else:
                        matched = _check_match_detail(check, response_text)
                        result_str = _check_result(check, failed_for_turn)
                    w(f"| {check_type} | {_md_cell(display)} | {_md_cell(matched)} | {result_str} |")
                w("")
                continue

            n_reqs = len(t.model_requests)
            n_tools = len(t.tool_spans)
            w(
                f"**Summary:** {n_reqs} LLM request(s), {n_tools} tool call(s), "
                f"in={t.total_input_tokens:,} out={t.total_output_tokens:,} tokens total"
            )
            w("")

            # System instructions — shown once per turn
            sys_text = _extract_system_text(t.system_instructions)
            if not sys_text and t.model_requests:
                sys_text = _extract_system_text(t.model_requests[0].system_instructions)
            w("#### System Instructions")
            w("")
            if sys_text:
                for line in sys_text.splitlines():
                    w(f"> {line}" if line.strip() else ">")
            else:
                w("> *(not captured)*")
            w("")

            # Timeline
            w("#### Timeline")
            w("")
            w("| Elapsed (ms) | Duration (ms) | Span | Detail |")
            w("|---|---|---|---|")
            if t.timeline:
                for trow in t.timeline:
                    w(
                        f"| {trow.elapsed_ms:,} | {trow.duration_ms} | "
                        f"{_md_cell(trow.span_name)} | {_md_cell(trow.detail)} |"
                    )
            else:
                w("| — | — | (no spans) | — |")
            w("")

            # Model requests
            prev_input_tokens = 0
            for req in t.model_requests:
                model_label = req.request_model or req.response_model or "unknown"
                w(f"#### Request {req.request_index} of {n_reqs} — model={model_label}  finish={req.finish_reason}")
                w("")

                # Settings line
                settings_parts: list[str] = []
                if req.temperature is not None:
                    settings_parts.append(f"temp={req.temperature}")
                if req.top_p is not None:
                    settings_parts.append(f"top_p={req.top_p}")
                if req.max_tokens is not None:
                    settings_parts.append(f"max_tokens={req.max_tokens:,}")
                if req.server_address:
                    port_str = f":{req.server_port}" if req.server_port else ""
                    settings_parts.append(f"server={req.server_address}{port_str}")
                if settings_parts:
                    w(f"> Settings: {',  '.join(settings_parts)}")

                # Token line with delta
                token_delta = ""
                if req.request_index > 1 and prev_input_tokens > 0:
                    delta = req.input_tokens - prev_input_tokens
                    sign = "+" if delta >= 0 else ""
                    token_delta = f" (Δ{sign}{delta:,} vs prev)"
                cache_parts = []
                if req.cache_read_tokens:
                    cache_parts.append(f"cache_read={req.cache_read_tokens:,}")
                if req.cache_write_tokens:
                    cache_parts.append(f"cache_write={req.cache_write_tokens:,}")
                cache_str = f"  {',  '.join(cache_parts)}" if cache_parts else ""
                w(
                    f"> Tokens: in={req.input_tokens:,}{token_delta}  "
                    f"out={req.output_tokens:,}{cache_str}"
                )
                w("")
                prev_input_tokens = req.input_tokens

                # Input messages table
                if req.input_messages:
                    w(f"**Input messages ({len(req.input_messages)} total):**")
                    w("")
                    w("| # | Role | Content |")
                    w("|---|------|---------|")
                    for msg_i, msg in enumerate(req.input_messages, 1):
                        role = msg.get("role", "?") if isinstance(msg, dict) else "?"
                        # pydantic-ai OTel messages use "parts" list, not a flat "content" string
                        content = (msg.get("content") or msg.get("parts") or "") if isinstance(msg, dict) else msg
                        content_str = _format_msg_content(content, max_chars=300)
                        w(f"| {msg_i} | {role} | {_md_cell(content_str)} |")
                    w("")

                # Full thinking in fenced code block
                if req.thinking_full:
                    w("**Thinking (full):**")
                    w("")
                    w("```")
                    w(req.thinking_full)
                    w("```")
                    w("")
                else:
                    w("**Thinking:** none")
                    w("")

                # Tool calls emitted
                if req.tool_calls:
                    w("**Tool calls emitted:**")
                    w("")
                    for tc in req.tool_calls:
                        tc_name = tc.get("name") or tc.get("tool_name", "unknown")
                        tc_args_raw = tc.get("arguments") or tc.get("args", "{}")
                        if isinstance(tc_args_raw, str):
                            try:
                                tc_args = json.loads(tc_args_raw)
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {"raw": tc_args_raw}
                        elif isinstance(tc_args_raw, dict):
                            tc_args = tc_args_raw
                        else:
                            tc_args = {}
                        call_id = tc.get("tool_call_id") or tc.get("id") or ""
                        call_id_str = f"  [call_id: {call_id}]" if call_id else ""
                        w(f"- `{tc_name}({json.dumps(tc_args)})`{call_id_str}")
                    w("")

            # Tool spans
            for ts in t.tool_spans:
                call_id_str = f"  [call_id: {ts.tool_call_id}]" if ts.tool_call_id else ""
                dur_str = f"  ({int(ts.duration_ms)}ms)" if ts.duration_ms is not None else ""
                w(f"#### Tool: {ts.tool_name}{call_id_str}{dur_str}")
                w("")

                w("**Arguments:**")
                w("")
                w("```json")
                w(json.dumps(ts.arguments, indent=2))
                w("```")
                w("")

                w("**Result (full):**")
                w("")
                w("```")
                w(ts.result_full if ts.result_full else "(empty)")
                w("```")
                w("")

                if ts.exception_events:
                    w("**Exception:**")
                    w("")
                    w("```")
                    for evt in ts.exception_events:
                        attrs = evt.get("attributes") or evt
                        exc_type = attrs.get("exception.type", "")
                        exc_msg = attrs.get("exception.message", "")
                        exc_tb = attrs.get("exception.stacktrace", "")
                        if exc_type or exc_msg:
                            w(f"{exc_type}: {exc_msg}")
                        if exc_tb:
                            w(exc_tb)
                    w("```")
                    w("")

            # Response
            w(f"#### Response (Turn {tr.turn_idx + 1})")
            w("")
            if tr.response_text:
                w(tr.response_text)
            else:
                w("*(no text response captured)*")
            w("")

            # Checks with match detail — uses TurnRun.failed_checks (authoritative)
            # For llm_judge checks, uses TurnRun.judge_details for full reasoning.
            w("**Checks:**")
            w("")
            w("| Check | Criteria | Judgment / Matched | Result |")
            w("|-------|----------|--------------------|--------|")
            checks_for_turn = case.checks_per_turn[tr.turn_idx]
            failed_for_turn = tr.failed_checks or []
            response_for_checks = tr.response_text or ""
            judge_deets = tr.judge_details or {}
            for check_i, check in enumerate(checks_for_turn):
                display = _check_display(check)
                check_type = check.get("type", "")
                if check_type == "llm_judge":
                    note = judge_deets.get(check_i, "")
                    # note format: "PASS: reasoning" or "FAIL: reasoning"
                    if note:
                        verdict, _, reasoning = note.partition(": ")
                        matched = reasoning
                        result_str = "PASS" if verdict == "PASS" else f"FAIL — {reasoning}"
                    else:
                        matched = "(not evaluated)"
                        result_str = "PASS"
                else:
                    matched = _check_match_detail(check, response_for_checks)
                    result_str = _check_result(check, failed_for_turn)
                w(f"| {check_type} | {_md_cell(display)} | {_md_cell(matched)} | {result_str} |")
            w("")

        w("---")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_eval(args: argparse.Namespace) -> int:
    model_tag = args.model_tag or detect_model_tag()

    jsonl_path = Path(args.cases)
    cases = load_cases(jsonl_path)
    print(f"Loaded {len(cases)} eval cases from {jsonl_path}")
    print(f"Model: {model_tag}\n")

    if args.personalities:
        cases = [c for c in cases if c.personality in args.personalities]
        print(f"Filtered to {len(cases)} cases (personalities={', '.join(sorted(args.personalities))})")
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
        print(f"Filtered to {len(cases)} case(s) (id={args.case_id})")

    if not cases:
        print("No cases to run.")
        return 0

    # Create one agent per unique personality — soul seed is baked into the
    # static system prompt at agent creation time (get_agent(personality=…)).
    # A single get_agent() without personality= would skip the soul seed entirely,
    # breaking the new seed+strategy personality architecture.
    unique_personalities = sorted({c.personality for c in cases})
    personality_agents: dict[str, tuple[Any, ModelSettings | None, list[str]]] = {}
    for p in unique_personalities:
        p_agent, p_ms, p_tn = get_agent(personality=p)
        personality_agents[p] = (p_agent, p_ms, p_tn)
        print(f"Agent({p}) created with {len(p_tn)} tools")
    print(f"Running {args.runs} run(s) per case, threshold={args.threshold:.0%}\n")

    db_path = str(DATA_DIR / "co-cli.db")
    provider = bootstrap_telemetry(db_path)

    t0 = time.monotonic()
    results: list[CaseResult] = []

    # Deps keyed by personality+case.id — each case gets its own session so
    # save_memory calls in one case cannot contaminate later cases of the same
    # personality.
    personality_deps: dict[str, Any] = {}

    for i, case in enumerate(cases, 1):
        print(
            f"Case {i}/{len(cases)}: {case.id} ({case.personality}) ...",
            end=" ", flush=True,
        )
        cr = CaseResult(case=case)

        deps_key = f"{case.personality}-{case.id}"
        if deps_key not in personality_deps:
            personality_deps[deps_key] = make_eval_deps(
                # session_id scoped to personality+case to prevent cross-run
                # memory contamination if the model calls save_memory mid-eval.
                session_id=f"eval-pb-{case.personality}-{case.id}",
                personality=case.personality,
            )
        deps = personality_deps[deps_key]

        case_agent, case_model_settings, _ = personality_agents[case.personality]

        n_turns = len(case.turns)
        try:
            async with asyncio.timeout(_case_timeout_s(args.runs, args.run_timeout, n_turns)):
                for _run_idx in range(args.runs):
                    run_result = await run_single(
                        case_agent, deps, case_model_settings, case,
                        turn_timeout=args.run_timeout,
                        provider=provider, db_path=db_path,
                    )
                    cr.runs.append(run_result)
                    if run_result.error:
                        print("E", end="", flush=True)
                    elif run_result.passed:
                        print(".", end="", flush=True)
                    elif n_turns > 1:
                        # Show per-turn marks for multi-turn failures
                        turn_marks = "".join(
                            "." if tr.passed else "x"
                            for tr in run_result.turn_runs
                        )
                        print(f"[{turn_marks}]", end="", flush=True)
                    else:
                        print("x", end="", flush=True)
        except asyncio.TimeoutError:
            remaining = args.runs - len(cr.runs)
            for _ in range(remaining):
                cr.runs.append(RunResult(
                    passed=False,
                    error=True,
                    error_msg=f"case timeout: exceeded {_case_timeout_s(args.runs, args.run_timeout, n_turns)}s",
                ))
            print("T", end="", flush=True)

        print(f" {cr.status}")
        results.append(cr)

    elapsed = time.monotonic() - t0
    print()

    print("=" * 60)
    print(f"RESULTS — {model_tag}")
    print("=" * 60)
    print()

    print_case_table(results)

    stats = compute_dim_stats(results)
    print_dim_summary(stats)

    exit_code = check_gates(stats, args.threshold, args.max_drift_rate, args.max_tool_leakage)

    print()
    cases_stem = jsonl_path.stem
    save_path = Path(args.save) if args.save else _OUTPUT_DIR / f"{cases_stem}-data.json"
    report_path = _OUTPUT_DIR / f"{cases_stem}-result.md"
    ts = time.strftime("%Y%m%d-%H%M%S")
    trace_path = _OUTPUT_DIR / f"{cases_stem}-trace-{ts}.md"
    json_path = save_data_json(results, stats, args, model_tag, path=save_path)
    md_path = save_result_md(
        results, stats, args, exit_code, elapsed, model_tag,
        path=report_path,
    )
    trace_md_path = save_trace_md(results, cases_stem, model_tag, path=trace_path)
    print(f"Report saved to {md_path}")
    print(f"Data saved to   {json_path}")
    print(f"Trace saved to  {trace_md_path}")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Eval: personality behavior — 1-turn + multi-turn consistency"
    )
    parser.add_argument(
        "--cases", type=str,
        default=str(Path(__file__).parent / "personality_behavior.jsonl"),
        help="Path to eval cases JSONL (default: evals/personality_behavior.jsonl)",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Runs per case (odd recommended for majority vote, default: 1)",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.80,
        help="Absolute pass rate gate 0.0-1.0 (default: 0.80)",
    )
    parser.add_argument(
        "--personalities", nargs="+", default=None,
        help="Filter to specific personalities (default: all)",
    )
    parser.add_argument(
        "--case-id", type=str, default=None,
        help="Run a single case by ID",
    )
    parser.add_argument(
        "--model-tag", type=str, default=None,
        help="Label for this run. Auto-detected from config if omitted.",
    )
    parser.add_argument(
        "--save", type=str, default=None,
        help="Save results JSON to custom path",
    )
    parser.add_argument(
        "--run-timeout", type=int, default=120,
        dest="run_timeout",
        help="Per-turn wall-clock cap in seconds (default: 180). Actual run limit = run_timeout * turns. Timed-out runs are marked ERROR.",
    )
    parser.add_argument(
        "--max-drift-rate", type=float, default=None,
        dest="max_drift_rate",
        help="Optional guardrail: fail if drift_rate > this value (0.0-1.0)",
    )
    parser.add_argument(
        "--max-tool-leakage", type=float, default=None,
        dest="max_tool_leakage",
        help="Optional guardrail: fail if tool_leakage_rate > this value (0.0-1.0)",
    )

    args = parser.parse_args()
    return asyncio.run(run_eval(args))


if __name__ == "__main__":
    sys.exit(main())
