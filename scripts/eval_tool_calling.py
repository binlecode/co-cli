"""Eval framework for tool-calling quality.

Runs golden JSONL cases through the real agent, extracts tool calls,
and scores them against expected values. Supports majority-vote scoring,
absolute/relative gates, model tagging, and multi-baseline comparison.

Prerequisites:
  - LLM provider configured (gemini_api_key or ollama running)
  - Set LLM_PROVIDER env var if not using the default (gemini)

Usage:
    uv run python scripts/eval_tool_calling.py
    uv run python scripts/eval_tool_calling.py --runs 5 --threshold 0.90
    uv run python scripts/eval_tool_calling.py --model-tag ollama-q4 --save evals/baseline-q4.json
    uv run python scripts/eval_tool_calling.py --compare evals/baseline-gemini.json evals/baseline-q8.json
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
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend


# ---------------------------------------------------------------------------
# Model tag detection
# ---------------------------------------------------------------------------

def detect_model_tag() -> str:
    """Auto-detect a model tag from the current LLM config."""
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        return f"gemini-{settings.gemini_model}"
    if provider == "ollama":
        return f"ollama-{settings.ollama_model}"
    return provider


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    id: str
    dim: str
    prompt: str
    expect_tool: str | None
    expect_args: dict[str, Any] | None
    arg_match: str | None  # "exact" | "subset" | None


@dataclass
class RunResult:
    """Result of a single run of a single case."""
    passed: bool
    error: bool  # transient error (excluded from vote)
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    error_msg: str | None = None
    recovered: bool | None = None  # error_recovery dim: model returned text after failure


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
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(EvalCase(
                id=raw["id"],
                dim=raw["dim"],
                prompt=raw["prompt"],
                expect_tool=raw.get("expect_tool"),
                expect_args=raw.get("expect_args"),
                arg_match=raw.get("arg_match"),
            ))
    return cases


# ---------------------------------------------------------------------------
# Tool call extraction
# ---------------------------------------------------------------------------

def extract_first_tool_call(messages: list[Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Extract the first ToolCallPart from agent messages."""
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                return part.tool_name, part.args_as_dict()
    return None, None


def extract_deferred_tool_call(
    output: DeferredToolRequests,
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract the first tool call from deferred approval requests."""
    for call in output.approvals:
        args = call.args
        if isinstance(args, str):
            args = json.loads(args)
        args = args or {}
        return call.tool_name, args
    return None, None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def check_args(
    actual: dict[str, Any] | None,
    expected: dict[str, Any] | None,
    mode: str | None,
) -> bool:
    """Check argument matching."""
    if mode is None or expected is None:
        return True  # no arg check requested
    actual = actual or {}
    if mode == "exact":
        return actual == expected
    if mode == "subset":
        return all(
            k in actual and actual[k] == v
            for k, v in expected.items()
        )
    return False


def score_run(
    case: EvalCase,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
    recovered: bool | None = None,
) -> bool:
    """Score a single run against the case expectations."""
    if case.dim == "refusal":
        # Pass if no tool was called
        return tool_name is None

    if case.dim == "error_recovery":
        # Pass if: 1) right tool was selected first, 2) model recovered gracefully
        return tool_name == case.expect_tool and recovered is True

    # tool_selection and arg_extraction both require correct tool
    if tool_name != case.expect_tool:
        return False

    if case.dim == "arg_extraction":
        return check_args(tool_args, case.expect_args, case.arg_match)

    # tool_selection — tool name match is sufficient
    return True


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
    deps: CoDeps,
    model_settings: ModelSettings | None,
    case: EvalCase,
) -> RunResult:
    """Run a single case once and return the result."""
    # Override temperature to 0 for determinism
    if model_settings:
        # model_settings may be a dict or ModelSettings — normalise to dict
        base = dict(model_settings) if isinstance(model_settings, dict) else {
            "temperature": model_settings.temperature,
            "top_p": model_settings.top_p,
            "max_tokens": model_settings.max_tokens,
        }
        base["temperature"] = 0
        eval_settings = ModelSettings(**base)
    else:
        eval_settings = ModelSettings(temperature=0)

    try:
        # request_limit=2: model gets one shot to pick a tool. With all_approval=True
        # no tool executes, so no ModelRetry loops — one request is enough.
        result = await agent.run(
            case.prompt,
            deps=deps,
            model_settings=eval_settings,
            usage_limits=UsageLimits(request_limit=2),
        )

        # With all_approval=True, any tool call returns DeferredToolRequests.
        # A pure-text response (refusal) returns str.
        if isinstance(result.output, DeferredToolRequests):
            tool_name, tool_args = extract_deferred_tool_call(result.output)
        else:
            # No tool was called — model returned text only
            tool_name, tool_args = None, None

        passed = score_run(case, tool_name, tool_args)
        return RunResult(passed=passed, error=False, tool_name=tool_name, tool_args=tool_args)

    except Exception as exc:
        if is_transient_error(exc):
            return RunResult(passed=False, error=True, error_msg=f"transient: {exc}")
        # Non-transient errors still count as failures
        return RunResult(passed=False, error=False, error_msg=str(exc))


async def run_single_recovery(
    agent: Any,
    deps: CoDeps,
    model_settings: ModelSettings | None,
    case: EvalCase,
) -> RunResult:
    """Run an error_recovery case: tools execute normally (and fail on missing creds).

    Pass criteria:
      1. Model picked the right tool on first attempt (ToolCallPart)
      2. After ModelRetry, model recovered gracefully (returned text, didn't loop)

    Uses normal agent (no all_approval) with request_limit=5:
      - Request 1: model picks tool → tool executes → returns error dict or ModelRetry
      - Request 2: model sees error → should return text to user
      - Requests 3-5: budget for recovery if model tries alternative tools
    """
    if model_settings:
        base = dict(model_settings) if isinstance(model_settings, dict) else {
            "temperature": model_settings.temperature,
            "top_p": model_settings.top_p,
            "max_tokens": model_settings.max_tokens,
        }
        base["temperature"] = 0
        eval_settings = ModelSettings(**base)
    else:
        eval_settings = ModelSettings(temperature=0)

    try:
        result = await agent.run(
            case.prompt,
            deps=deps,
            model_settings=eval_settings,
            usage_limits=UsageLimits(request_limit=5),
        )

        # Extract the FIRST tool call from message history (before retries)
        tool_name, tool_args = extract_first_tool_call(result.all_messages())

        # Recovery check: model returned text (str), not stuck in tool loop
        recovered = isinstance(result.output, str)

        passed = score_run(case, tool_name, tool_args, recovered=recovered)
        return RunResult(
            passed=passed, error=False,
            tool_name=tool_name, tool_args=tool_args, recovered=recovered,
        )

    except Exception as exc:
        exc_msg = str(exc).lower()
        if is_transient_error(exc):
            return RunResult(passed=False, error=True, error_msg=f"transient: {exc}")

        # request_limit exceeded = model looped instead of recovering
        if "request_limit" in exc_msg or "would exceed" in exc_msg:
            return RunResult(
                passed=False, error=False, recovered=False,
                error_msg=f"model looped: {exc}",
            )

        return RunResult(passed=False, error=False, error_msg=str(exc))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_case_table(results: list[CaseResult]) -> None:
    header = f"{'CASE':<20} {'DIM':<18} {'TOOL EXPECTED':<26} {'RESULT':<8} {'RUNS'}"
    print(header)
    print("-" * len(header))
    for cr in results:
        tool = cr.case.expect_tool or "(none)"
        valid = cr.valid_runs
        total = len(cr.runs)
        pass_str = f"{cr.pass_count}/{total}"
        print(f"{cr.case.id:<20} {cr.case.dim:<18} {tool:<26} {cr.status:<8} {pass_str}")
    print()


def compute_dim_stats(results: list[CaseResult]) -> dict[str, dict[str, Any]]:
    """Compute per-dimension stats, excluding ERROR cases from gate calculations."""
    dims: dict[str, list[CaseResult]] = {}
    for cr in results:
        dims.setdefault(cr.case.dim, []).append(cr)

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


def print_dim_summary(stats: dict[str, dict[str, Any]]) -> None:
    header = f"{'DIMENSION':<20} {'CASES':<8} {'PASSED':<8} {'ACCURACY':<10} {'ERRORS'}"
    print(header)
    print("-" * len(header))

    total_scorable = 0
    total_passed = 0
    total_errors = 0
    for dim, s in stats.items():
        acc_str = f"{s['accuracy']:.1%}"
        err_str = str(s["errors"]) if s["errors"] > 0 else ""
        print(f"{dim:<20} {s['scorable']:<8} {s['passed']:<8} {acc_str:<10} {err_str}")
        total_scorable += s["scorable"]
        total_passed += s["passed"]
        total_errors += s["errors"]

    print("-" * len(header))
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    err_str = str(total_errors) if total_errors > 0 else ""
    print(f"{'OVERALL':<20} {total_scorable:<8} {total_passed:<8} {overall_acc:.1%}{'':<5} {err_str}")
    print()

    return overall_acc


def check_gates(
    stats: dict[str, dict[str, Any]],
    threshold: float,
    baselines: list[dict[str, Any]] | None,
    max_degradation: float,
) -> int:
    """Check absolute and relative gates. Returns exit code."""
    # Overall accuracy
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0

    # Absolute gate
    abs_pass = overall_acc >= threshold
    status = "PASS" if abs_pass else "FAIL"
    print(f"Absolute gate:  {status} ({overall_acc:.1%} {'≥' if abs_pass else '<'} {threshold:.1%})")

    if not abs_pass:
        return 1

    # Relative gate — check against every baseline
    if baselines:
        rel_pass = True
        for baseline in baselines:
            bl_model = baseline.get("model", "baseline")
            baseline_stats = baseline.get("dim_stats", {})

            for dim, current in stats.items():
                prev = baseline_stats.get(dim, {})
                prev_acc = prev.get("accuracy", 0.0)
                drop = prev_acc - current["accuracy"]
                if drop > max_degradation:
                    print(
                        f"Relative gate:  FAIL ({dim} dropped {drop:.1%} > "
                        f"{max_degradation:.1%} max vs {bl_model})"
                    )
                    rel_pass = False

        if not rel_pass:
            return 2
        print(f"Relative gate:  PASS (no dimension dropped > {max_degradation:.1%})")

    return 0


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

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
            "dim_filter": args.dim,
            "case_id_filter": args.case_id,
        },
        "overall_accuracy": overall_acc,
        "dim_stats": stats,
        "cases": [
            {
                "id": cr.case.id,
                "dim": cr.case.dim,
                "expect_tool": cr.case.expect_tool,
                "status": cr.status,
                "pass_count": cr.pass_count,
                "total_runs": len(cr.runs),
                "valid_runs": len(cr.valid_runs),
                "runs": [
                    {
                        "passed": r.passed,
                        "error": r.error,
                        "tool_name": r.tool_name,
                        "tool_args": r.tool_args,
                        "error_msg": r.error_msg,
                        "recovered": r.recovered,
                    }
                    for r in cr.runs
                ],
            }
            for cr in results
        ],
    }


# Auto-save output dir: evals/ next to the JSONL
_OUTPUT_DIR = Path(__file__).parent.parent / "evals"


def save_data_json(
    results: list[CaseResult],
    stats: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    model_tag: str,
    path: Path | None = None,
) -> Path:
    """Save detailed results JSON for --compare and re-review."""
    path = path or _OUTPUT_DIR / "eval_tool_calling-data.json"
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
    baselines: list[dict[str, Any]] | None = None,
    path: Path | None = None,
) -> Path:
    """Save a human-readable markdown report."""
    path = path or _OUTPUT_DIR / "eval_tool_calling-result.md"
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0
    total_errors = sum(s["errors"] for s in stats.values())

    gate_status = "PASS" if exit_code == 0 else "FAIL"
    lines: list[str] = []
    w = lines.append

    w(f"# Eval: tool-calling — {gate_status}")
    w("")
    w(f"**Model**: {model_tag}  ")
    w(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"**Runs per case**: {args.runs}  ")
    w(f"**Threshold**: {args.threshold:.0%}  ")
    if args.dim:
        w(f"**Dimension filter**: {args.dim}  ")
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
    w("| Case | Dim | Expected Tool | Result | Runs |")
    w("|------|-----|---------------|--------|------|")
    for cr in results:
        tool = cr.case.expect_tool or "(none)"
        pass_str = f"{cr.pass_count}/{len(cr.runs)}"
        w(f"| {cr.case.id} | {cr.case.dim} | `{tool}` | **{cr.status}** | {pass_str} |")
    w("")

    # Per-dimension summary
    w("## Per-Dimension Summary")
    w("")
    w("| Dimension | Cases | Passed | Accuracy | Errors |")
    w("|-----------|-------|--------|----------|--------|")
    for dim, s in stats.items():
        acc_str = f"{s['accuracy']:.1%}"
        err_str = str(s["errors"]) if s["errors"] > 0 else "-"
        w(f"| {dim} | {s['scorable']} | {s['passed']} | {acc_str} | {err_str} |")
    err_total = str(total_errors) if total_errors > 0 else "-"
    w(f"| **OVERALL** | **{total_scorable}** | **{total_passed}** | **{overall_acc:.1%}** | {err_total} |")
    w("")

    # Gate verdicts
    w("## Gates")
    w("")
    abs_pass = overall_acc >= args.threshold
    w(f"- **Absolute gate**: {'PASS' if abs_pass else 'FAIL'} "
      f"({overall_acc:.1%} {'≥' if abs_pass else '<'} {args.threshold:.1%})")
    if args.compare:
        if exit_code == 2:
            w(f"- **Relative gate**: FAIL (degradation exceeded {args.max_degradation:.1%})")
        else:
            w(f"- **Relative gate**: PASS (no dimension dropped > {args.max_degradation:.1%})")
    w("")

    # Model comparison
    if baselines:
        _write_model_comparison(w, stats, overall_acc, model_tag, baselines, results)

    # Failed cases detail
    failed = [cr for cr in results if cr.status in ("FAIL", "ERROR")]
    if failed:
        w("## Failed / Error Cases")
        w("")
        for cr in failed:
            w(f"### {cr.case.id} — {cr.status}")
            w(f"- **Prompt**: {cr.case.prompt!r}")
            w(f"- **Expected tool**: `{cr.case.expect_tool}`")
            if cr.case.expect_args:
                w(f"- **Expected args**: `{json.dumps(cr.case.expect_args)}`")
            for i, r in enumerate(cr.runs, 1):
                if r.error:
                    w(f"- Run {i}: ERROR — {r.error_msg}")
                else:
                    detail = f"tool=`{r.tool_name}`, args=`{json.dumps(r.tool_args)}`"
                    if r.recovered is not None:
                        detail += f", recovered={r.recovered}"
                    w(f"- Run {i}: {'PASS' if r.passed else 'FAIL'} — {detail}")
            w("")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def _write_model_comparison(
    w,
    stats: dict[str, dict[str, Any]],
    overall_acc: float,
    model_tag: str,
    baselines: list[dict[str, Any]],
    results: list[CaseResult],
) -> None:
    """Write model comparison section to the markdown report.

    Single baseline: two-column Baseline vs Current table.
    Multiple baselines: full model comparison matrix.
    """
    # Collect all dimensions across current + baselines
    all_dims = list(stats.keys())

    if len(baselines) == 1:
        # Single baseline — compact two-column view
        bl = baselines[0]
        bl_stats = bl.get("dim_stats", {})
        bl_overall = bl.get("overall_accuracy", 0.0)
        bl_model = bl.get("model", "baseline")
        bl_ts = bl.get("timestamp", "unknown")

        w("## Model Comparison vs Baseline")
        w("")
        w(f"Baseline model: **{bl_model}** (saved {bl_ts})  ")
        w(f"Current model: **{model_tag}**")
        w("")
        w("| Dimension | Baseline | Current | Delta |")
        w("|-----------|----------|---------|-------|")
        for dim in all_dims:
            bl_dim = bl_stats.get(dim, {})
            bl_acc = bl_dim.get("accuracy", 0.0)
            cur_acc = stats[dim]["accuracy"]
            delta = cur_acc - bl_acc
            sign = "+" if delta > 0 else ""
            w(f"| {dim} | {bl_acc:.1%} | {cur_acc:.1%} | {sign}{delta:.1%} |")
        delta_overall = overall_acc - bl_overall
        sign = "+" if delta_overall > 0 else ""
        w(f"| **OVERALL** | **{bl_overall:.1%}** | **{overall_acc:.1%}** | **{sign}{delta_overall:.1%}** |")
        w("")

    else:
        # Multiple baselines — full matrix
        w("## Model Comparison")
        w("")

        # Build rows: each model is a row, each dimension + OVERALL is a column
        # Collect model entries: [{model, dim_stats, overall_accuracy}, ...]
        model_rows: list[dict[str, Any]] = []
        for bl in baselines:
            model_rows.append({
                "model": bl.get("model", "unknown"),
                "dim_stats": bl.get("dim_stats", {}),
                "overall": bl.get("overall_accuracy", 0.0),
            })
        # Current run is the last row
        model_rows.append({
            "model": f"{model_tag} (current)",
            "dim_stats": stats,
            "overall": overall_acc,
        })

        # Header
        dim_headers = "".join(f" {d} |" for d in all_dims)
        w(f"| Model |{dim_headers} OVERALL |")
        sep = "".join(f"{'---':>8}|" for _ in all_dims)
        w(f"|-------|{sep}---------|")

        for row in model_rows:
            cells = ""
            for dim in all_dims:
                ds = row["dim_stats"].get(dim, {})
                acc = ds.get("accuracy", 0.0)
                passed = ds.get("passed", 0)
                scorable = ds.get("scorable", 0)
                cells += f" {acc:.1%} ({passed}/{scorable}) |"
            ov = row["overall"]
            w(f"| {row['model']} |{cells} **{ov:.1%}** |")
        w("")

    # Per-case regressions/improvements vs first baseline
    bl = baselines[0]
    bl_cases = {c["id"]: c for c in bl.get("cases", [])}
    regressions: list[str] = []
    improvements: list[str] = []
    for cr in results:
        bl_case = bl_cases.get(cr.case.id)
        if not bl_case:
            continue
        bl_status = bl_case.get("status", "PASS")
        if cr.status != bl_status:
            if cr.status == "FAIL" and bl_status == "PASS":
                regressions.append(cr.case.id)
            elif cr.status == "PASS" and bl_status == "FAIL":
                improvements.append(cr.case.id)

    if regressions:
        w(f"**Regressions** (PASS → FAIL): {', '.join(f'`{r}`' for r in regressions)}  ")
    if improvements:
        w(f"**Improvements** (FAIL → PASS): {', '.join(f'`{r}`' for r in improvements)}  ")
    if not regressions and not improvements:
        w("No case status changes vs baseline.")
    w("")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_eval(args: argparse.Namespace) -> int:
    # Resolve model tag
    model_tag = args.model_tag or detect_model_tag()

    # Load cases
    jsonl_path = Path(__file__).parent.parent / "evals" / "tool_calling.jsonl"
    cases = load_cases(jsonl_path)
    print(f"Loaded {len(cases)} eval cases from {jsonl_path}")
    print(f"Model: {model_tag}\n")

    # Filter
    if args.dim:
        cases = [c for c in cases if c.dim == args.dim]
        print(f"Filtered to {len(cases)} cases (dim={args.dim})")
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
        print(f"Filtered to {len(cases)} case(s) (id={args.case_id})")

    if not cases:
        print("No cases to run.")
        return 0

    # Two agents: deferred (selection/args/refusal) and normal (error_recovery)
    has_recovery = any(c.dim == "error_recovery" for c in cases)
    has_selection = any(c.dim != "error_recovery" for c in cases)

    deps = CoDeps(
        sandbox=SubprocessBackend(),
        obsidian_vault_path=None,
        google_credentials_path=None,
        slack_client=None,
        shell_safe_commands=[],
    )

    # Deferred agent: all tools return DeferredToolRequests without executing
    agent_deferred = None
    model_settings = None
    tool_names: list[str] = []
    if has_selection:
        agent_deferred, model_settings, tool_names = get_agent(all_approval=True)

    # Normal agent: tools execute (and fail on missing creds) for recovery testing
    agent_normal = None
    if has_recovery:
        agent_normal, model_settings, tool_names = get_agent()

    tool_count = len(tool_names)
    print(f"Agent created with {tool_count} tools")
    if has_recovery:
        print(f"  error_recovery cases use normal agent (tools execute)")
    print(f"Running {args.runs} run(s) per case, threshold={args.threshold:.0%}\n")

    # Run cases
    t0 = time.monotonic()
    results: list[CaseResult] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case.id} ({case.dim}) ...", end=" ", flush=True)
        cr = CaseResult(case=case)

        for run_idx in range(args.runs):
            if case.dim == "error_recovery":
                run_result = await run_single_recovery(
                    agent_normal, deps, model_settings, case,
                )
            else:
                run_result = await run_single(
                    agent_deferred, deps, model_settings, case,
                )
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

    # Load baselines: explicit --compare paths, else auto-discover evals/baseline-*.json
    baselines: list[dict[str, Any]] | None = None
    bl_paths: list[str] = args.compare or sorted(
        str(p) for p in _OUTPUT_DIR.glob("baseline-*.json")
    )
    if bl_paths:
        baselines = []
        for bl_path in bl_paths:
            with open(bl_path) as f:
                baselines.append(json.load(f))
        source = "explicit" if args.compare else "auto-discovered"
        print(f"Baselines ({source}): {', '.join(bl_paths)}")

    # Gates
    exit_code = check_gates(stats, args.threshold, baselines, args.max_degradation)

    # Print model comparison matrix to terminal (when baselines exist)
    if baselines:
        print()
        _print_model_comparison(stats, model_tag, baselines)

    # Auto-save markdown report + JSON data
    print()
    save_path = Path(args.save) if args.save else None
    json_path = save_data_json(results, stats, args, model_tag, path=save_path)
    md_path = save_result_md(
        results, stats, args, exit_code, elapsed, model_tag,
        baselines=baselines,
    )
    print(f"Report saved to {md_path}")
    print(f"Data saved to   {json_path}")

    return exit_code


def _print_model_comparison(
    stats: dict[str, dict[str, Any]],
    model_tag: str,
    baselines: list[dict[str, Any]],
) -> None:
    """Print model comparison matrix to the terminal."""
    total_scorable = sum(s["scorable"] for s in stats.values())
    total_passed = sum(s["passed"] for s in stats.values())
    overall_acc = total_passed / total_scorable if total_scorable > 0 else 0.0

    all_dims = list(stats.keys())

    # Build rows
    rows: list[tuple[str, dict[str, float], float]] = []
    for bl in baselines:
        bl_model = bl.get("model", "unknown")
        bl_dim_accs = {}
        for dim in all_dims:
            ds = bl.get("dim_stats", {}).get(dim, {})
            bl_dim_accs[dim] = ds.get("accuracy", 0.0)
        rows.append((bl_model, bl_dim_accs, bl.get("overall_accuracy", 0.0)))
    # Current run
    cur_dim_accs = {dim: stats[dim]["accuracy"] for dim in all_dims}
    rows.append((f"{model_tag} (current)", cur_dim_accs, overall_acc))

    # Print
    print("MODEL COMPARISON")
    col_w = 18
    model_w = max(len(r[0]) for r in rows) + 2
    header = f"{'MODEL':<{model_w}}" + "".join(f"{d:<{col_w}}" for d in all_dims) + "OVERALL"
    print(header)
    print("-" * len(header))
    for name, dim_accs, overall in rows:
        cells = "".join(f"{dim_accs.get(d, 0.0):.1%}{'':<{col_w - 5}}" for d in all_dims)
        print(f"{name:<{model_w}}{cells}{overall:.1%}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Eval framework for tool-calling quality"
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
        "--dim", type=str, default=None,
        help="Filter to a single dimension (tool_selection, arg_extraction, refusal, error_recovery)",
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
        help="Save results JSON to file (for later --compare)",
    )
    parser.add_argument(
        "--compare", type=str, nargs="+", default=None,
        help="Override baseline JSONs to compare against (default: auto-discover evals/baseline-*.json)",
    )
    parser.add_argument(
        "--max-degradation", type=float, default=0.10,
        help="Max allowed per-dimension accuracy drop vs baseline (default: 0.10)",
    )

    args = parser.parse_args()
    return asyncio.run(run_eval(args))


if __name__ == "__main__":
    sys.exit(main())
