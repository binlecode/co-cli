"""Prepend a dated ``## Run <ISO8601>`` section to ``docs/REPORT-eval-<workflow>.md``.

REPORT structure per the plan:
- Dated section header (``## Run <ISO8601>``)
- Header table: case verdict / duration / model_call_seconds / token usage
- Top-3 slow operations (sorted by ``model_call_seconds``)
- Regression-vs-prior-run diff (deltas vs the previous run's cases)
- Per-case trace-file links → ``_outputs/<eval>-<ts>/case_<id>.jsonl``
- Review signals — SOFT_PASS / SOFT_FAIL cases surfaced for human attention
"""

from __future__ import annotations

from pathlib import Path

from evals._observability import CaseResult, Verdict, load_prior_cases, prior_run_dir
from evals._perf import perf_verdict

_VERDICT_CHIP = {
    Verdict.PASS: "PASS",
    Verdict.FAIL: "FAIL",
    Verdict.SOFT_PASS: "SOFT_PASS",
    Verdict.SOFT_FAIL: "SOFT_FAIL",
}


def _verdict_chip(case: CaseResult) -> str:
    if case.skipped:
        return f"SKIP:{case.skip_category or '?'}"
    return _VERDICT_CHIP[case.verdict]


def _fmt_tokens(usage: dict[str, int]) -> str:
    if not usage:
        return "-"
    total = usage.get("total")
    if total is not None:
        return f"{total}"
    parts = []
    for key in ("prompt", "completion"):
        if key in usage:
            parts.append(f"{key[0]}{usage[key]}")
    return "/".join(parts) if parts else "-"


def _fmt_perf(case: CaseResult) -> str:
    """Compact ``p95 / peak-ctx / goal%`` cell, or ``-`` when no perf was collected."""
    p = case.perf
    if p is None:
        return "-"
    return f"{p.call_p95_s:.1f}s / {p.peak_input_tokens} / {p.goal_fulfillment * 100:.0f}%"


def _build_header_table(cases: list[CaseResult]) -> list[str]:
    lines = [
        "| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |",
        "|------|---------|----------|--------------|--------|---------------------|--------|",
    ]
    for c in cases:
        lines.append(
            f"| {c.name} | {_verdict_chip(c)} | {c.duration_s:.2f}s | "
            f"{c.model_call_seconds:.2f}s | {_fmt_tokens(c.token_usage)} | "
            f"{_fmt_perf(c)} | {c.reason or '-'} |"
        )
    return lines


def _build_slow_ops(cases: list[CaseResult]) -> list[str]:
    ranked = sorted(cases, key=lambda c: c.model_call_seconds, reverse=True)
    top = [c for c in ranked if c.model_call_seconds > 0][:3]
    if not top:
        return ["_(no model calls captured)_"]
    lines = ["| Rank | Case | Model-call s |", "|------|------|--------------|"]
    for i, c in enumerate(top, 1):
        lines.append(f"| {i} | {c.name} | {c.model_call_seconds:.2f}s |")
    return lines


def _build_review_signals(cases: list[CaseResult]) -> list[str]:
    lines: list[str] = []
    for c in cases:
        if c.soft:
            chip = _verdict_chip(c)
            lines.append(f"- **{c.name}** [{chip}] — {c.reason or '_(no reason)_'}")
    for c in cases:
        if c.perf is None:
            continue
        pv = perf_verdict(c.perf)
        if pv in (Verdict.FAIL, Verdict.SOFT_FAIL):
            p = c.perf
            lines.append(
                f"- **{c.name}** [perf {pv.value}] — p95={p.call_p95_s:.1f}s "
                f"peak-ctx={p.peak_input_tokens} goal={p.goal_fulfillment * 100:.0f}% "
                f"(perf signal only — does not override behavioral verdict)"
            )
    if not lines:
        return ["_(no review signals this run)_"]
    return lines


def _build_regression_diff(
    cases: list[CaseResult],
    prior: dict[str, CaseResult],
) -> list[str]:
    if not prior:
        return ["_(no prior run on disk)_"]
    rows: list[str] = []
    for c in cases:
        p = prior.get(c.name)
        if p is None:
            rows.append(f"- {c.name}: new case (no prior run)")
            continue
        if c.verdict != p.verdict:
            rows.append(
                f"- {c.name}: verdict {_VERDICT_CHIP[p.verdict]} → {_VERDICT_CHIP[c.verdict]}"
            )
        if c.model_call_seconds > 0 and p.model_call_seconds > 0:
            delta = c.model_call_seconds - p.model_call_seconds
            if abs(delta) >= 1.0:
                arrow = "↑" if delta > 0 else "↓"
                rows.append(
                    f"- {c.name}: model-call {arrow} "
                    f"{p.model_call_seconds:.1f}s → {c.model_call_seconds:.1f}s ({delta:+.1f}s)"
                )
    return rows if rows else ["_(no changes vs prior run)_"]


def _build_trace_links(cases: list[CaseResult], run_dir_relpath: str) -> list[str]:
    if not cases:
        return []
    lines = []
    for c in cases:
        if not c.trace_files:
            lines.append(f"- **{c.name}** — _(no trace)_")
            continue
        link_parts = [f"[{Path(t).name}]({run_dir_relpath}/{Path(t).name})" for t in c.trace_files]
        trace_chip = f" · `co trace {c.trace_id}`" if c.trace_id else ""
        lines.append(f"- **{c.name}** — {', '.join(link_parts)}{trace_chip}")
    return lines


def prepend_report(
    report_path: Path | str,
    eval_name: str,
    run_iso: str,
    cases: list[CaseResult],
    run_dir: Path | None = None,
) -> None:
    """Prepend a ``## Run <iso>`` block to ``report_path``.

    Creates the file with a one-line title if it doesn't exist. Appends — not
    replaces — so historical run sections stack newest-first under the title.
    """
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    prior_dir = prior_run_dir(eval_name, run_dir) if run_dir is not None else None
    prior_cases = load_prior_cases(prior_dir) if prior_dir is not None else {}

    run_dir_relpath = ""
    if run_dir is not None:
        try:
            run_dir_relpath = "../" + str(run_dir.relative_to(Path.cwd()))
        except ValueError:
            run_dir_relpath = str(run_dir)

    passed = sum(1 for c in cases if c.verdict == Verdict.PASS and not c.skipped)
    failed = sum(1 for c in cases if c.verdict == Verdict.FAIL and not c.skipped)
    soft_pass = sum(1 for c in cases if c.verdict == Verdict.SOFT_PASS)
    soft_fail = sum(1 for c in cases if c.verdict == Verdict.SOFT_FAIL)
    skipped = sum(1 for c in cases if c.skipped)

    section_lines: list[str] = [
        f"## Run {run_iso}",
        "",
        f"**Summary:** {passed} PASS · {failed} FAIL · {soft_pass} SOFT_PASS · "
        f"{soft_fail} SOFT_FAIL · {skipped} SKIP (total {len(cases)})",
        "",
        "### Cases",
        "",
    ]
    section_lines += _build_header_table(cases)
    section_lines += ["", "### Review signals", ""]
    section_lines += _build_review_signals(cases)
    section_lines += ["", "### Slow ops (top 3)", ""]
    section_lines += _build_slow_ops(cases)
    section_lines += ["", "### Regression vs prior run", ""]
    section_lines += _build_regression_diff(cases, prior_cases)
    section_lines += ["", "### Trace files", ""]
    section_lines += _build_trace_links(cases, run_dir_relpath)
    section_lines += ["", "---", ""]

    new_section = "\n".join(section_lines) + "\n"

    if report_path.exists():
        existing = report_path.read_text(encoding="utf-8")
        title_marker = f"# REPORT: {eval_name}"
        if existing.startswith(title_marker):
            header, _, body = existing.partition("\n\n")
            content = header + "\n\n" + new_section + body
        else:
            content = f"# REPORT: {eval_name}\n\n" + new_section + existing
    else:
        content = f"# REPORT: {eval_name}\n\n" + new_section

    report_path.write_text(content, encoding="utf-8")
