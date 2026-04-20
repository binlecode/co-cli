#!/usr/bin/env python3
"""Audit LLM role delegation from the OTel trace DB.

Queries production co-cli spans with agent.role attribute (subagent invocations) and writes
docs/REPORT-llm-audit-roles-YYYYMMDD-HHMMSS.md.

Usage:
    uv run python scripts/llm_audit_roles.py
    uv run python scripts/llm_audit_roles.py --since 2026-04-01
    uv run python scripts/llm_audit_roles.py --since 2026-04-01 --until 2026-04-30
    uv run python scripts/llm_audit_roles.py --db ~/.co-cli/co-cli-logs.db --out docs/
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


class RoleSpan(NamedTuple):
    role: str
    model: str | None
    duration_ms: float
    requests_used: int | None
    request_limit: int | None
    input_tokens: int | None
    output_tokens: int | None
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


def _parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]  # value is checked non-None above; int() accepts many numeric types
    except (ValueError, TypeError):
        return None


def _parse_row_attrs(attrs: dict) -> RoleSpan | None:
    """Build a RoleSpan from a parsed attributes dict; return None if agent.role missing."""
    role: str | None = attrs.get("agent.role")
    if not role:
        return None
    return RoleSpan(
        role=role,
        model=attrs.get("agent.model"),
        duration_ms=0.0,
        requests_used=_parse_optional_int(attrs.get("agent.requests_used")),
        request_limit=_parse_optional_int(attrs.get("agent.request_limit")),
        input_tokens=_parse_optional_int(attrs.get("gen_ai.usage.input_tokens")),
        output_tokens=_parse_optional_int(attrs.get("gen_ai.usage.output_tokens")),
        start_time_ns=0,
    )


def _query_spans(
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> list[RoleSpan]:
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
            WHERE attributes LIKE '%"agent.role"%'
              AND {_PROD_FILTER}
              {time_filter}
            ORDER BY start_time
            """,
            params,
        ).fetchall()

    spans: list[RoleSpan] = []
    for _name, duration_ms, attributes_json, start_time_ns in rows:
        try:
            attrs = json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            attrs = {}

        span = _parse_row_attrs(attrs)
        if span is None:
            continue
        spans.append(
            span._replace(
                duration_ms=duration_ms or 0.0,
                start_time_ns=start_time_ns or 0,
            )
        )

    return spans


def _generate_report(
    spans: list[RoleSpan],
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

    distinct_roles = sorted({s.role for s in spans})

    sections: list[str] = []

    # §1 Scope
    sections.append(f"""\
# REPORT: LLM Role Delegation Audit

**Date:** {today}
**Source:** `{db_path}`
**Filter:** `{since_label}` → `{until_label}` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: {time_range}
- Total role invocations: `{len(spans)}`
- Distinct roles: `{len(distinct_roles)}`
- Roles seen: {", ".join(f"`{r}`" for r in distinct_roles) if distinct_roles else "N/A"}
""")

    # §2 Role Usage
    by_role: dict[str, list[RoleSpan]] = defaultdict(list)
    for span in spans:
        by_role[span.role].append(span)

    usage_rows = []
    for role in sorted(by_role, key=lambda r: -len(by_role[r])):
        role_spans = by_role[role]
        models = sorted({s.model for s in role_spans if s.model})
        model_label = ", ".join(f"`{m}`" for m in models) if models else "—"
        usage_rows.append(
            f"| `{role}` | {len(role_spans)} | {_pct(len(role_spans), len(spans))} | {model_label} |"
        )

    usage_table = "| Role | Invocations | Share | Models |\n|---|---:|---:|---|\n" + "\n".join(
        usage_rows
    )

    sections.append(f"""\
## 2. Role Usage

{usage_table}
""")

    # §3 Request Limit Saturation — requests_used / request_limit
    saturation_rows = []
    flagged_roles: list[str] = []

    for role in sorted(by_role):
        role_spans = by_role[role]
        saturation_vals = []
        for span in role_spans:
            if (
                span.requests_used is not None
                and span.request_limit is not None
                and span.request_limit > 0
            ):
                saturation_vals.append(span.requests_used / span.request_limit)

        if not saturation_vals:
            saturation_rows.append(f"| `{role}` | {len(role_spans)} | — | — | — | — |")
            continue

        sorted_sat = sorted(saturation_vals)
        p50 = _percentile(sorted_sat, 50)
        p95 = _percentile(sorted_sat, 95)
        max_sat = max(sorted_sat)
        flag = " ⚠" if p95 > 0.8 else ""
        if p95 > 0.8:
            flagged_roles.append(role)

        saturation_rows.append(
            f"| `{role}` | {len(role_spans)} | {len(saturation_vals)}"
            f" | {p50:.2f} | {p95:.2f}{flag} | {max_sat:.2f} |"
        )

    saturation_table = (
        "| Role | Total Spans | Spans w/ Data | p50 Sat | p95 Sat | Max Sat |\n"
        "|---|---:|---:|---:|---:|---:|\n" + "\n".join(saturation_rows)
    )

    flagged_note = (
        f"\n**Flagged (p95 > 0.8):** {', '.join(f'`{r}`' for r in flagged_roles)}"
        if flagged_roles
        else "\n_No roles with p95 saturation > 0.8._"
    )

    sections.append(f"""\
## 3. Request Limit Saturation

{saturation_table}
{flagged_note}
""")

    # §4 Per-Role Latency
    latency_rows = []
    for role in sorted(by_role, key=lambda r: -len(by_role[r])):
        role_spans = by_role[role]
        durs = sorted(s.duration_ms for s in role_spans)
        latency_rows.append(
            f"| `{role}` | {len(role_spans)}"
            f" | {_dur_s(_percentile(durs, 50))}"
            f" | {_dur_s(_percentile(durs, 95))}"
            f" | {_dur_s(max(durs))} |"
        )

    latency_table = (
        "| Role | Invocations | p50 | p95 | Max |\n"
        "|---|---:|---:|---:|---:|\n" + "\n".join(latency_rows)
    )

    sections.append(f"""\
## 4. Per-Role Latency

{latency_table}
""")

    # §5 Per-Role Token Cost
    token_rows = []
    for role in sorted(by_role, key=lambda r: -len(by_role[r])):
        role_spans = by_role[role]
        in_vals = sorted(s.input_tokens for s in role_spans if s.input_tokens is not None)
        out_vals = sorted(s.output_tokens for s in role_spans if s.output_tokens is not None)

        in_p50 = int(_percentile(in_vals, 50)) if in_vals else None
        out_p50 = int(_percentile(out_vals, 50)) if out_vals else None

        total_in = sum(in_vals)
        total_out = sum(out_vals)
        efficiency = f"{total_out / total_in:.3f}" if total_in > 0 and total_out > 0 else "—"

        token_rows.append(
            f"| `{role}` | {len(role_spans)}"
            f" | {in_p50 if in_p50 is not None else '—'}"
            f" | {out_p50 if out_p50 is not None else '—'}"
            f" | {total_in if in_vals else '—'}"
            f" | {total_out if out_vals else '—'}"
            f" | {efficiency} |"
        )

    token_table = (
        "| Role | n | p50 In | p50 Out | Total In | Total Out | Out/In Ratio |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n" + "\n".join(token_rows)
    )

    sections.append(f"""\
## 5. Per-Role Token Cost

{token_table}
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
    distinct_roles = {s.role for s in spans}
    print(f"  {len(spans)} role delegation spans, {len(distinct_roles)} distinct roles")

    report = _generate_report(spans, db_path, since_ns, until_ns)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"REPORT-llm-audit-roles-{stamp}.md"
    out_path.write_text(report)
    print(f"Written:  {out_path}")


if __name__ == "__main__":
    main()
