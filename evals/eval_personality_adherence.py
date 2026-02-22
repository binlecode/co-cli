"""Eval: personality-adherence — verify personality output characteristics.

Runs golden JSONL cases through the real agent with personality-specific deps,
scores the agent's natural language response against heuristic checks
(sentence count, forbidden/required phrases, preamble detection).

Target flow:   agent.run() → extract text output → score against checks
Critical impact: personality adherence is the differentiator for co's
                 companion UX — each role must produce measurably distinct output.

Dimensions:    terse, finch, friendly, jeff, inquisitive (one per personality)

Prerequisites: LLM provider configured (ollama running, or gemini_api_key set).
               Set LLM_PROVIDER env var if not using the default.

Usage:
    uv run python evals/eval_personality_adherence.py
    uv run python evals/eval_personality_adherence.py --personality terse
    uv run python evals/eval_personality_adherence.py --case-id p2-terse-brevity-tcp
    uv run python evals/eval_personality_adherence.py --runs 5 --threshold 0.70
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
    prompt: str
    checks: list[dict[str, Any]]


@dataclass
class RunResult:
    """Result of a single run of a single case."""
    passed: bool
    error: bool  # transient error (excluded from vote)
    response_text: str | None = None
    failed_checks: list[str] | None = None
    error_msg: str | None = None


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
                prompt=raw["prompt"],
                checks=raw["checks"],
            ))
    return cases


# ---------------------------------------------------------------------------
# Sentence counting
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r'[.!?]+(?:\s|$)')


def count_sentences(text: str) -> int:
    """Count sentences in text.

    Splits on sentence-ending punctuation (.!?) followed by whitespace or
    end-of-string. Ignores empty fragments. Treats bullet/fragment lists
    where each line lacks sentence-ending punctuation as 1 sentence per line.
    """
    # Strip code blocks — they shouldn't count as sentences
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)

    # If text has sentence-ending punctuation, split on it
    if _SENTENCE_END.search(text):
        parts = _SENTENCE_END.split(text)
        return sum(1 for p in parts if p.strip())

    # Fallback: count non-empty lines (for fragment-style responses)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return max(len(lines), 1) if lines else 0


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_max_sentences(text: str, params: dict[str, Any]) -> str | None:
    """Pass if response has <= n sentences."""
    n = params["n"]
    actual = count_sentences(text)
    if actual <= n:
        return None
    return f"max_sentences: got {actual}, expected <= {n}"


def check_min_sentences(text: str, params: dict[str, Any]) -> str | None:
    """Pass if response has >= n sentences."""
    n = params["n"]
    actual = count_sentences(text)
    if actual >= n:
        return None
    return f"min_sentences: got {actual}, expected >= {n}"


def check_forbidden(text: str, params: dict[str, Any]) -> str | None:
    """Pass if response contains none of the phrases (case-insensitive)."""
    # Strip inline markdown emphasis (* and _) so formatted text like
    # "not *always* wrong" doesn't bypass forbidden checks on "always".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return f"forbidden: found '{phrase}'"
    return None


def check_required_any(text: str, params: dict[str, Any]) -> str | None:
    """Pass if response contains at least one phrase (case-insensitive)."""
    # Strip inline markdown emphasis (* and _) before matching so
    # "not *always* wrong" matches the phrase "not always wrong".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return None
    return f"required_any: none of {params['phrases']} found"


def check_no_preamble(text: str, params: dict[str, Any]) -> str | None:
    """Pass if response does not start with any phrase (case-insensitive)."""
    stripped = text.strip().lower()
    for phrase in params["phrases"]:
        if stripped.startswith(phrase.lower()):
            return f"no_preamble: starts with '{phrase}'"
    return None


def check_has_question(text: str, params: dict[str, Any]) -> str | None:
    """Pass if response contains a question mark."""
    if "?" in text:
        return None
    return "has_question: no '?' found"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

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
# Single case runner
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


async def run_single(
    agent: Any,
    deps: Any,
    model_settings: ModelSettings | None,
    case: EvalCase,
) -> RunResult:
    """Run a single case once and return the result."""
    # max_tokens=2048: personality checks are surface-level (sentence count, phrase
    # presence). qwen3 thinking chains can reach 32K tokens; capping at 2048 limits
    # the thinking budget to ~1500 tokens and keeps each case under ~60s locally.
    eval_settings = make_eval_settings(model_settings, max_tokens=2048)

    try:
        # request_limit=2: one shot for text response
        result = await agent.run(
            case.prompt,
            deps=deps,
            model_settings=eval_settings,
            usage_limits=UsageLimits(request_limit=2),
        )

        # If agent tried to call a tool, this case fails
        # (prompts should elicit text-only responses)
        if isinstance(result.output, DeferredToolRequests):
            return RunResult(
                passed=False,
                error=False,
                response_text=None,
                failed_checks=["agent returned tool calls instead of text"],
            )

        text = str(result.output)
        failures = score_response(text, case.checks)
        return RunResult(
            passed=len(failures) == 0,
            error=False,
            response_text=text,
            failed_checks=failures if failures else None,
        )

    except Exception as exc:
        if is_transient_error(exc):
            return RunResult(passed=False, error=True, error_msg=f"transient: {exc}")
        return RunResult(passed=False, error=False, error_msg=str(exc))


# ---------------------------------------------------------------------------
# Report
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
    """Check absolute gate. Returns exit code."""
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0

    abs_pass = overall_acc >= threshold
    status = "PASS" if abs_pass else "FAIL"
    print(f"Absolute gate:  {status} ({overall_acc:.1%} {'≥' if abs_pass else '<'} {threshold:.1%})")

    if not abs_pass:
        return 1
    return 0


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
    """Build the structured result data used by both JSON and MD outputs."""
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
                "status": cr.status,
                "pass_count": cr.pass_count,
                "total_runs": len(cr.runs),
                "valid_runs": len(cr.valid_runs),
                "runs": [
                    {
                        "passed": r.passed,
                        "error": r.error,
                        "response_text": r.response_text,
                        "failed_checks": r.failed_checks,
                        "error_msg": r.error_msg,
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
    """Save detailed results JSON."""
    path = path or _OUTPUT_DIR / "p2-personality_adherence-data.json"
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
    """Save a human-readable markdown report."""
    path = path or _OUTPUT_DIR / "p2-personality_adherence-result.md"
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    total_errors = sum(s["errors"] for s in stats.values())

    gate_status = "PASS" if exit_code == 0 else "FAIL"
    lines: list[str] = []
    w = lines.append

    w(f"# Eval: personality-adherence — {gate_status}")
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

    # Per-case table
    w("## Per-Case Results")
    w("")
    w("| Case | Personality | Result | Runs |")
    w("|------|-------------|--------|------|")
    for cr in results:
        pass_str = f"{cr.pass_count}/{len(cr.runs)}"
        w(f"| {cr.case.id} | {cr.case.personality} | **{cr.status}** | {pass_str} |")
    w("")

    # Per-personality summary
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

    # Gate verdict
    w("## Gates")
    w("")
    abs_pass = overall_acc >= args.threshold
    w(f"- **Absolute gate**: {'PASS' if abs_pass else 'FAIL'} "
      f"({overall_acc:.1%} {'≥' if abs_pass else '<'} {args.threshold:.1%})")
    w("")

    # Failed cases detail
    failed = [cr for cr in results if cr.status in ("FAIL", "ERROR")]
    if failed:
        w("## Failed / Error Cases")
        w("")
        for cr in failed:
            w(f"### {cr.case.id} — {cr.status}")
            w(f"- **Personality**: {cr.case.personality}")
            w(f"- **Prompt**: {cr.case.prompt!r}")
            w(f"- **Checks**: {json.dumps(cr.case.checks)}")
            for i, r in enumerate(cr.runs, 1):
                if r.error:
                    w(f"- Run {i}: ERROR — {r.error_msg}")
                elif r.failed_checks:
                    preview = (r.response_text or "")[:200]
                    w(f"- Run {i}: FAIL — {', '.join(r.failed_checks)}")
                    w(f"  - Response preview: {preview!r}")
                else:
                    w(f"- Run {i}: PASS")
            w("")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_eval(args: argparse.Namespace) -> int:
    model_tag = args.model_tag or detect_model_tag()

    # Load cases
    jsonl_path = Path(args.cases)
    cases = load_cases(jsonl_path)
    print(f"Loaded {len(cases)} eval cases from {jsonl_path}")
    print(f"Model: {model_tag}\n")

    # Filter
    if args.personality:
        cases = [c for c in cases if c.personality == args.personality]
        print(f"Filtered to {len(cases)} cases (personality={args.personality})")
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
        print(f"Filtered to {len(cases)} case(s) (id={args.case_id})")

    if not cases:
        print("No cases to run.")
        return 0

    # Create the agent (no all_approval — we want text responses, not deferred tool calls)
    agent, model_settings, tool_names = get_agent()
    tool_count = len(tool_names)
    print(f"Agent created with {tool_count} tools")
    print(f"Running {args.runs} run(s) per case, threshold={args.threshold:.0%}\n")

    # Run cases
    t0 = time.monotonic()
    results: list[CaseResult] = []

    # Group cases by personality to reuse deps
    personality_deps: dict[str, Any] = {}

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case.id} ({case.personality}) ...", end=" ", flush=True)
        cr = CaseResult(case=case)

        # Build personality-specific deps (cached per personality)
        if case.personality not in personality_deps:
            personality_deps[case.personality] = make_eval_deps(
                session_id=f"eval-p2-{case.personality}",
                personality=case.personality,
            )
        deps = personality_deps[case.personality]

        for run_idx in range(args.runs):
            run_result = await run_single(agent, deps, model_settings, case)
            cr.runs.append(run_result)
            if run_result.error:
                print("E", end="", flush=True)
            elif run_result.passed:
                print(".", end="", flush=True)
            else:
                print("x", end="", flush=True)

        print(f" {cr.status}")
        results.append(cr)

    elapsed = time.monotonic() - t0
    print()

    # Report
    print("=" * 60)
    print(f"RESULTS — {model_tag}")
    print("=" * 60)
    print()

    print_case_table(results)

    stats = compute_dim_stats(results)
    print_dim_summary(stats)

    # Gates
    exit_code = check_gates(stats, args.threshold)

    # Auto-save markdown report + JSON data
    print()
    cases_stem = jsonl_path.stem
    save_path = Path(args.save) if args.save else _OUTPUT_DIR / f"{cases_stem}-data.json"
    report_path = _OUTPUT_DIR / f"{cases_stem}-result.md"
    json_path = save_data_json(results, stats, args, model_tag, path=save_path)
    md_path = save_result_md(
        results, stats, args, exit_code, elapsed, model_tag,
        path=report_path,
    )
    print(f"Report saved to {md_path}")
    print(f"Data saved to   {json_path}")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Eval framework for personality adherence quality"
    )
    parser.add_argument(
        "--cases", type=str,
        default=str(Path(__file__).parent / "p2-personality_adherence.jsonl"),
        help="Path to eval cases JSONL (default: evals/p2-personality_adherence.jsonl)",
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
        help="Label for this run (e.g. 'ollama-q4'). Auto-detected from config if omitted",
    )
    parser.add_argument(
        "--save", type=str, default=None,
        help="Save results JSON to file",
    )
    parser.add_argument(
        "--compare", type=str, nargs="+", default=None,
        help="Baseline JSONs to compare against (not yet implemented for P2)",
    )

    args = parser.parse_args()
    return asyncio.run(run_eval(args))


if __name__ == "__main__":
    sys.exit(main())
