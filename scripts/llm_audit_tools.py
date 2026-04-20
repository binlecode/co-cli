#!/usr/bin/env python3
"""Audit LLM tool usage from the OTel trace DB.

Queries production co-cli execute_tool spans and writes
docs/REPORT-llm-audit-tools-YYYYMMDD-HHMMSS.md.

Usage:
    uv run python scripts/llm_audit_tools.py
    uv run python scripts/llm_audit_tools.py --since 2026-04-01
    uv run python scripts/llm_audit_tools.py --since 2026-04-01 --until 2026-04-30
    uv run python scripts/llm_audit_tools.py --db ~/.co-cli/co-cli-logs.db --out docs/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DB = Path.home() / ".co-cli" / "co-cli-logs.db"
_DEFAULT_OUT = _REPO_ROOT / "docs"

_PROD_FILTER = (
    '(resource LIKE \'%"service.name": "co-cli"%\''
    " AND resource NOT LIKE '%co-cli-pytest%'"
    " AND resource NOT LIKE '%co-cli-eval%')"
)


class ToolSpan(NamedTuple):
    tool_name: str
    duration_ms: float
    result_size: int | None
    requires_approval: bool | None
    source: str | None
    rag_backend: str | None
    is_error: bool
    start_time_ns: int


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * pct / 100
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _pct(num: int, den: int) -> str:
    return f"{num / den * 100:.1f}%" if den > 0 else "—"


def _dur_s(ms: float) -> str:
    return f"{ms / 1000:.3f}s"


def _query_spans(
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> list[ToolSpan]:
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

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT name, duration_ms, attributes, start_time
            FROM spans
            WHERE name LIKE 'execute_tool %'
              AND {_PROD_FILTER}
              {time_filter}
            ORDER BY start_time
            """,
            params,
        ).fetchall()

    spans: list[ToolSpan] = []
    for name, duration_ms, attributes_json, start_time_ns in rows:
        try:
            attrs = json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            attrs = {}

        tool_name = name.removeprefix("execute_tool ")

        result_size_raw = attrs.get("co.tool.result_size")
        result_size: int | None = None
        if result_size_raw is not None:
            try:
                result_size = int(result_size_raw)
            except (ValueError, TypeError):
                pass

        approval_raw = attrs.get("co.tool.requires_approval")
        requires_approval: bool | None = None
        if approval_raw is not None:
            if isinstance(approval_raw, bool):
                requires_approval = approval_raw
            elif isinstance(approval_raw, str):
                requires_approval = approval_raw.lower() in ("true", "1", "yes")

        source: str | None = attrs.get("co.tool.source")
        rag_backend: str | None = attrs.get("rag.backend")

        status_code = attrs.get("status_code", "")
        is_error = str(status_code).upper() == "ERROR"

        spans.append(
            ToolSpan(
                tool_name=tool_name,
                duration_ms=duration_ms or 0.0,
                result_size=result_size,
                requires_approval=requires_approval,
                source=source,
                rag_backend=rag_backend,
                is_error=is_error,
                start_time_ns=start_time_ns or 0,
            )
        )

    return spans


def _generate_report(
    spans: list[ToolSpan],
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> str:
    today = date.today().isoformat()

    since_label = (
        datetime.fromtimestamp(since_ns / 1e9, tz=UTC).strftime("%Y-%m-%d")
        if since_ns
        else "all time"
    )
    until_label = (
        datetime.fromtimestamp(until_ns / 1e9, tz=UTC).strftime("%Y-%m-%d") if until_ns else "now"
    )

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

    distinct_tools = sorted({s.tool_name for s in spans})

    sections: list[str] = []

    # §1 Scope
    sections.append(f"""\
# REPORT: LLM Tool Usage Audit

**Date:** {today}
**Source:** `{db_path}`
**Filter:** `{since_label}` → `{until_label}` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: {time_range}
- Total tool calls: `{len(spans)}`
- Distinct tools: `{len(distinct_tools)}`
- Tools seen: {", ".join(f"`{t}`" for t in distinct_tools) if distinct_tools else "N/A"}
""")

    # §2 Error Rate by Tool
    by_tool: dict[str, list[ToolSpan]] = defaultdict(list)
    for span in spans:
        by_tool[span.tool_name].append(span)

    error_rows = []
    for tool_name in sorted(by_tool):
        tool_spans = by_tool[tool_name]
        error_count = sum(1 for s in tool_spans if s.is_error)
        error_rows.append(
            f"| `{tool_name}` | {len(tool_spans)} | {error_count}"
            f" | {_pct(error_count, len(tool_spans))} |"
        )

    total_errors = sum(1 for s in spans if s.is_error)
    error_table = "| Tool | Calls | Errors | Error Rate |\n|---|---:|---:|---:|\n" + "\n".join(
        error_rows
    )

    sections.append(f"""\
## 2. Error Rate by Tool

- Total errors: `{total_errors}` ({_pct(total_errors, len(spans))})

{error_table}
""")

    # §3 Latency by Tool (top 15 by call count)
    top_tools = sorted(by_tool.items(), key=lambda kv: -len(kv[1]))[:15]
    latency_rows = []
    for tool_name, tool_spans in top_tools:
        durs = sorted(s.duration_ms for s in tool_spans)
        latency_rows.append(
            f"| `{tool_name}` | {len(tool_spans)}"
            f" | {_dur_s(_percentile(durs, 50))}"
            f" | {_dur_s(_percentile(durs, 95))}"
            f" | {_dur_s(max(durs))} |"
        )

    latency_table = "| Tool | Calls | p50 | p95 | Max |\n|---|---:|---:|---:|---:|\n" + "\n".join(
        latency_rows
    )

    sections.append(f"""\
## 3. Latency by Tool

{latency_table}
""")

    # §4 Result Size Distribution
    sizes_with_tool: list[tuple[str, int]] = [
        (s.tool_name, s.result_size) for s in spans if s.result_size is not None
    ]
    sizes_all = sorted(sz for _, sz in sizes_with_tool)

    if sizes_all:
        size_summary = (
            f"- Spans with result_size: `{len(sizes_all)}`\n"
            f"- p50: `{int(_percentile(sizes_all, 50))}` bytes\n"
            f"- p95: `{int(_percentile(sizes_all, 95))}` bytes\n"
            f"- Max: `{max(sizes_all)}` bytes\n"
        )

        sizes_by_tool: dict[str, list[int]] = defaultdict(list)
        for tool_name, sz in sizes_with_tool:
            sizes_by_tool[tool_name].append(sz)

        size_tool_rows = []
        for tool_name in sorted(sizes_by_tool, key=lambda t: -len(sizes_by_tool[t])):
            tool_sizes = sorted(sizes_by_tool[tool_name])
            size_tool_rows.append(
                f"| `{tool_name}` | {len(tool_sizes)}"
                f" | {int(_percentile(tool_sizes, 50))}"
                f" | {int(_percentile(tool_sizes, 95))}"
                f" | {max(tool_sizes)} |"
            )

        size_table = (
            "| Tool | n | p50 (bytes) | p95 (bytes) | Max (bytes) |\n"
            "|---|---:|---:|---:|---:|\n" + "\n".join(size_tool_rows)
        )
    else:
        size_summary = "- No `co.tool.result_size` attribute found in spans.\n"
        size_table = "_No result_size data available._"

    sections.append(f"""\
## 4. Result Size Distribution

{size_summary}
{size_table}
""")

    # §5 Approval & Source Profile
    approval_spans = [s for s in spans if s.requires_approval is not None]
    approved_count = sum(1 for s in approval_spans if s.requires_approval)
    mcp_count = sum(1 for s in spans if s.source == "mcp")
    native_count = len(spans) - mcp_count

    sections.append(f"""\
## 5. Approval & Source Profile

- Spans with requires_approval attribute: `{len(approval_spans)}`
- Requires approval: `{approved_count}` ({_pct(approved_count, len(approval_spans))})
- MCP tools: `{mcp_count}` ({_pct(mcp_count, len(spans))})
- Native tools: `{native_count}` ({_pct(native_count, len(spans))})
""")

    # §6 RAG Backend Distribution
    rag_spans = [s for s in spans if s.rag_backend is not None]
    if rag_spans:
        rag_counts: dict[str, int] = defaultdict(int)
        for span in rag_spans:
            rag_counts[span.rag_backend or "unknown"] += 1

        rag_rows = "\n".join(
            f"| `{backend}` | {count} | {_pct(count, len(rag_spans))} |"
            for backend, count in sorted(rag_counts.items(), key=lambda kv: -kv[1])
        )
        rag_table = "| Backend | Calls | Share |\n|---|---:|---:|\n" + rag_rows
    else:
        rag_table = "_No spans with `rag.backend` attribute found._"

    sections.append(f"""\
## 6. RAG Backend Distribution

- Total RAG spans: `{len(rag_spans)}`

{rag_table}
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
        until_ns = int(
            (datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 86400 - 1)
            * 1e9
        )

    print(f"Querying: {db_path}")
    spans = _query_spans(db_path, since_ns, until_ns)
    print(f"  {len(spans)} tool spans found")

    report = _generate_report(spans, db_path, since_ns, until_ns)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"REPORT-llm-audit-tools-{stamp}.md"
    out_path.write_text(report)
    print(f"Written:  {out_path}")


if __name__ == "__main__":
    main()
