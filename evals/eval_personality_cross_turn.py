"""Eval: personality-cross-turn — verify personality holds across a multi-turn conversation.

Runs 3-turn conversations through the real agent for each personality,
scoring each turn's output against heuristic checks.  A run passes only if
ALL turns satisfy the personality constraints.

This is the critical gap that eval_personality_adherence.py (P2) does not
cover: P2 tests single-turn behavior.  Cross-turn tests that personality is
re-injected on every agent.run() call and that model behavior remains
consistent as conversation history grows.

Target flow:   agent.run() × N turns → extract text per turn → score each turn
Critical impact: personality breaks down mid-conversation if the system_prompt
                 hook is not firing on every request, or if model drift occurs
                 as history accumulates.

Dimensions:    terse, finch, friendly, jeff, inquisitive (one per personality)

Prerequisites: LLM provider configured (ollama running, or gemini_api_key set).
               Set LLM_PROVIDER env var if not using the default.

Usage:
    uv run python evals/eval_personality_cross_turn.py
    uv run python evals/eval_personality_cross_turn.py --personality terse
    uv run python evals/eval_personality_cross_turn.py --case-id p3-terse-cross-turn
    uv run python evals/eval_personality_cross_turn.py --runs 5 --threshold 0.70
"""

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import DeferredToolRequests
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent

from evals._common import detect_model_tag, make_eval_deps, make_eval_settings


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    id: str
    personality: str
    turns: list[str]
    checks_per_turn: list[list[dict[str, Any]]]


@dataclass
class TurnRun:
    """Result of one turn within a single multi-turn run."""
    turn_idx: int
    passed: bool
    response_text: str | None = None
    failed_checks: list[str] | None = None


@dataclass
class RunResult:
    """Result of one complete multi-turn run (all turns executed in sequence)."""
    passed: bool
    error: bool
    turn_runs: list[TurnRun] = field(default_factory=list)
    error_msg: str | None = None
    # turn index where the run stopped (on error or first failure mid-run)
    stopped_at_turn: int | None = None


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


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(EvalCase(
                id=raw["id"],
                personality=raw["personality"],
                turns=raw["turns"],
                checks_per_turn=raw["checks_per_turn"],
            ))
    return cases


# ---------------------------------------------------------------------------
# Sentence counting (shared with eval_personality_adherence)
# ---------------------------------------------------------------------------


_SENTENCE_END = re.compile(r'[.!?]+(?:\s|$)')


def count_sentences(text: str) -> int:
    """Count sentences in text, ignoring code blocks."""
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    if _SENTENCE_END.search(text):
        parts = _SENTENCE_END.split(text)
        return sum(1 for p in parts if p.strip())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return max(len(lines), 1) if lines else 0


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------


def check_max_sentences(text: str, params: dict[str, Any]) -> str | None:
    n = params["n"]
    actual = count_sentences(text)
    if actual <= n:
        return None
    return f"max_sentences: got {actual}, expected <= {n}"


def check_min_sentences(text: str, params: dict[str, Any]) -> str | None:
    n = params["n"]
    actual = count_sentences(text)
    if actual >= n:
        return None
    return f"min_sentences: got {actual}, expected >= {n}"


def check_forbidden(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) so "not *always* wrong"
    # doesn't trip a forbidden check on "always" being formatted.
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return f"forbidden: found '{phrase}'"
    return None


def check_required_any(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) before matching so
    # "not *always* wrong" matches the phrase "not always wrong".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return None
    return f"required_any: none of {params['phrases']} found"


def check_no_preamble(text: str, params: dict[str, Any]) -> str | None:
    stripped = text.strip().lower()
    for phrase in params["phrases"]:
        if stripped.startswith(phrase.lower()):
            return f"no_preamble: starts with '{phrase}'"
    return None


def check_has_question(text: str, params: dict[str, Any]) -> str | None:
    if "?" in text:
        return None
    return "has_question: no '?' found"


_CHECK_DISPATCH: dict[str, Any] = {
    "max_sentences": check_max_sentences,
    "min_sentences": check_min_sentences,
    "forbidden": check_forbidden,
    "required_any": check_required_any,
    "no_preamble": check_no_preamble,
    "has_question": check_has_question,
}


def score_response(text: str, checks: list[dict[str, Any]]) -> list[str]:
    """Run all checks against response text. Returns list of failure descriptions."""
    failures: list[str] = []
    for check in checks:
        check_type = check["type"]
        fn = _CHECK_DISPATCH.get(check_type)
        if fn is None:
            failures.append(f"unknown check type: {check_type}")
            continue
        result = fn(text, check)
        if result is not None:
            failures.append(result)
    return failures


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


# ---------------------------------------------------------------------------
# Multi-turn runner
# ---------------------------------------------------------------------------


async def run_single(
    agent: Any,
    deps: Any,
    model_settings: ModelSettings | None,
    case: EvalCase,
) -> RunResult:
    """Run one complete multi-turn conversation and return the result.

    Executes all turns in sequence, passing accumulated message history to
    each subsequent turn. A RunResult passes only if every turn passes its
    checks. Stops early on error; continues through per-turn failures to
    report which turn failed.
    """
    # max_tokens=2048: personality checks are surface-level (sentence count, phrase
    # presence). qwen3 thinking chains can reach 32K tokens; capping at 2048 limits
    # the thinking budget to ~1500 tokens and keeps each turn under ~60s locally.
    eval_settings = make_eval_settings(model_settings, max_tokens=2048)

    try:
        history: list[Any] = []
        turn_runs: list[TurnRun] = []

        for turn_idx, (prompt, checks) in enumerate(
            zip(case.turns, case.checks_per_turn)
        ):
            result = await agent.run(
                prompt,
                deps=deps,
                model_settings=eval_settings,
                message_history=history,
                # request_limit=4: allows up to 2 tool calls + text response per turn.
                # Multi-turn conversations frequently trigger recall_memory before
                # responding — 2 was too tight.
                usage_limits=UsageLimits(request_limit=4),
            )

            # Check output type before updating history.
            # If the model returned DeferredToolRequests, result.all_messages() contains
            # an unresolved tool call. Passing that history to the next turn causes
            # pydantic-ai to error with "unprocessed tool calls in history".
            # Fix: only advance history when the turn produced a real text response.
            if isinstance(result.output, DeferredToolRequests):
                turn_runs.append(TurnRun(
                    turn_idx=turn_idx,
                    passed=False,
                    response_text=None,
                    failed_checks=[f"turn {turn_idx + 1}: tool calls instead of text"],
                ))
                # Keep history from before this turn so subsequent turns can proceed.
                continue

            # Accumulate history for the next turn
            history = result.all_messages()

            text = str(result.output)
            failures = score_response(text, checks)
            turn_runs.append(TurnRun(
                turn_idx=turn_idx,
                passed=len(failures) == 0,
                response_text=text,
                failed_checks=failures if failures else None,
            ))

        all_passed = all(tr.passed for tr in turn_runs)
        return RunResult(passed=all_passed, error=False, turn_runs=turn_runs)

    except Exception as exc:
        if is_transient_error(exc):
            return RunResult(
                passed=False, error=True,
                error_msg=f"transient: {exc}",
            )
        return RunResult(passed=False, error=False, error_msg=str(exc))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_case_table(results: list[CaseResult]) -> None:
    header = f"{'CASE':<36} {'PERSONALITY':<14} {'RESULT':<8} {'RUNS'}"
    print(header)
    print("-" * len(header))
    for cr in results:
        valid = cr.valid_runs
        total = len(cr.runs)
        pass_str = f"{cr.pass_count}/{total}"
        print(f"{cr.case.id:<36} {cr.case.personality:<14} {cr.status:<8} {pass_str}")
    print()


def compute_dim_stats(results: list[CaseResult]) -> dict[str, dict[str, Any]]:
    dims: dict[str, list[CaseResult]] = {}
    for cr in results:
        dims.setdefault(cr.case.personality, []).append(cr)

    stats: dict[str, dict[str, Any]] = {}
    for dim, cases in sorted(dims.items()):
        scorable = [c for c in cases if not c.all_errors]
        passed = sum(1 for c in scorable if c.majority_pass)
        total = len(scorable)
        accuracy = passed / total if total > 0 else 0.0
        stats[dim] = {
            "cases": len(cases),
            "scorable": total,
            "passed": passed,
            "accuracy": accuracy,
            "errors": len(cases) - total,
        }
    return stats


def print_dim_summary(stats: dict[str, dict[str, Any]]) -> float:
    header = f"{'PERSONALITY':<14} {'CASES':<8} {'PASSED':<8} {'ACCURACY':<10} {'ERRORS'}"
    print(header)
    print("-" * len(header))

    total_scorable = 0
    total_passed = 0
    total_errors = 0
    for dim, s in stats.items():
        acc_str = f"{s['accuracy']:.1%}"
        err_str = str(s["errors"]) if s["errors"] > 0 else ""
        print(f"{dim:<14} {s['scorable']:<8} {s['passed']:<8} {acc_str:<10} {err_str}")
        total_scorable += s["scorable"]
        total_passed += s["passed"]
        total_errors += s["errors"]

    print("-" * len(header))
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    err_str = str(total_errors) if total_errors > 0 else ""
    print(f"{'OVERALL':<14} {total_scorable:<8} {total_passed:<8} {overall_acc:.1%}{'':<5} {err_str}")
    print()

    return overall_acc


def check_gates(
    stats: dict[str, dict[str, Any]],
    threshold: float,
) -> int:
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0

    abs_pass = overall_acc >= threshold
    status = "PASS" if abs_pass else "FAIL"
    print(f"Absolute gate:  {status} ({overall_acc:.1%} {'≥' if abs_pass else '<'} {threshold:.1%})")

    return 0 if abs_pass else 1


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
            "personality_filter": args.personality,
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
                        "turns": [
                            {
                                "turn_idx": tr.turn_idx,
                                "passed": tr.passed,
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
    path = path or _OUTPUT_DIR / "p3-personality_cross_turn-data.json"
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
    path = path or _OUTPUT_DIR / "p3-personality_cross_turn-result.md"
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    total_errors = sum(s["errors"] for s in stats.values())

    gate_status = "PASS" if exit_code == 0 else "FAIL"
    lines: list[str] = []
    w = lines.append

    w(f"# Eval: personality-cross-turn — {gate_status}")
    w("")
    w(f"**Model**: {model_tag}  ")
    w(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"**Runs per case**: {args.runs}  ")
    w(f"**Threshold**: {args.threshold:.0%}  ")
    if args.personality:
        w(f"**Personality filter**: {args.personality}  ")
    if args.case_id:
        w(f"**Case filter**: {args.case_id}  ")
    w(f"**Elapsed**: {elapsed:.1f}s  ")
    w(f"**Overall accuracy**: {overall_acc:.1%} ({total_passed}/{total_scorable})")
    if total_errors > 0:
        w(f"**Transient errors**: {total_errors} case(s) excluded")
    w("")

    w("## Per-Case Results")
    w("")
    w("| Case | Personality | Result | Runs |")
    w("|------|-------------|--------|------|")
    for cr in results:
        pass_str = f"{cr.pass_count}/{len(cr.runs)}"
        w(f"| {cr.case.id} | {cr.case.personality} | **{cr.status}** | {pass_str} |")
    w("")

    w("## Per-Personality Summary")
    w("")
    w("| Personality | Cases | Passed | Accuracy | Errors |")
    w("|-------------|-------|--------|----------|--------|")
    for dim, s in stats.items():
        acc_str = f"{s['accuracy']:.1%}"
        err_str = str(s["errors"]) if s["errors"] > 0 else "-"
        w(f"| {dim} | {s['scorable']} | {s['passed']} | {acc_str} | {err_str} |")
    err_total = str(total_errors) if total_errors > 0 else "-"
    w(f"| **OVERALL** | **{total_scorable}** | **{total_passed}** | **{overall_acc:.1%}** | {err_total} |")
    w("")

    w("## Gates")
    w("")
    abs_pass = overall_acc >= args.threshold
    w(f"- **Absolute gate**: {'PASS' if abs_pass else 'FAIL'} "
      f"({overall_acc:.1%} {'≥' if abs_pass else '<'} {args.threshold:.1%})")
    w("")

    failed = [cr for cr in results if cr.status in ("FAIL", "ERROR")]
    if failed:
        w("## Failed / Error Cases")
        w("")
        for cr in failed:
            w(f"### {cr.case.id} — {cr.status}")
            w(f"- **Personality**: {cr.case.personality}")
            for run_i, r in enumerate(cr.runs, 1):
                if r.error:
                    w(f"- Run {run_i}: ERROR — {r.error_msg}")
                    continue
                run_label = "PASS" if r.passed else "FAIL"
                w(f"- Run {run_i}: {run_label}")
                for tr in r.turn_runs:
                    if not tr.passed:
                        prompt_preview = cr.case.turns[tr.turn_idx][:60]
                        response_preview = (tr.response_text or "")[:200]
                        w(f"  - Turn {tr.turn_idx + 1} FAIL: {', '.join(tr.failed_checks or [])}")
                        w(f"    - Prompt: {prompt_preview!r}")
                        w(f"    - Response: {response_preview!r}")
            w("")

    with open(path, "w") as f:
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

    if args.personality:
        cases = [c for c in cases if c.personality == args.personality]
        print(f"Filtered to {len(cases)} cases (personality={args.personality})")
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
        print(f"Filtered to {len(cases)} case(s) (id={args.case_id})")

    if not cases:
        print("No cases to run.")
        return 0

    agent, model_settings, tool_names = get_agent()
    print(f"Agent created with {len(tool_names)} tools")
    print(f"Running {args.runs} run(s) per case ({len(cases[0].turns)}-turn conversations), "
          f"threshold={args.threshold:.0%}\n")

    t0 = time.monotonic()
    results: list[CaseResult] = []

    # Cache deps per personality
    personality_deps: dict[str, Any] = {}

    for i, case in enumerate(cases, 1):
        print(
            f"[{i}/{len(cases)}] {case.id} ({case.personality}, "
            f"{len(case.turns)} turns) ...",
            end=" ", flush=True,
        )
        cr = CaseResult(case=case)

        if case.personality not in personality_deps:
            personality_deps[case.personality] = make_eval_deps(
                session_id=f"eval-p3-{case.personality}",
                personality=case.personality,
            )
        deps = personality_deps[case.personality]

        for _run_idx in range(args.runs):
            run_result = await run_single(agent, deps, model_settings, case)
            cr.runs.append(run_result)
            if run_result.error:
                print("E", end="", flush=True)
            elif run_result.passed:
                print(".", end="", flush=True)
            else:
                # Show which turns failed (e.g. "x.." = turn 1 failed, 2+3 passed)
                turn_marks = "".join(
                    "." if tr.passed else "x"
                    for tr in run_result.turn_runs
                )
                print(f"[{turn_marks}]", end="", flush=True)

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

    exit_code = check_gates(stats, args.threshold)

    print()
    cases_stem = jsonl_path.stem
    save_path = Path(args.save) if args.save else _OUTPUT_DIR / f"{cases_stem}-data.json"
    report_path = _OUTPUT_DIR / f"{cases_stem}-result.md"
    json_path = save_data_json(results, stats, args, model_tag, path=save_path)
    md_path = save_result_md(results, stats, args, exit_code, elapsed, model_tag, path=report_path)
    print(f"Report saved to {md_path}")
    print(f"Data saved to   {json_path}")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Eval framework for personality cross-turn consistency"
    )
    parser.add_argument(
        "--cases", type=str,
        default=str(Path(__file__).parent / "p3-personality_cross_turn.jsonl"),
        help="Path to eval cases JSONL (default: evals/p3-personality_cross_turn.jsonl)",
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="Runs per case (odd recommended for majority vote, default: 3)",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.80,
        help="Absolute pass rate gate 0.0-1.0 (default: 0.80)",
    )
    parser.add_argument(
        "--personality", type=str, default=None,
        help="Filter to a single personality (terse, finch, friendly, jeff, inquisitive)",
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
        help="Save results JSON to file",
    )

    args = parser.parse_args()
    return asyncio.run(run_eval(args))


if __name__ == "__main__":
    sys.exit(main())
