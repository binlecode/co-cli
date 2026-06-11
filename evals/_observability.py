"""Per-run JSONL observability + case-result accumulator for evals.

``open_eval_run(name)`` yields an ``EvalRun`` whose artifacts are flat,
prefix-keyed files under ``evals/_outputs/``: ``<name>-<ts>-run.jsonl`` (one
``CaseResult`` line per case, appended as it lands), ``<name>-<ts>-case_<id>.jsonl``
(``TurnTrace`` lines, one per ``run_turn``, via ``_trace.record_turn``), and
``<name>-<ts>-spans.jsonl`` (the per-run span dump).

Reviewers can open a case trace file directly — or ``co trace <trace_id>`` —
to replay step-by-step why a case passed or failed.
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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evals._perf import PerfRecord

_OUTPUTS_DIR = Path(__file__).parent / "_outputs"


class Verdict(StrEnum):
    """4-state case outcome. PASS / FAIL are gates; SOFT_PASS / SOFT_FAIL are review signals.

    SOFT_PASS — case passes the gate, but a non-load-bearing criterion was borderline
    (e.g. LLM-merge dropped a rare token in W3.F: the dedup behavior is correct, the
    rare-token preservation isn't).

    SOFT_FAIL — case fails a behavioral criterion within known LLM variance bounds
    (judge rubric flagged a borderline call). Recorded in run.jsonl for review;
    doesn't fail the eval exit code. Three SOFT_FAILs in a row on the same case
    warrants manual promotion to FAIL.
    """

    PASS = "pass"
    FAIL = "fail"
    SOFT_PASS = "soft_pass"
    SOFT_FAIL = "soft_fail"


@dataclass
class CaseResult:
    """One sub-case outcome — appended to ``<stem>-run.jsonl``.

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
      trace_files           — list of paths relative to _outputs/.
      reason                — short tag for FAIL/SKIP/SOFT; empty for plain PASS.
      skipped               — True when this is a SKIPPED:* record (mcp / product-gap).
      skip_category         — "mcp" | "product-gap" | "" when skipped is False.
      perf                  — span-derived performance overlay (``evals/_perf.py``),
                              or None when the case did not collect perf. Recorded
                              in run.jsonl as a review signal; never overrides the
                              behavioral ``verdict``.

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
    perf: PerfRecord | None = None

    @property
    def passed(self) -> bool:
        """True iff verdict is PASS or SOFT_PASS — for exit-code logic."""
        return self.verdict in (Verdict.PASS, Verdict.SOFT_PASS)

    @property
    def soft(self) -> bool:
        """True iff verdict is SOFT_PASS or SOFT_FAIL — review signal recorded in run.jsonl."""
        return self.verdict in (Verdict.SOFT_PASS, Verdict.SOFT_FAIL)


@dataclass
class EvalRun:
    """Per-run JSONL writer keyed by a flat ``<name>-<ts>`` stem.

    Lifecycle:
      async with open_eval_run("daily_chat") as run:
          run.append(case_result)        # appends a line to <stem>-run.jsonl
          run.case_trace_path(case_id)   # path for <stem>-case_<id>.jsonl

    On exit: the output files stay under ``_outputs/`` for review.
    """

    name: str
    iso: str = ""
    started_at: float = 0.0
    stem: str = ""

    @property
    def outputs_dir(self) -> Path:
        """Flat directory holding every run's prefix-keyed artifacts."""
        return _OUTPUTS_DIR

    @property
    def run_jsonl_path(self) -> Path:
        return _OUTPUTS_DIR / f"{self.stem}-run.jsonl"

    def case_trace_path(self, case_id: str) -> Path:
        """Path for this run's ``<stem>-case_<id>.jsonl`` — created on first append."""
        return _OUTPUTS_DIR / f"{self.stem}-case_{case_id}.jsonl"

    @property
    def spans_path(self) -> Path:
        """Path for this run's ``<stem>-spans.jsonl`` span dump."""
        return _OUTPUTS_DIR / f"{self.stem}-spans.jsonl"

    def append(self, case: CaseResult) -> None:
        """Append one ``CaseResult`` line to ``run.jsonl``."""
        line = json.dumps(asdict(case), default=str, separators=(",", ":"))
        with self.run_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


@asynccontextmanager
async def open_eval_run(name: str) -> AsyncIterator[EvalRun]:
    """Yield an ``EvalRun`` writing flat ``_outputs/<name>-<ts>-*`` artifacts."""
    _OUTPUTS_DIR.mkdir(exist_ok=True)
    compact_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run = EvalRun(
        name=name,
        iso=datetime.now(UTC).isoformat(timespec="seconds"),
        started_at=time.monotonic(),
        stem=f"{name}-{compact_ts}",
    )
    run.run_jsonl_path.touch(exist_ok=True)
    try:
        yield run
    finally:
        pass
