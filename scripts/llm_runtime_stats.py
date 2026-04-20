#!/usr/bin/env python3
"""Generate a runtime statistics report from the OTel trace DB.

Queries production co-cli chat spans (excludes pytest and eval services) and writes
docs/REPORT-llm-runtime-stats-YYYYMMDD-HHMMSS.md.

Usage:
    uv run python scripts/llm_runtime_stats.py
    uv run python scripts/llm_runtime_stats.py --since 2026-04-01
    uv run python scripts/llm_runtime_stats.py --since 2026-04-01 --until 2026-04-30
    uv run python scripts/llm_runtime_stats.py --db ~/.co-cli/co-cli-logs.db --out docs/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DB = Path.home() / ".co-cli" / "co-cli-logs.db"
_DEFAULT_OUT = _REPO_ROOT / "docs"

# Exclude guardrail/mock functions that pollute real usage stats
_MOCK_NAME_PREFIX = "chat function::"


class ChatSpan(NamedTuple):
    name: str
    duration_ms: float
    model: str | None
    provider: str | None
    api: str | None
    finish_reasons: list[str]
    input_tokens: int | None
    output_tokens: int | None
    has_thinking: bool
    start_time_ns: int


def _fmt(val: int | float | None) -> str:
    return "—" if val is None else str(val)


def _dur_s(ms: float) -> str:
    return f"{ms / 1000:.3f}s"


def _pct(num: int, den: int) -> str:
    return f"{num / den * 100:.1f}%" if den > 0 else "—"


def _query_spans(
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> tuple[list[ChatSpan], list[dict], list[dict]]:
    """
    Returns (chat_spans, orchestration_rows, tool_rows).

    chat_spans: real model calls only (no mock functions).
    orchestration_rows: [{name, count}] for invoke_agent / co.turn / etc.
    tool_rows: [{name, count}] for execute_tool spans.
    """
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    time_filter = ""
    params: list[int] = []
    if since_ns is not None:
        time_filter += " AND start_time >= ?"
        params.append(since_ns)
    if until_ns is not None:
        time_filter += " AND start_time <= ?"
        params.append(until_ns)

    prod_filter = (
        '(resource LIKE \'%"service.name": "co-cli"%\''
        " AND resource NOT LIKE '%co-cli-pytest%'"
        " AND resource NOT LIKE '%co-cli-eval%')"
    )

    with sqlite3.connect(db_path) as conn:
        # Real chat spans (exclude mock/guardrail functions)
        chat_rows = conn.execute(
            f"""
            SELECT name, duration_ms, attributes, start_time
            FROM spans
            WHERE name LIKE 'chat %'
              AND name NOT LIKE '{_MOCK_NAME_PREFIX}%'
              AND {prod_filter}
              {time_filter}
            ORDER BY start_time
            """,
            params,
        ).fetchall()

        # Orchestration spans
        orch_rows = conn.execute(
            f"""
            SELECT name, COUNT(*) as cnt
            FROM spans
            WHERE name IN (
                'invoke_agent agent', 'co.turn', 'ctx_overflow_check',
                'restore_session', 'sync_knowledge'
            )
              AND {prod_filter}
              {time_filter}
            GROUP BY name
            ORDER BY cnt DESC
            """,
            params,
        ).fetchall()

        # Tool execution spans
        tool_rows = conn.execute(
            f"""
            SELECT REPLACE(name, 'execute_tool ', '') as tool_name, COUNT(*) as cnt
            FROM spans
            WHERE name LIKE 'execute_tool %'
              AND {prod_filter}
              {time_filter}
            GROUP BY tool_name
            ORDER BY cnt DESC
            LIMIT 20
            """,
            params,
        ).fetchall()

    chat_spans: list[ChatSpan] = []
    for name, duration_ms, attributes_json, start_time_ns in chat_rows:
        try:
            attrs = json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            attrs = {}

        finish_reasons = attrs.get("gen_ai.response.finish_reasons", [])
        if isinstance(finish_reasons, str):
            try:
                finish_reasons = json.loads(finish_reasons)
            except json.JSONDecodeError:
                finish_reasons = [finish_reasons]

        has_thinking = False
        output_msgs_raw = attrs.get("gen_ai.output.messages")
        if isinstance(output_msgs_raw, str):
            try:
                msgs = json.loads(output_msgs_raw)
                if isinstance(msgs, list):
                    has_thinking = any(
                        part.get("type") == "thinking"
                        for msg in msgs
                        if isinstance(msg, dict)
                        for part in msg.get("parts", [])
                        if isinstance(part, dict)
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        host = attrs.get("server.address")
        port = attrs.get("server.port")
        api = f"{host}:{port}" if host and port is not None else (host or None)

        chat_spans.append(
            ChatSpan(
                name=name,
                duration_ms=duration_ms or 0.0,
                model=attrs.get("gen_ai.request.model"),
                provider=attrs.get("gen_ai.provider.name"),
                api=api,
                finish_reasons=finish_reasons,
                input_tokens=attrs.get("gen_ai.usage.input_tokens"),
                output_tokens=attrs.get("gen_ai.usage.output_tokens"),
                has_thinking=has_thinking,
                start_time_ns=start_time_ns or 0,
            )
        )

    orch = [{"name": row[0], "count": row[1]} for row in orch_rows]
    tools = [{"name": row[0], "count": row[1]} for row in tool_rows]
    return chat_spans, orch, tools


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * pct / 100
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _generate_report(
    spans: list[ChatSpan],
    orch: list[dict],
    tools: list[dict],
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> str:
    today = date.today().isoformat()

    if spans:
        actual_start = datetime.fromtimestamp(
            min(s.start_time_ns for s in spans) / 1e9, tz=UTC
        ).strftime("%Y-%m-%d %H:%M UTC")
        actual_end = datetime.fromtimestamp(
            max(s.start_time_ns for s in spans) / 1e9, tz=UTC
        ).strftime("%Y-%m-%d %H:%M UTC")
        time_range = f"`{actual_start}` → `{actual_end}`"
    else:
        time_range = "no spans found"

    since_label = (
        datetime.fromtimestamp(since_ns / 1e9, tz=UTC).strftime("%Y-%m-%d")
        if since_ns
        else "all time"
    )
    until_label = (
        datetime.fromtimestamp(until_ns / 1e9, tz=UTC).strftime("%Y-%m-%d") if until_ns else "now"
    )

    models = sorted({s.model for s in spans if s.model})
    providers = sorted({s.provider for s in spans if s.provider})
    apis = sorted({s.api for s in spans if s.api})

    total_in = sum(s.input_tokens for s in spans if s.input_tokens is not None)
    total_out = sum(s.output_tokens for s in spans if s.output_tokens is not None)
    io_ratio = f"{total_in / total_out:.1f}" if total_out > 0 else "—"

    finish_counts: dict[str, int] = defaultdict(int)
    for s in spans:
        for r in s.finish_reasons:
            finish_counts[r] += 1

    thinking_spans = sum(1 for s in spans if s.has_thinking)

    throughputs = [
        s.output_tokens / s.duration_ms * 1000
        for s in spans
        if s.output_tokens is not None and s.duration_ms > 0
    ]

    sections: list[str] = []

    sections.append(f"""\
# REPORT: LLM Runtime Statistics

**Date:** {today}
**Source:** `{db_path}`
**Filter:** `{since_label}` → `{until_label}` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: {time_range}
- Real model chat spans: `{len(spans)}`
- Models: {", ".join(f"`{m}`" for m in models) if models else "N/A"}
- Providers: {", ".join(f"`{p}`" for p in providers) if providers else "N/A"}
- APIs: {", ".join(f"`{a}`" for a in apis) if apis else "N/A"}
""")

    finish_lines = (
        "\n".join(f"  - `{count}` × `{reason}`" for reason, count in sorted(finish_counts.items()))
        or "  - (none)"
    )

    sections.append(f"""\
## 2. Executive Summary

- Total LLM calls: `{len(spans)}`
- Total input tokens: `{total_in if total_in else "—"}`
- Total output tokens: `{total_out if total_out else "—"}`
- Input/output ratio: `{io_ratio}` to 1
- Tool-call finish: `{finish_counts.get("tool_call", 0)}` ({_pct(finish_counts.get("tool_call", 0), len(spans))})
- Stop finish: `{finish_counts.get("stop", 0)}` ({_pct(finish_counts.get("stop", 0), len(spans))})
- Length-truncated: `{finish_counts.get("length", 0)}`
- Spans with thinking blocks: `{thinking_spans}` ({_pct(thinking_spans, len(spans))})
- Finish reasons:
{finish_lines}
""")

    # Per-model breakdown
    by_model: dict[str, list[ChatSpan]] = defaultdict(list)
    for span in spans:
        by_model[span.model or "unknown"].append(span)

    model_rows = []
    for model, mspans in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        m_durs = sorted(s.duration_ms for s in mspans)
        m_in = sum(s.input_tokens for s in mspans if s.input_tokens is not None)
        m_out = sum(s.output_tokens for s in mspans if s.output_tokens is not None)
        m_p50 = _percentile(m_durs, 50)
        m_p95 = _percentile(m_durs, 95)
        m_ratio = f"{m_in / m_out:.1f}" if m_out > 0 else "—"
        model_rows.append(
            f"| `{model}` | {len(mspans)} | {_pct(len(mspans), len(spans))}"
            f" | {m_in if m_in else '—'} | {m_out if m_out else '—'} | {m_ratio}"
            f" | {_dur_s(m_p50)} | {_dur_s(m_p95)} |"
        )

    model_table = (
        "| Model | Calls | % | In Tokens | Out Tokens | I/O Ratio | p50 Latency | p95 Latency |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n" + "\n".join(model_rows)
    )

    sections.append(f"""\
## 3. Per-Model Breakdown

{model_table}
""")

    def _latency_block(label: str, subset: list[ChatSpan]) -> str:
        if not subset:
            return f"**{label}** (n=0): no data\n"
        sdurs = sorted(s.duration_ms for s in subset)
        return (
            f"**{label}** (n={len(subset)})\n"
            f"- Min: `{_dur_s(min(sdurs))}`\n"
            f"- p50: `{_dur_s(_percentile(sdurs, 50))}`\n"
            f"- p95: `{_dur_s(_percentile(sdurs, 95))}`\n"
            f"- Max: `{_dur_s(max(sdurs))}`\n"
            f"- Mean: `{_dur_s(statistics.mean(sdurs))}`\n"
            f"- StdDev: `{_dur_s(statistics.stdev(sdurs) if len(sdurs) > 1 else 0)}`"
        )

    thinking_spans_list = [s for s in spans if s.has_thinking]
    no_thinking_spans_list = [s for s in spans if not s.has_thinking]

    latency_section = "\n\n".join(
        [
            _latency_block("All spans", spans),
            _latency_block("Reasoning mode (thinking blocks present)", thinking_spans_list),
            _latency_block("No-reason mode (no thinking blocks)", no_thinking_spans_list),
        ]
    )

    tps_lines = ""
    if throughputs:
        tps_lines = (
            f"- Median tokens/s: `{statistics.median(throughputs):.1f}`\n"
            f"- Max tokens/s: `{max(throughputs):.1f}`\n"
            f"- Mean tokens/s: `{statistics.mean(throughputs):.1f}`"
        )

    sections.append(f"""\
## 4. Latency Profile

{latency_section}

## 5. Throughput

{tps_lines if tps_lines else "— (no token data)"}
""")

    # Orchestration
    if orch:
        orch_rows = "\n".join(f"| `{row['name']}` | {row['count']} |" for row in orch)
        orch_table = "| Event | Count |\n|---|---:|\n" + orch_rows
    else:
        orch_table = "_No orchestration spans found in this time range._"

    sections.append(f"""\
## 6. Orchestration Events

{orch_table}
""")

    # Tool usage
    if tools:
        tool_rows_md = "\n".join(f"| `{row['name']}` | {row['count']} |" for row in tools)
        tool_table = "| Tool | Calls |\n|---|---:|\n" + tool_rows_md
    else:
        tool_table = "_No tool spans found in this time range._"

    sections.append(f"""\
## 7. Tool Execution Profile

{tool_table}
""")

    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB,
        help="OTel trace DB (default: ~/.co-cli/co-cli-logs.db)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help="output directory (default: docs/)",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="include spans from this date (UTC, inclusive)",
    )
    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="include spans up to this date (UTC, inclusive)",
    )
    args = parser.parse_args()

    db_path = args.db.resolve()
    out_dir = args.out.resolve()

    since_ns: int | None = None
    until_ns: int | None = None
    if args.since:
        since_ns = int(
            datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1e9
        )
    if args.until:
        # end of the given day
        until_ns = int(
            (datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 86400 - 1)
            * 1e9
        )

    print(f"Querying: {db_path}")
    spans, orch, tools = _query_spans(db_path, since_ns, until_ns)
    print(
        f"  {len(spans)} real chat spans, {len(orch)} orchestration event types, {len(tools)} tools"
    )

    if not spans and not orch:
        raise SystemExit(
            "No spans found. Check --since/--until or whether the DB has production spans."
        )

    report = _generate_report(spans, orch, tools, db_path, since_ns, until_ns)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"REPORT-llm-runtime-stats-{stamp}.md"
    out_path.write_text(report)
    print(f"Written:  {out_path}")


if __name__ == "__main__":
    main()
