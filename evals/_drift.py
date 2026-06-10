"""Cross-run stability / drift aggregator for behavioral-eval REPORTs (plan T-9, dim 1).

Reads the last K ``## Run <ISO8601>`` sections from
``docs/REPORT-eval-<scenario>.md``, and per case diffs ``(verdict, judge_score)``
across runs: a verdict-flip count and the mean judge-score delta. Emits a drift
table plus an aggregate verdict — SOFT_FAIL when more than ``FLIP_PCT`` of cases
flip verdict across the window, or any case's score regresses beyond
``SCORE_DELTA``. This is the deferred drift-tracker from ``uat_evals.md
§Coverage gaps``, made concrete.

The aggregator CODE is loop-agnostic and may run anytime; the baseline HISTORY
it diffs against must be seeded only from post-compaction-fix runs (post
v0.8.327) — a pre-fix baseline would flag the intended improvement as a
regression (see the plan's Cross-plan Sequencing).

Thresholds (plan Open Q3 starting values; tune once real history exists):
- ``K`` = 5 most-recent runs
- ``FLIP_PCT`` = 0.20 (>20% of cases flipping → SOFT_FAIL)
- ``SCORE_DELTA`` = 2 (a judge-score regression beyond this → SOFT_FAIL)

Run manually:
    uv run python evals/_drift.py agentic_loop
    uv run python evals/_drift.py eval_agentic_loop   # eval_ prefix tolerated
    uv run python evals/_drift.py                      # all REPORTs on disk
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

_REPORTS_DIR = Path(__file__).parent.parent / "docs"

K = 5
FLIP_PCT = 0.20
SCORE_DELTA = 2

_RUN_SPLIT_RE = re.compile(r"^## Run ", re.MULTILINE)
_JUDGE_SCORE_RE = re.compile(r"judge\.score=(\d+)")
_ROW_RE = re.compile(r"^\|\s*(?P<case>[^|]+?)\s*\|\s*(?P<verdict>[^|]+?)\s*\|")

_VERDICT_TOKENS = {"PASS", "FAIL", "SOFT_PASS", "SOFT_FAIL"}


@dataclass(frozen=True)
class CaseObservation:
    """One case's outcome in one run."""

    verdict: str
    judge_score: int | None


def scenario_to_report_path(scenario: str) -> Path:
    """Map a scenario name to ``docs/REPORT-eval-<scenario>.md``.

    Tolerates the ``eval_`` / ``eval-`` prefix and underscore/hyphen mixing:
    ``eval_agentic_loop``, ``agentic_loop``, and ``agentic-loop`` all resolve to
    ``docs/REPORT-eval-agentic-loop.md``.
    """
    name = scenario.strip()
    for prefix in ("eval_", "eval-"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    name = name.replace("_", "-")
    return _REPORTS_DIR / f"REPORT-eval-{name}.md"


def _parse_run_section(section: str) -> dict[str, CaseObservation]:
    """Extract ``{case_name: CaseObservation}`` from one ``## Run`` section body."""
    out: dict[str, CaseObservation] = {}
    for line in section.splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        case = match.group("case")
        verdict = match.group("verdict")
        if case.lower() == "case" or set(case) <= {"-", " "}:
            continue
        verdict_token = verdict.split(":")[0].strip().upper()
        if verdict_token not in _VERDICT_TOKENS and not verdict_token.startswith("SKIP"):
            continue
        score_match = _JUDGE_SCORE_RE.search(line)
        score = int(score_match.group(1)) if score_match else None
        out[case] = CaseObservation(verdict=verdict_token, judge_score=score)
    return out


def parse_runs(report_path: Path, limit: int = K) -> list[dict[str, CaseObservation]]:
    """Return up to ``limit`` most-recent run sections, newest first.

    REPORT sections stack newest-first under the title (``prepend_report``), so
    the first ``limit`` sections after the split are the most recent runs.
    """
    if not report_path.exists():
        return []
    text = report_path.read_text(encoding="utf-8")
    sections = _RUN_SPLIT_RE.split(text)[1:]
    runs: list[dict[str, CaseObservation]] = []
    for section in sections[:limit]:
        parsed = _parse_run_section(section)
        if parsed:
            runs.append(parsed)
    return runs


@dataclass(frozen=True)
class CaseDrift:
    case: str
    verdicts: list[str]
    flips: int
    score_delta: float | None


def compute_drift(runs: list[dict[str, CaseObservation]]) -> list[CaseDrift]:
    """Per case across runs (newest first): verdict-flip count + newest-vs-oldest score delta."""
    case_names: list[str] = []
    for run in runs:
        for case in run:
            if case not in case_names:
                case_names.append(case)

    drifts: list[CaseDrift] = []
    for case in case_names:
        observations = [run[case] for run in runs if case in run]
        verdicts = [o.verdict for o in observations]
        flips = sum(1 for a, b in pairwise(verdicts) if a != b)
        scores = [o.judge_score for o in observations if o.judge_score is not None]
        score_delta: float | None = None
        if len(scores) >= 2:
            score_delta = float(scores[0] - scores[-1])
        drifts.append(
            CaseDrift(case=case, verdicts=verdicts, flips=flips, score_delta=score_delta)
        )
    return drifts


def aggregate_verdict(drifts: list[CaseDrift]) -> tuple[str, list[str]]:
    """Return ``(verdict, notes)`` — ``DRIFT_SOFT_FAIL`` or ``STABLE``.

    SOFT_FAIL when more than ``FLIP_PCT`` of cases flipped at least once, or any
    case regressed (newest score lower than oldest) beyond ``SCORE_DELTA``.
    """
    if not drifts:
        return "INSUFFICIENT_HISTORY", []
    notes: list[str] = []
    flipped = [d for d in drifts if d.flips > 0]
    flip_ratio = len(flipped) / len(drifts)
    if flip_ratio > FLIP_PCT:
        notes.append(
            f"{len(flipped)}/{len(drifts)} cases flipped verdict "
            f"({flip_ratio:.0%} > {FLIP_PCT:.0%})"
        )
    regressed = [d for d in drifts if d.score_delta is not None and d.score_delta < -SCORE_DELTA]
    for d in regressed:
        notes.append(f"{d.case}: judge-score regressed {d.score_delta:+.0f}")
    return ("DRIFT_SOFT_FAIL" if notes else "STABLE"), notes


def _render(scenario: str, report_path: Path, runs: list[dict[str, CaseObservation]]) -> None:
    print(f"\n=== drift: {scenario}  ({report_path.name}) ===")
    if len(runs) < 2:
        print(f"insufficient history: {len(runs)} run(s) on disk (need ≥2 to diff).")
        return
    drifts = compute_drift(runs)
    print(f"window: {len(runs)} most-recent runs (newest first)\n")
    print(f"{'Case':<14} {'Flips':>5}  {'ScoreΔ':>7}  Verdicts (newest→oldest)")
    print(f"{'-' * 14} {'-' * 5}  {'-' * 7}  {'-' * 30}")
    for d in drifts:
        delta = "-" if d.score_delta is None else f"{d.score_delta:+.0f}"
        print(f"{d.case:<14} {d.flips:>5}  {delta:>7}  {' '.join(d.verdicts)}")
    verdict, notes = aggregate_verdict(drifts)
    print(f"\naggregate: {verdict}")
    for note in notes:
        print(f"  - {note}")


def _discover_scenarios() -> list[str]:
    """All ``REPORT-eval-<scenario>.md`` scenarios on disk."""
    out: list[str] = []
    for path in sorted(_REPORTS_DIR.glob("REPORT-eval-*.md")):
        out.append(path.stem.removeprefix("REPORT-eval-"))
    return out


def main(argv: list[str]) -> int:
    scenarios = argv[1:] if len(argv) > 1 else _discover_scenarios()
    if not scenarios:
        print("no REPORT-eval-*.md files found in docs/")
        return 0
    for scenario in scenarios:
        report_path = scenario_to_report_path(scenario)
        runs = parse_runs(report_path)
        _render(scenario, report_path, runs)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
