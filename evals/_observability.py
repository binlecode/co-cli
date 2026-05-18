"""Per-run JSONL observability + case-result accumulator for evals.

``EvalRun(name)`` is an async context manager that owns one
``evals/_outputs/<eval>-<ts>/`` directory. Each ``CaseResult`` is appended to
``run.jsonl`` as it lands; ``TurnTrace`` lines (one per ``run_turn`` driven by
the eval) are appended to ``case_<case_id>.jsonl`` via ``_trace.record_turn``.

Reviewers reading a REPORT row can click straight to the trace file and
replay step-by-step why a case passed or failed.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

_OUTPUTS_DIR = Path(__file__).parent / "_outputs"


class Verdict(StrEnum):
    """4-state case outcome. PASS / FAIL are gates; SOFT_PASS / SOFT_FAIL are review signals.

    SOFT_PASS — case passes the gate, but a non-load-bearing criterion was borderline
    (e.g. LLM-merge dropped a rare token in W3.F: the dedup behavior is correct, the
    rare-token preservation isn't).

    SOFT_FAIL — case fails a behavioral criterion within known LLM variance bounds
    (judge rubric flagged a borderline call). Surfaces in REPORT for review; doesn't
    fail the eval exit code. Three SOFT_FAILs in a row on the same case warrants
    manual promotion to FAIL.
    """

    PASS = "pass"
    FAIL = "fail"
    SOFT_PASS = "soft_pass"
    SOFT_FAIL = "soft_fail"


@dataclass
class CaseResult:
    """One sub-case outcome — appended to run.jsonl + rendered in REPORT.

    Field semantics:
      name                  — case id, e.g. "W1.A", used to look up case_<id>.jsonl.
      verdict               — 4-state Verdict (see Verdict docstring).
      duration_s            — total wall time including trace I/O.
      model_call_seconds    — sum of ``run_turn`` model-call seconds (asserted
                              against per-case latency budget per BC #13).
      token_usage           — {"prompt", "completion", "total"} summed across turns.
      trace_id              — co trace id (from observability spans log) for the case;
                              empty when no turn captured one. Use with ``co tail`` /
                              ``co trace <trace_id>`` to inspect the structured-log timeline.
      trace_files           — list of relative paths under _outputs/<run>/.
      reason                — short tag for FAIL/SKIP/SOFT; empty for plain PASS.
      skipped               — True when this is a SKIPPED:* record (mcp / product-gap).
      skip_category         — "mcp" | "product-gap" | "" when skipped is False.

    The ``passed`` property is True iff verdict ∈ {PASS, SOFT_PASS} — kept for
    exit-code logic and read-site backward-compat. Construction sites must use
    ``verdict=Verdict.PASS|FAIL|SOFT_PASS|SOFT_FAIL``.
    """

    name: str
    verdict: Verdict
    duration_s: float
    model_call_seconds: float = 0.0
    token_usage: dict[str, int] = field(default_factory=dict)
    trace_id: str = ""
    trace_files: list[str] = field(default_factory=list)
    reason: str = ""
    skipped: bool = False
    skip_category: str = ""

    @property
    def passed(self) -> bool:
        """True iff verdict is PASS or SOFT_PASS — for exit-code logic."""
        return self.verdict in (Verdict.PASS, Verdict.SOFT_PASS)

    @property
    def soft(self) -> bool:
        """True iff verdict is SOFT_PASS or SOFT_FAIL — for REPORT review-signal section."""
        return self.verdict in (Verdict.SOFT_PASS, Verdict.SOFT_FAIL)


@dataclass
class EvalRun:
    """Per-run output directory + JSONL writer.

    Lifecycle:
      async with EvalRun("daily_chat") as run:
          run.append(case_result)        # writes line to run.jsonl
          run.case_dir(case_id) / "..."  # path helper for trace lines

    On exit: closes the run.jsonl file; the directory itself stays for review.
    """

    name: str
    iso: str = ""
    started_at: float = 0.0
    dir: Path = field(default_factory=Path)

    @property
    def run_jsonl_path(self) -> Path:
        return self.dir / "run.jsonl"

    def case_trace_path(self, case_id: str) -> Path:
        """Relative-safe path for ``case_<id>.jsonl`` — created on first append."""
        return self.dir / f"case_{case_id}.jsonl"

    def append(self, case: CaseResult) -> None:
        """Append one ``CaseResult`` line to ``run.jsonl``."""
        line = json.dumps(asdict(case), default=str, separators=(",", ":"))
        with self.run_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


@asynccontextmanager
async def open_eval_run(name: str) -> AsyncIterator[EvalRun]:
    """Create ``evals/_outputs/<name>-<ts>/`` and yield an ``EvalRun``."""
    _OUTPUTS_DIR.mkdir(exist_ok=True)
    iso_now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = _OUTPUTS_DIR / f"{name}-{iso_now}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run = EvalRun(
        name=name,
        iso=datetime.now(UTC).isoformat(timespec="seconds"),
        started_at=time.monotonic(),
        dir=run_dir,
    )
    run.run_jsonl_path.touch(exist_ok=True)
    try:
        yield run
    finally:
        pass


def prior_run_dir(name: str, current: Path) -> Path | None:
    """Return the most-recent prior run directory for ``<name>``, or None."""
    if not _OUTPUTS_DIR.exists():
        return None
    candidates = sorted(
        (
            p
            for p in _OUTPUTS_DIR.iterdir()
            if p.is_dir() and p.name.startswith(f"{name}-") and p != current
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_prior_cases(run_dir: Path) -> dict[str, CaseResult]:
    """Read ``run.jsonl`` from a prior run; return {case_name: CaseResult}.

    Reads the post-migration schema (``verdict`` string). Lines missing the
    ``verdict`` field are skipped — old pre-migration JSONLs are not loadable
    by design (zero-backward-compat). Delete ``evals/_outputs/`` to start fresh.
    """
    path = run_dir / "run.jsonl"
    if not path.exists():
        return {}
    out: dict[str, CaseResult] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        verdict_raw = data.get("verdict")
        if verdict_raw is None:
            continue
        try:
            verdict = Verdict(verdict_raw)
        except ValueError:
            continue
        case = CaseResult(
            name=data.get("name", ""),
            verdict=verdict,
            duration_s=float(data.get("duration_s", 0.0)),
            model_call_seconds=float(data.get("model_call_seconds", 0.0)),
            token_usage=dict(data.get("token_usage") or {}),
            trace_id=data.get("trace_id", ""),
            trace_files=list(data.get("trace_files") or []),
            reason=data.get("reason", ""),
            skipped=bool(data.get("skipped", False)),
            skip_category=data.get("skip_category", ""),
        )
        out[case.name] = case
    return out
