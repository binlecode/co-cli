"""Prepend a dated ``## Run <ISO8601>`` section to ``docs/REPORT-eval-<workflow>.md``.

REPORT structure per the plan:
- Dated section header (``## Run <ISO8601>``)
- Header table: case verdict / duration / model_call_seconds / token usage
- Top-3 slow operations (sorted by ``model_call_seconds``)
- Regression-vs-prior-run diff (deltas vs the previous run's cases)
- Per-case trace-file links → ``_outputs/<eval>-<ts>/case_<id>.jsonl``
"""

from __future__ import annotations

from pathlib import Path

from evals._observability import CaseResult, load_prior_cases, prior_run_dir


def _verdict_chip(case: CaseResult) -> str:
    if case.skipped:
        return f"SKIP:{case.skip_category or '?'}"
    if case.passed:
        return "SOFT" if case.soft_fail else "PASS"
    return "FAIL"


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


def _build_header_table(cases: list[CaseResult]) -> list[str]:
    lines = [
        "| Case | Verdict | Duration | Model-call s | Tokens | Reason |",
        "|------|---------|----------|--------------|--------|--------|",
    ]
    for c in cases:
        lines.append(
            f"| {c.name} | {_verdict_chip(c)} | {c.duration_s:.2f}s | "
            f"{c.model_call_seconds:.2f}s | {_fmt_tokens(c.token_usage)} | {c.reason or '-'} |"
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
        if c.passed != p.passed:
            rows.append(
                f"- {c.name}: verdict {'PASS' if p.passed else 'FAIL'} → "
                f"{'PASS' if c.passed else 'FAIL'}"
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
        otel = f" · trace_id=`{c.trace_id}`" if c.trace_id else ""
        lines.append(f"- **{c.name}** — {', '.join(link_parts)}{otel}")
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

    failed = sum(1 for c in cases if not c.passed and not c.skipped)
    soft = sum(1 for c in cases if c.soft_fail)
    skipped = sum(1 for c in cases if c.skipped)
    passed = sum(1 for c in cases if c.passed and not c.skipped)

    section_lines: list[str] = [
        f"## Run {run_iso}",
        "",
        f"**Summary:** {passed} PASS · {failed} FAIL · {soft} SOFT_FAIL · {skipped} SKIP "
        f"(total {len(cases)})",
        "",
        "### Cases",
        "",
    ]
    section_lines += _build_header_table(cases)
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
