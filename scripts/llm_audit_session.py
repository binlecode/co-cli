#!/usr/bin/env python3
"""Audit LLM session health from the OTel trace DB.

Queries production co-cli co.turn, ctx_overflow_check, and restore_session spans and writes
docs/REPORT-llm-audit-session-YYYYMMDD-HHMMSS.md.

Usage:
    uv run python scripts/llm_audit_session.py
    uv run python scripts/llm_audit_session.py --since 2026-04-01
    uv run python scripts/llm_audit_session.py --since 2026-04-01 --until 2026-04-30
    uv run python scripts/llm_audit_session.py --db ~/.co-cli/co-cli-logs.db --out docs/
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


class SessionSpan(NamedTuple):
    name: str
    duration_ms: float
    input_tokens: int | None
    output_tokens: int | None
    outcome: str | None
    interrupted: bool | None
    has_error: bool
    http_status: int | None
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


def _parse_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return None


def _parse_session_attrs(
    attrs: dict,
) -> tuple[int | None, int | None, str | None, bool | None, int | None, bool]:
    """Extract session span fields from an attributes dict."""
    input_tokens = _parse_optional_int(attrs.get("turn.input_tokens"))
    output_tokens = _parse_optional_int(attrs.get("turn.output_tokens"))
    outcome: str | None = attrs.get("turn.outcome")
    interrupted = _parse_optional_bool(attrs.get("turn.interrupted"))
    http_status = _parse_optional_int(attrs.get("http.status_code"))
    has_error = (
        attrs.get("error") is not None
        or attrs.get("provider_error") is not None
        or (http_status is not None and http_status >= 400)
        or (outcome is not None and outcome != "success")
    )
    return input_tokens, output_tokens, outcome, interrupted, http_status, has_error


def _query_spans(
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> list[SessionSpan]:
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
            WHERE name IN ('co.turn', 'ctx_overflow_check', 'restore_session')
              AND {_PROD_FILTER}
              {time_filter}
            ORDER BY start_time
            """,
            params,
        ).fetchall()

    spans: list[SessionSpan] = []
    for name, duration_ms, attributes_json, start_time_ns in rows:
        try:
            attrs = json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            attrs = {}

        input_tokens, output_tokens, outcome, interrupted, http_status, has_error = (
            _parse_session_attrs(attrs)
        )
        spans.append(
            SessionSpan(
                name=name,
                duration_ms=duration_ms or 0.0,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                outcome=outcome,
                interrupted=interrupted,
                has_error=has_error,
                http_status=http_status,
                start_time_ns=start_time_ns or 0,
            )
        )

    return spans


def _session_depth_summary(spans: list[SessionSpan]) -> str:
    """Compute session depth (turns per session) and return formatted summary lines."""
    all_by_time = sorted(spans, key=lambda s: s.start_time_ns)
    session_turn_counts: list[int] = []
    current_turn_count = 0
    for span in all_by_time:
        if span.name == "restore_session":
            if current_turn_count > 0:
                session_turn_counts.append(current_turn_count)
            current_turn_count = 0
        elif span.name == "co.turn":
            current_turn_count += 1
    if current_turn_count > 0:
        session_turn_counts.append(current_turn_count)

    if not session_turn_counts:
        return "- No session depth data available."
    sorted_depths = sorted(session_turn_counts)
    return (
        f"- Sessions with turn data: `{len(sorted_depths)}`\n"
        f"- p50 turns/session: `{_percentile(sorted_depths, 50):.1f}`\n"
        f"- p95 turns/session: `{_percentile(sorted_depths, 95):.1f}`\n"
        f"- Max turns/session: `{max(sorted_depths)}`\n"
        f"- Min turns/session: `{min(sorted_depths)}`"
    )


def _token_dist_summary(vals: list[int], label: str) -> str:
    """Format a p50/p95/max summary for a list of token counts."""
    if not vals:
        return f"  - (no {label} data)"
    return (
        f"  - p50: `{int(_percentile(vals, 50))}`\n"
        f"  - p95: `{int(_percentile(vals, 95))}`\n"
        f"  - Max: `{max(vals)}`"
    )


def _generate_report(
    spans: list[SessionSpan],
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

    turn_spans = [s for s in spans if s.name == "co.turn"]
    restore_spans = [s for s in spans if s.name == "restore_session"]
    overflow_spans = [s for s in spans if s.name == "ctx_overflow_check"]

    # Sessions = groups delimited by restore_session events (restore_session count + 1, or 0 if no turns)
    session_count = (len(restore_spans) + 1) if turn_spans else 0

    sections: list[str] = []

    # §1 Scope
    sections.append(f"""\
# REPORT: LLM Session Health Audit

**Date:** {today}
**Source:** `{db_path}`
**Filter:** `{since_label}` → `{until_label}` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: {time_range}
- Total turns (co.turn): `{len(turn_spans)}`
- Total sessions (restore_session + 1): `{session_count}`
- Context overflow checks: `{len(overflow_spans)}`
""")

    # §2 Provider Reliability
    error_turns = [s for s in turn_spans if s.has_error]
    status_counts: dict[int, int] = defaultdict(int)
    for s in turn_spans:
        if s.http_status is not None and s.http_status >= 400:
            status_counts[s.http_status] += 1

    outcome_counts: dict[str, int] = defaultdict(int)
    for s in turn_spans:
        if s.outcome is not None:
            outcome_counts[s.outcome] += 1

    status_lines = (
        "\n".join(f"  - HTTP `{code}`: `{count}`" for code, count in sorted(status_counts.items()))
        or "  - (none detected)"
    )

    outcome_lines = (
        "\n".join(
            f"  - `{outcome}`: `{count}`"
            for outcome, count in sorted(outcome_counts.items(), key=lambda kv: -kv[1])
        )
        or "  - (no outcome attribute data)"
    )

    sections.append(f"""\
## 2. Provider Reliability

- Turns with error indicators: `{len(error_turns)}` ({_pct(len(error_turns), len(turn_spans))})
- HTTP error status breakdown:
{status_lines}
- Turn outcome breakdown:
{outcome_lines}
""")

    # §3 Context Pressure
    overflow_rate = len(overflow_spans) / session_count if session_count > 0 else 0.0

    sections.append(f"""\
## 3. Context Pressure

- ctx_overflow_check spans: `{len(overflow_spans)}`
- Sessions (restore_session + 1): `{session_count}`
- Overflow checks per session: `{overflow_rate:.2f}`
""")

    # §4 Session Depth
    sections.append(f"""\
## 4. Session Depth

{_session_depth_summary(spans)}
""")

    # §5 Token Accumulation — per-turn input/output token distribution
    in_tokens = sorted(s.input_tokens for s in turn_spans if s.input_tokens is not None)
    out_tokens = sorted(s.output_tokens for s in turn_spans if s.output_tokens is not None)

    sections.append(f"""\
## 5. Token Accumulation

- Input tokens per turn (n={len(in_tokens)}):
{_token_dist_summary(in_tokens, "turn.input_tokens")}
- Output tokens per turn (n={len(out_tokens)}):
{_token_dist_summary(out_tokens, "turn.output_tokens")}
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
    turn_count = sum(1 for s in spans if s.name == "co.turn")
    restore_count = sum(1 for s in spans if s.name == "restore_session")
    overflow_count = sum(1 for s in spans if s.name == "ctx_overflow_check")
    print(
        f"  {turn_count} co.turn, {restore_count} restore_session,"
        f" {overflow_count} ctx_overflow_check"
    )

    report = _generate_report(spans, db_path, since_ns, until_ns)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"REPORT-llm-audit-session-{stamp}.md"
    out_path.write_text(report)
    print(f"Written:  {out_path}")


if __name__ == "__main__":
    main()
