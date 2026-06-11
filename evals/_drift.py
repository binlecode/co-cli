"""Cross-run stability / drift aggregator for behavioral evals (plan T-9, dim 1).

Reads the last K per-run JSONL files ``evals/_outputs/<scenario>-<ts>-run.jsonl``,
and per case diffs ``(verdict, judge_score)`` across runs: a verdict-flip count
and the mean judge-score delta. Emits a drift table plus an aggregate verdict —
SOFT_FAIL when more than ``FLIP_PCT`` of cases flip verdict across the window,
or any case's score regresses beyond ``SCORE_DELTA``. This is the deferred
drift-tracker from ``uat_evals.md §Coverage gaps``, made concrete.

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
    uv run python evals/_drift.py                      # all scenarios on disk
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

_OUTPUTS_DIR = Path(__file__).parent / "_outputs"

K = 5
FLIP_PCT = 0.20
SCORE_DELTA = 2

_STEM_RE = re.compile(r"^(?P<scenario>.+)-\d{8}T\d{6}Z$")
_JUDGE_SCORE_RE = re.compile(r"judge\.score=(\d+)")

_VERDICT_TOKENS = {"PASS", "FAIL", "SOFT_PASS", "SOFT_FAIL"}


@dataclass(frozen=True)
class CaseObservation:
    """One case's outcome in one run."""

    verdict: str
    judge_score: int | None


def _run_glob(scenario: str) -> str:
    """Glob matching a scenario's per-run files: ``<scenario>-<ts>-run.jsonl``."""
    return f"{scenario}-????????T??????Z-run.jsonl"


def _parse_run_jsonl(run_jsonl_path: Path) -> dict[str, CaseObservation]:
    """Extract ``{case_name: CaseObservation}`` from one ``<stem>-run.jsonl`` file.

    ``verdict`` is a lowercase ``StrEnum`` value on disk (``"pass"`` /
    ``"soft_fail"``) — uppercased before the ``_VERDICT_TOKENS`` check. Skipped
    cases (``"skipped": true``) carry a real verdict but are excluded so they do
    not pollute the flip-ratio denominator. ``judge_score`` comes from the same
    ``judge.score=N`` pattern the markdown reader used, now applied to ``reason``.
    """
    out: dict[str, CaseObservation] = {}
    for line in run_jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("skipped"):
            continue
        name = data.get("name")
        verdict_raw = data.get("verdict")
        if not name or verdict_raw is None:
            continue
        verdict = str(verdict_raw).upper()
        if verdict not in _VERDICT_TOKENS:
            continue
        score_match = _JUDGE_SCORE_RE.search(str(data.get("reason", "")))
        score = int(score_match.group(1)) if score_match else None
        out[name] = CaseObservation(verdict=verdict, judge_score=score)
    return out


def parse_runs(scenario: str, limit: int = K) -> list[dict[str, CaseObservation]]:
    """Return up to ``limit`` most-recent runs for ``scenario``, newest first.

    Run files are named ``<scenario>-<ts>-run.jsonl`` with a sortable UTC
    timestamp, so a reverse filename sort is newest-first.
    """
    paths = sorted(_OUTPUTS_DIR.glob(_run_glob(scenario)), reverse=True)
    runs: list[dict[str, CaseObservation]] = []
    for path in paths[:limit]:
        parsed = _parse_run_jsonl(path)
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


def _render(scenario: str, runs: list[dict[str, CaseObservation]]) -> None:
    print(f"\n=== drift: {scenario} ===")
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
    """All scenarios with at least one ``<scenario>-<ts>-run.jsonl`` on disk."""
    scenarios: set[str] = set()
    for path in _OUTPUTS_DIR.glob("*-run.jsonl"):
        stem = path.name.removesuffix("-run.jsonl")
        match = _STEM_RE.match(stem)
        if match:
            scenarios.add(match.group("scenario"))
    return sorted(scenarios)


def _normalize_scenario(arg: str, discovered: list[str]) -> str:
    """Resolve a CLI arg to a discovered scenario.

    Strips an ``eval_`` / ``eval-`` prefix, then matches against discovered
    scenarios — exact first, then with ``_``↔``-`` swapped (scenarios like
    ``context-stability`` write hyphenated stems while most use underscores).
    Falls through to the stripped name when nothing matches; ``parse_runs`` then
    finds no files and the caller reports insufficient history.
    """
    name = arg.strip()
    for prefix in ("eval_", "eval-"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if name in discovered:
        return name
    for candidate in (name.replace("_", "-"), name.replace("-", "_")):
        if candidate in discovered:
            return candidate
    return name


def main(argv: list[str]) -> int:
    discovered = _discover_scenarios()
    if len(argv) > 1:
        scenarios = [_normalize_scenario(a, discovered) for a in argv[1:]]
    else:
        scenarios = discovered
    if not scenarios:
        print("no <scenario>-<ts>-run.jsonl files found in evals/_outputs/")
        return 0
    for scenario in scenarios:
        runs = parse_runs(scenario)
        _render(scenario, runs)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
