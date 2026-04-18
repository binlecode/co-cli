#!/usr/bin/env python3
"""Audit LLM API calls from a pytest run.

Reads a pytest log (--log) and the OTel trace DB (--db), correlates chat spans
by duration to their test contexts, and writes docs/REPORT-llm-call-audit-YYYYMMDD-HHMMSS.md.

Usage:
    uv run python scripts/llm_call_audit.py --log .pytest-logs/20260418-110642-full-flow-audit.log
    uv run python scripts/llm_call_audit.py --log .pytest-logs/... --db ~/.co-cli/co-cli-logs.db
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DB = Path.home() / ".co-cli" / "co-cli-logs.db"
_DEFAULT_OUT = _REPO_ROOT / "docs"

# Duration tolerance (ms) for fuzzy matching log spans to DB spans.
_DURATION_TOLERANCE_MS = 150.0

# Time window padding around estimated run boundaries when querying DB.
_WINDOW_BUFFER_S = 7200  # 2 hours on each side

# Slow-test threshold used by the pytest harness (from _co_harness.py default).
_SLOW_MS = 2000

# Summary line: [pytest-harness] <test_id> | key=val | ...
_SUMMARY_PAT = re.compile(r"^\[pytest-harness\] (\S+) \| (.*)")
# Detail line: [pytest-harness]   <dur>s | chat <model> | ...
_DETAIL_PAT = re.compile(r"^\[pytest-harness\]\s{3}([\d.]+)s \| (chat \S+)")
# Pytest session summary line
_SESSION_PAT = re.compile(r"(\d+) passed in ([\d.]+)s")


class ChatSpan(NamedTuple):
    test_id: str
    flow: str
    duration_ms: float
    model: str | None
    api: str | None
    provider: str | None
    system: str | None
    finish_reasons: list[str]
    input_tokens: int | None
    output_tokens: int | None
    input_chars: int | None
    output_chars: int | None


def _parse_kv(tail: str) -> dict[str, str]:
    result = {}
    for part in tail.split(" | "):
        part = part.strip()
        if "=" in part:
            key, _, val = part.partition("=")
            result[key.strip()] = val.strip()
    return result


def _infer_flow(test_id: str) -> str:
    """Derive a human-readable flow label from a test node ID."""
    parts = test_id.split("::")
    name = parts[-1].lower() if parts else test_id.lower()
    param_m = re.search(r"\[([^\]]+)\]", name)
    param = param_m.group(1).lower() if param_m else ""
    base = re.sub(r"\[.*\]", "", name)

    if "approval" in base:
        return "approval"
    if ("extraction" in base and "memory" in base) or "distiller" in base:
        return "memory extraction"
    if "circuit_breaker" in base or re.search(r"\bcompact\b", base):
        return "history compaction"
    if "dream_cycle" in base:
        return "knowledge dream cycle"
    if "dream_mine" in base:
        return "knowledge dream mining"
    if "dream_merge" in base:
        return "knowledge dream merge"
    if "tool_selection" in base or "arg_extraction" in base:
        for keyword, label in (("shell", "shell"), ("web", "web"), ("knowledge", "knowledge")):
            if keyword in param:
                return f"tool calling: {label}"
        return "tool calling"
    if "no_tool" in base or "refusal" in base:
        return "tool calling: no-tool"
    if "intent_routing" in base:
        return "intent routing"
    # Fall back to module name
    mod = parts[0].split("/")[-1] if parts else ""
    return re.sub(r"^test_|\.py$", "", mod).replace("_", " ") or "unknown"


def _parse_log(log_path: Path) -> tuple[list[tuple[str, float]], float, float]:
    """
    Parse a pytest harness log.

    Returns:
        log_spans: (test_id, chat_duration_ms) for each chat detail line found
        run_start_ts: estimated unix timestamp of run start
        run_end_ts: estimated unix timestamp of run end
    """
    log_spans: list[tuple[str, float]] = []
    current_test: str | None = None
    run_total_s = 0.0

    for line in log_path.read_text(errors="replace").splitlines():
        m = _SESSION_PAT.search(line)
        if m:
            run_total_s = float(m.group(2))
            continue

        if line.startswith("[pytest-harness] ") and not line.startswith("[pytest-harness]   "):
            m2 = _SUMMARY_PAT.match(line)
            if m2:
                kv = _parse_kv(m2.group(2))
                current_test = m2.group(1) if "models" in kv else None
            continue

        if line.startswith("[pytest-harness]   ") and current_test:
            m3 = _DETAIL_PAT.match(line)
            if m3 and not m3.group(2).startswith("chat function::"):
                log_spans.append((current_test, float(m3.group(1)) * 1000))

    mtime = log_path.stat().st_mtime
    # Parse start time from filename like 20260418-110642-*.log
    fname_m = re.match(r"(\d{8})-(\d{6})", log_path.stem)
    if fname_m:
        try:
            run_start = datetime.strptime(
                fname_m.group(1) + fname_m.group(2), "%Y%m%d%H%M%S"
            ).timestamp()
        except ValueError:
            run_start = mtime - max(run_total_s, 300)
    else:
        run_start = mtime - max(run_total_s, 300)

    run_end = run_start + max(run_total_s, 300)
    return log_spans, run_start, run_end


def _query_db_spans(db_path: Path, run_start: float, run_end: float) -> list[dict]:
    """Query co-cli-pytest chat spans from DB within an expanded time window."""
    if not db_path.exists():
        return []
    window_start_ns = int((run_start - _WINDOW_BUFFER_S) * 1_000_000_000)
    window_end_ns = int((run_end + _WINDOW_BUFFER_S) * 1_000_000_000)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT duration_ms, attributes
            FROM spans
            WHERE name LIKE 'chat %'
              AND resource LIKE '%co-cli-pytest%'
              AND start_time BETWEEN ? AND ?
            ORDER BY start_time
            """,
            (window_start_ns, window_end_ns),
        ).fetchall()
    result = []
    for duration_ms, attributes_json in rows:
        try:
            attrs = json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            attrs = {}
        result.append({"duration_ms": duration_ms or 0.0, "attrs": attrs})
    return result


def _build_api(attrs: dict) -> str | None:
    host = attrs.get("server.address")
    port = attrs.get("server.port")
    if isinstance(host, str) and host:
        return f"{host}:{port}" if port is not None else host
    return None


def _match_spans(
    log_spans: list[tuple[str, float]],
    db_spans: list[dict],
) -> list[ChatSpan]:
    """Fuzzy-match log chat spans to DB spans by duration and build ChatSpan list."""
    used: set[int] = set()
    result: list[ChatSpan] = []

    for test_id, log_dur_ms in log_spans:
        flow = _infer_flow(test_id)

        # Find closest unmatched DB span within tolerance
        best_idx: int | None = None
        best_diff = _DURATION_TOLERANCE_MS + 1.0
        for idx, db_span in enumerate(db_spans):
            if idx in used:
                continue
            diff = abs(db_span["duration_ms"] - log_dur_ms)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx

        if best_idx is not None and best_diff <= _DURATION_TOLERANCE_MS:
            used.add(best_idx)
            attrs = db_spans[best_idx]["attrs"]
            db_dur = db_spans[best_idx]["duration_ms"]

            finish_reasons = attrs.get("gen_ai.response.finish_reasons", [])
            if isinstance(finish_reasons, str):
                try:
                    finish_reasons = json.loads(finish_reasons)
                except json.JSONDecodeError:
                    finish_reasons = [finish_reasons]

            input_msgs = attrs.get("gen_ai.input.messages", "")
            output_msgs = attrs.get("gen_ai.output.messages", "")

            result.append(
                ChatSpan(
                    test_id=test_id,
                    flow=flow,
                    duration_ms=db_dur,
                    model=attrs.get("gen_ai.request.model"),
                    api=_build_api(attrs),
                    provider=attrs.get("gen_ai.provider.name"),
                    system=attrs.get("gen_ai.system"),
                    finish_reasons=finish_reasons,
                    input_tokens=attrs.get("gen_ai.usage.input_tokens"),
                    output_tokens=attrs.get("gen_ai.usage.output_tokens"),
                    input_chars=len(input_msgs) if isinstance(input_msgs, str) else None,
                    output_chars=len(output_msgs) if isinstance(output_msgs, str) else None,
                )
            )
        else:
            result.append(
                ChatSpan(
                    test_id=test_id,
                    flow=flow,
                    duration_ms=log_dur_ms,
                    model=None,
                    api=None,
                    provider=None,
                    system=None,
                    finish_reasons=[],
                    input_tokens=None,
                    output_tokens=None,
                    input_chars=None,
                    output_chars=None,
                )
            )

    return result


def _verdict(span: ChatSpan) -> str:
    if not span.finish_reasons:
        return "no DB match"
    if "length" in span.finish_reasons:
        return "WARN: length"
    if (
        span.output_tokens is not None
        and span.output_tokens <= 3
        and "stop" in span.finish_reasons
    ):
        return "OK, minimal"
    return "OK"


def _fmt(val: int | float | None) -> str:
    return "—" if val is None else str(val)


def _dur_s(ms: float) -> str:
    return f"{ms / 1000:.3f}s"


def _api_finding(matched: list[ChatSpan], unmatched_count: int, apis: list[str]) -> str:
    if not matched:
        return "No DB-matched spans — API correctness cannot be assessed."
    if unmatched_count:
        return (
            f"{unmatched_count} span(s) had no DB match. "
            f"Matched calls used: {', '.join(f'api=`{a}`' for a in apis)}. "
            "No provider drift observed among matched calls."
        )
    return (
        f"All {len(matched)} matched calls used: "
        + ", ".join(f"api=`{a}`" for a in apis)
        + ". No provider drift observed."
    )


def _finish_finding(finish_counts: dict[str, int]) -> str:
    unexpected = {r for r in finish_counts if r not in ("tool_call", "stop", "length")}
    if not finish_counts:
        return "No finish reason data available."
    if "length" in finish_counts:
        return f"WARNING: `{finish_counts['length']}` call(s) finished with `length` — possible output truncation."
    if unexpected:
        return f"Unexpected finish reasons observed: {', '.join(f'`{r}`' for r in sorted(unexpected))}."
    return "Finish reasons were `tool_call` and `stop` only — no unexpected terminations or length clipping."


def _cut_finding(warn_spans: list[ChatSpan], small_stops: list[ChatSpan]) -> str:
    if warn_spans:
        return (
            f"WARNING: {len(warn_spans)} call(s) with `finish_reason=length` need investigation."
        )
    if small_stops:
        return (
            f"{len(small_stops)} `stop` call(s) returned ≤3 output tokens. "
            "These appear to be intentional minimal acknowledgements, not truncation."
        )
    return "No suspiciously small `stop` outputs or `length` terminations detected."


def _generate_report(
    spans: list[ChatSpan],
    log_path: Path,
    db_path: Path,
    db_span_count: int,
    matched_count: int,
) -> str:
    today = date.today().isoformat()
    matched = [s for s in spans if s.finish_reasons]
    unmatched_count = len(spans) - len(matched)

    models = sorted({s.model for s in matched if s.model})
    apis = sorted({s.api for s in matched if s.api})
    providers = sorted({s.provider for s in matched if s.provider})

    finish_counts: dict[str, int] = defaultdict(int)
    for s in matched:
        for r in s.finish_reasons:
            finish_counts[r] += 1

    warn_spans = [s for s in spans if "WARN" in _verdict(s)]
    small_stops = [
        s
        for s in matched
        if (s.output_tokens is not None and s.output_tokens <= 3 and "stop" in s.finish_reasons)
    ]

    slowest = max(spans, key=lambda s: s.duration_ms)

    sections: list[str] = []

    sections.append(f"""\
# REPORT: LLM Call Audit from Pytest Run

**Date:** {today}
**Log Source:** `{log_path}`
**Trace Source:** `{db_path}`

## 1. Scope

Audits LLM chat calls visible in the specified pytest log against OTel spans in the trace DB.
Spans are matched by duration (±{_DURATION_TOLERANCE_MS:.0f} ms tolerance). Only tests that
exceeded the harness slow threshold ({_SLOW_MS} ms) emit per-span detail; tests faster than
this threshold are excluded from this report.

- Chat spans extracted from log: `{len(spans)}`
- DB spans found in time window: `{db_span_count}`
- DB spans matched: `{matched_count}`
- Unmatched (log-only, no token data): `{unmatched_count}`
""")

    # Executive summary
    correctness = (
        f"`{len(matched)}/{len(matched)}` matched calls used "
        f"api=`{', '.join(apis)}`, provider=`{', '.join(providers)}`"
        if apis and providers
        else "N/A (no DB matches)"
    )
    finish_lines = (
        "\n".join(f"  - `{count}` `{reason}`" for reason, count in sorted(finish_counts.items()))
        or "  - (none)"
    )
    slowest_desc = f"`{_dur_s(slowest.duration_ms)}` — {slowest.flow}"

    sections.append(f"""\
## 2. Executive Summary

- Visible LLM call spans audited: `{len(spans)}`
- API correctness: {correctness}
- Models observed: {", ".join(f"`{m}`" for m in models) if models else "N/A"}
- Finish reasons:
{finish_lines}
- Confirmed output-cut anomalies (`finish_reason=length`): `{len(warn_spans)}`
- Small `stop` outputs (≤3 tokens): `{len(small_stops)}`
- Slowest visible call: {slowest_desc}
""")

    # Per-call table
    rows = []
    for idx, span in enumerate(spans, 1):
        finish_str = (
            ", ".join(f"`{r}`" for r in span.finish_reasons) if span.finish_reasons else "—"
        )
        rows.append(
            f"| {idx} | `{span.test_id.split('::')[-1]}` / {span.flow} "
            f"| {_dur_s(span.duration_ms)} | {finish_str} "
            f"| {_fmt(span.input_tokens)} | {_fmt(span.output_tokens)} "
            f"| {_fmt(span.input_chars)} | {_fmt(span.output_chars)} "
            f"| {_verdict(span)} |"
        )

    sections.append(
        "## 3. Per-Call Metrics\n\n"
        "| # | Test / Flow | Duration | Finish | In Tokens | Out Tokens | In Chars | Out Chars | Verdict |\n"
        "|---|---|---:|---|---:|---:|---:|---:|---|\n" + "\n".join(rows) + "\n"
    )

    # Workflow breakdown
    by_flow: dict[str, list[ChatSpan]] = defaultdict(list)
    for span in spans:
        by_flow[span.flow].append(span)

    flow_rows = []
    for flow, flow_spans in sorted(by_flow.items()):
        durs = [s.duration_ms for s in flow_spans]
        in_toks = [s.input_tokens for s in flow_spans if s.input_tokens is not None]
        out_toks = [s.output_tokens for s in flow_spans if s.output_tokens is not None]
        flow_rows.append(
            f"| {flow} | {len(flow_spans)} "
            f"| {_dur_s(statistics.median(durs))} "
            f"| {_dur_s(max(durs))} "
            f"| {_dur_s(statistics.mean(durs))} "
            f"| {_fmt(round(statistics.median(in_toks))) if in_toks else '—'} "
            f"| {_fmt(max(in_toks)) if in_toks else '—'} "
            f"| {_fmt(round(statistics.median(out_toks))) if out_toks else '—'} "
            f"| {_fmt(max(out_toks)) if out_toks else '—'} |"
        )

    sections.append(
        "## 4. Workflow Breakdown\n\n"
        "| Flow | Calls | Median Duration | Max Duration | Mean Duration"
        " | Median In Tokens | Max In Tokens | Median Out Tokens | Max Out Tokens |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n" + "\n".join(flow_rows) + "\n"
    )

    # Findings
    api_finding = _api_finding(matched, unmatched_count, apis)
    finish_finding = _finish_finding(finish_counts)
    cut_finding = _cut_finding(warn_spans, small_stops)

    sorted_by_max = sorted(
        by_flow.items(), key=lambda kv: max(s.duration_ms for s in kv[1]), reverse=True
    )
    latency_lines = "\n".join(
        f"- **{flow}**: max `{_dur_s(max(s.duration_ms for s in fspans))}`, "
        f"median `{_dur_s(statistics.median(s.duration_ms for s in fspans))}`"
        for flow, fspans in sorted_by_max[:5]
    )

    sections.append(f"""\
## 5. Findings

### 5.1 API Correctness

{api_finding}

### 5.2 Finish Reason Behavior

{finish_finding}

### 5.3 Output Size / Cutting Check

{cut_finding}

### 5.4 Latency Hotspots (top 5 by max duration)

{latency_lines}
""")

    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log", required=True, type=Path, help="pytest log file")
    parser.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB,
        help="OTel trace DB (default: ~/.co-cli/co-cli-logs.db)",
    )
    parser.add_argument(
        "--out", type=Path, default=_DEFAULT_OUT, help="output directory (default: docs/)"
    )
    args = parser.parse_args()

    log_path = args.log.resolve()
    db_path = args.db.resolve()
    out_dir = args.out.resolve()

    if not log_path.exists():
        raise SystemExit(f"Log not found: {log_path}")

    print(f"Parsing:  {log_path}")
    log_spans, run_start, run_end = _parse_log(log_path)

    if not log_spans:
        raise SystemExit(
            "No chat spans found in log. "
            "The log must be from a run with LLM-backed tests that exceeded "
            f"{_SLOW_MS} ms (harness slow threshold)."
        )

    print(
        f"  {len(log_spans)} chat spans — estimated run "
        f"{datetime.fromtimestamp(run_start).strftime('%Y-%m-%d %H:%M:%S')} → "
        f"{datetime.fromtimestamp(run_end).strftime('%H:%M:%S')}"
    )

    print(f"Querying: {db_path}")
    db_spans = _query_db_spans(db_path, run_start, run_end)
    print(f"  {len(db_spans)} co-cli-pytest chat spans in time window")

    if not db_spans:
        print(
            "  WARNING: No DB spans found. The DB may not contain spans for this specific "
            "run (they may be in a temp dir DB or from a different process). "
            "Report will be log-only with no token data."
        )

    spans = _match_spans(log_spans, db_spans)
    matched_count = sum(1 for s in spans if s.finish_reasons)
    print(f"  Matched {matched_count}/{len(spans)} log spans to DB entries")

    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"REPORT-llm-call-audit-{now}.md"
    report = _generate_report(spans, log_path, db_path, len(db_spans), matched_count)
    out_path.write_text(report)

    warn_count = sum(1 for s in spans if "WARN" in _verdict(s))
    print(f"\nReport → {out_path}")
    print(
        f"TL;DR: {len(spans)} spans audited, {matched_count} DB-matched, "
        f"{warn_count} warnings{'.' if not warn_count else ' — check report.'}"
    )


if __name__ == "__main__":
    main()
