#!/usr/bin/env python3
"""Spill-size calibration report from OTel traces and on-disk artifacts.

Builds the data needed to evaluate or retune ``SPILL_THRESHOLD_CHARS`` (4,000)
and per-tool ``spill_threshold_chars`` overrides. Pulls real-world signals
from production traces — no synthetic data.

Inputs:
  1. SQLite spans DB (``~/.co-cli/co-cli-logs.db``):
     - ``tool_budget.spill_tool_result``  — per-tool result size + spill outcome
     - ``tool_budget.spill_largest_tool_results`` — per-request aggregate trigger
     - ``co.tool``                        — args size (``co.tool.args_chars``),
                                            spill re-fetch attempts on file_read
     - ``co.turn``                        — user prompt size (``co.user_prompt.chars``)
  2. Tool-results spill directory (``~/.co-cli/tool-results/``):
     - on-disk ``<hash>.txt`` artifacts — actual persisted spills

Outputs (under ``docs/`` by default):
  REPORT-spill-calibration-YYYYMMDD-HHMMSS.md

Usage:
    uv run python scripts/calibrate_spill_size.py
    uv run python scripts/calibrate_spill_size.py --since 2026-04-01
    uv run python scripts/calibrate_spill_size.py --db ~/.co-cli/co-cli-logs.db --out docs/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DB = Path.home() / ".co-cli" / "co-cli-logs.db"
_DEFAULT_SPILL_DIR = Path.home() / ".co-cli" / "tool-results"
_DEFAULT_OUT = _REPO_ROOT / "docs"

# Production-service filter: exclude pytest and eval runs from calibration.
# Matches the convention used by scripts/llm_runtime_stats.py.
_PROD_FILTER = (
    '(resource LIKE \'%"service.name": "co-cli"%\''
    " AND resource NOT LIKE '%co-cli-pytest%'"
    " AND resource NOT LIKE '%co-cli-eval%')"
)


def _percentiles(samples: list[int], pcts: tuple[int, ...] = (50, 90, 95, 99)) -> dict[int, int]:
    """Return integer percentiles for a list of samples. Empty input yields zeros."""
    if not samples:
        return dict.fromkeys(pcts, 0)
    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    out: dict[int, int] = {}
    for p in pcts:
        idx = max(0, min(n - 1, round((p / 100.0) * (n - 1))))
        out[p] = sorted_samples[idx]
    return out


def _attr(row_attributes_json: str, key: str) -> object | None:
    try:
        attrs = json.loads(row_attributes_json or "{}")
    except json.JSONDecodeError:
        return None
    return attrs.get(key)


def _build_query(
    base_where: str, *, since_micros: int | None, include_pytest: bool
) -> tuple[str, tuple]:
    """Compose SELECT against spans table with shared filters.

    base_where supplies the span-specific predicate (e.g. name match or attribute LIKE).
    Adds optional time bound and the production service filter unless include_pytest is set.
    """
    sql = f"SELECT attributes FROM spans WHERE {base_where}"
    params: list[int] = []
    if not include_pytest:
        sql += f" AND {_PROD_FILTER}"
    if since_micros is not None:
        sql += " AND start_time >= ?"
        params.append(since_micros)
    return sql, tuple(params)


def _query_l1_spans(
    conn: sqlite3.Connection, since_micros: int | None, *, include_pytest: bool
) -> list[dict]:
    """Per-result spill spans — full size distribution including non-spilled cases."""
    sql, params = _build_query(
        "name = 'tool_budget.spill_tool_result'",
        since_micros=since_micros,
        include_pytest=include_pytest,
    )
    rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for (attrs_json,) in rows:
        try:
            out.append(json.loads(attrs_json or "{}"))
        except json.JSONDecodeError:
            continue
    return out


def _query_l2_spans(
    conn: sqlite3.Connection, since_micros: int | None, *, include_pytest: bool
) -> list[dict]:
    """Per-request spill_largest_tool_results spans — aggregate trigger frequency."""
    sql, params = _build_query(
        "name = 'tool_budget.spill_largest_tool_results'",
        since_micros=since_micros,
        include_pytest=include_pytest,
    )
    rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for (attrs_json,) in rows:
        try:
            out.append(json.loads(attrs_json or "{}"))
        except json.JSONDecodeError:
            continue
    return out


def _query_tool_args_spans(
    conn: sqlite3.Connection, since_micros: int | None, *, include_pytest: bool
) -> list[dict]:
    """Tool execution spans carrying co.tool.args_chars."""
    sql, params = _build_query(
        "attributes LIKE '%co.tool.args_chars%' AND attributes NOT LIKE '%spill.content_chars%'",
        since_micros=since_micros,
        include_pytest=include_pytest,
    )
    rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for (attrs_json,) in rows:
        try:
            attrs = json.loads(attrs_json or "{}")
        except json.JSONDecodeError:
            continue
        if "co.tool.args_chars" in attrs:
            out.append(attrs)
    return out


def _query_user_prompt_spans(
    conn: sqlite3.Connection, since_micros: int | None, *, include_pytest: bool
) -> list[int]:
    """co.turn spans carrying co.user_prompt.chars."""
    sql, params = _build_query(
        "name = 'co.turn'",
        since_micros=since_micros,
        include_pytest=include_pytest,
    )
    rows = conn.execute(sql, params).fetchall()
    sizes: list[int] = []
    for (attrs_json,) in rows:
        try:
            attrs = json.loads(attrs_json or "{}")
        except json.JSONDecodeError:
            continue
        v = attrs.get("co.user_prompt.chars")
        if isinstance(v, int):
            sizes.append(v)
    return sizes


def _query_refetch_attempts(
    conn: sqlite3.Connection, since_micros: int | None, *, include_pytest: bool
) -> tuple[int, int]:
    """Return (total_file_read_calls, refetch_attempts)."""
    sql, params = _build_query(
        "attributes LIKE '%co.tool.spill_refetch_attempt%'",
        since_micros=since_micros,
        include_pytest=include_pytest,
    )
    rows = conn.execute(sql, params).fetchall()
    total = 0
    attempts = 0
    for (attrs_json,) in rows:
        try:
            attrs = json.loads(attrs_json or "{}")
        except json.JSONDecodeError:
            continue
        flag = attrs.get("co.tool.spill_refetch_attempt")
        if flag is None:
            continue
        total += 1
        if flag is True or flag == "true":
            attempts += 1
    return total, attempts


def _scan_disk_artifacts(spill_dir: Path) -> tuple[int, list[int]]:
    """Return (file_count, list_of_byte_sizes) under spill_dir."""
    if not spill_dir.is_dir():
        return 0, []
    sizes: list[int] = []
    for entry in spill_dir.iterdir():
        if entry.is_file() and entry.suffix == ".txt" and "tmp" not in entry.name:
            try:
                sizes.append(entry.stat().st_size)
            except OSError:
                continue
    return len(sizes), sizes


def _aggregate_per_tool(l1_spans: list[dict]) -> dict[str, dict]:
    """Group L1 spans by tool name and compute per-tool size statistics."""
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for s in l1_spans:
        tool = s.get("tool.name") or "<unknown>"
        by_tool[str(tool)].append(s)

    out: dict[str, dict] = {}
    for tool, spans in by_tool.items():
        sizes = [int(s.get("spill.content_chars") or 0) for s in spans]
        fired = sum(1 for s in spans if s.get("spill.fired") is True)
        forced = sum(1 for s in spans if s.get("spill.forced") is True)
        savings = [int(s.get("spill.savings_chars") or 0) for s in spans if s.get("spill.fired")]
        thresholds = {int(s.get("spill.threshold_chars") or 0) for s in spans}
        out[tool] = {
            "calls": len(spans),
            "spilled": fired,
            "forced": forced,
            "spill_rate": fired / len(spans) if spans else 0.0,
            "size_p50": _percentiles(sizes)[50],
            "size_p90": _percentiles(sizes)[90],
            "size_p95": _percentiles(sizes)[95],
            "size_p99": _percentiles(sizes)[99],
            "size_max": max(sizes) if sizes else 0,
            "size_mean": int(statistics.mean(sizes)) if sizes else 0,
            "savings_total": sum(savings),
            "thresholds_seen": sorted(thresholds),
        }
    return out


def _aggregate_l2(l2_spans: list[dict]) -> dict:
    """Aggregate spill_largest_tool_results span statistics."""
    skip_reasons: Counter[str] = Counter()
    pressure_before: list[int] = []
    pressure_after: list[int] = []
    spill_fired_count = 0
    spilled_counts: list[int] = []
    for s in l2_spans:
        reason = str(s.get("request.skip_reason") or "")
        skip_reasons[reason or "(success)"] += 1
        if isinstance(s.get("request.tokens_before"), int):
            pressure_before.append(s["request.tokens_before"])
        if isinstance(s.get("request.tokens_after"), int):
            pressure_after.append(s["request.tokens_after"])
        if s.get("request.spill_fired"):
            spill_fired_count += 1
            if isinstance(s.get("request.spilled_count"), int):
                spilled_counts.append(s["request.spilled_count"])
    return {
        "total_requests": len(l2_spans),
        "spill_fired": spill_fired_count,
        "skip_reasons": dict(skip_reasons),
        "pressure_before_p50": _percentiles(pressure_before)[50],
        "pressure_before_p95": _percentiles(pressure_before)[95],
        "pressure_after_p50": _percentiles(pressure_after)[50],
        "spilled_per_request_max": max(spilled_counts) if spilled_counts else 0,
    }


def _recommend_per_tool(per_tool: dict[str, dict], current_default: int = 4_000) -> dict[str, str]:
    """Produce per-tool threshold recommendations.

    Logic:
      - If p99 of size <= current_default x 1.1: leave at default (no benefit)
      - If spill_rate > 0.50 AND p50 size > current_default: tool routinely overflows;
        consider raising threshold to p90 OR exempting (math.inf) if it's a "load" tool
        (file_read pattern)
      - If spill_rate < 0.05 AND p99 size << current_default: leave at default
      - Otherwise: recommend a threshold ≈ p90 (covers normal use, spills outliers)
    """
    recs: dict[str, str] = {}
    for tool, stats in per_tool.items():
        p50, p90, p99 = stats["size_p50"], stats["size_p90"], stats["size_p99"]
        rate = stats["spill_rate"]
        if p99 <= int(current_default * 1.1):
            recs[tool] = f"keep default (4,000) — p99 ({p99:,}) within budget"
        elif rate > 0.5 and p50 > current_default:
            recs[tool] = (
                f"REVIEW: chronic overflow (spill_rate {rate:.0%}, p50 {p50:,}). "
                f"Consider raising to p90 ({p90:,}) or exempting if essential output."
            )
        elif rate < 0.05 and p99 < current_default:
            recs[tool] = f"keep default — low spill rate ({rate:.1%}), distribution well-bounded"
        else:
            recs[tool] = (
                f"consider per-tool override → ~{p90:,} chars (current p90); "
                f"current spill_rate {rate:.0%}"
            )
    return recs


def _format_report(
    *,
    db_path: Path,
    spill_dir: Path,
    since: str | None,
    include_pytest: bool,
    per_tool: dict[str, dict],
    l2: dict,
    args_spans: list[dict],
    user_prompt_sizes: list[int],
    refetch_total: int,
    refetch_attempts: int,
    disk_count: int,
    disk_sizes: list[int],
    recs: dict[str, str],
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    scope = (
        "production + pytest + eval"
        if include_pytest
        else "production only (excludes pytest, eval)"
    )
    lines: list[str] = []
    lines.append(f"# Spill-Size Calibration Report — {now}")
    lines.append("")
    lines.append(f"- DB: `{db_path}`")
    lines.append(f"- Spill dir: `{spill_dir}`")
    lines.append(f"- Since: `{since or 'all-time'}`")
    lines.append(f"- Scope: **{scope}**")
    lines.append("")

    lines.append("## L1 — per-tool result size distribution")
    lines.append("")
    lines.append(
        "| tool | calls | spill_rate | mean | p50 | p90 | p95 | p99 | max | savings_total |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for tool, s in sorted(per_tool.items(), key=lambda kv: -kv[1]["calls"]):
        lines.append(
            f"| `{tool}` | {s['calls']:,} | {s['spill_rate']:.0%} | "
            f"{s['size_mean']:,} | {s['size_p50']:,} | {s['size_p90']:,} | "
            f"{s['size_p95']:,} | {s['size_p99']:,} | {s['size_max']:,} | "
            f"{s['savings_total']:,} |"
        )
    lines.append("")

    lines.append("## L1 — recommendations")
    lines.append("")
    if not recs:
        lines.append("_(no L1 data)_")
    for tool, msg in sorted(recs.items()):
        lines.append(f"- **`{tool}`** — {msg}")
    lines.append("")

    lines.append("## L2 — spill_largest_tool_results aggregate")
    lines.append("")
    lines.append(f"- total requests scanned: **{l2['total_requests']:,}**")
    lines.append(f"- spill fired: **{l2['spill_fired']:,}**")
    lines.append(
        f"- pressure before (tokens) p50/p95: {l2['pressure_before_p50']:,} / {l2['pressure_before_p95']:,}"
    )
    lines.append(f"- pressure after (tokens) p50: {l2['pressure_after_p50']:,}")
    lines.append(f"- max parts spilled in one request: {l2['spilled_per_request_max']:,}")
    lines.append("")
    lines.append("Skip-reason breakdown:")
    for reason, count in sorted(l2["skip_reasons"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  - `{reason}`: {count:,}")
    lines.append("")

    lines.append("## Tool-call args size distribution")
    lines.append("")
    if args_spans:
        args_sizes = [int(s.get("co.tool.args_chars") or 0) for s in args_spans]
        pcts = _percentiles(args_sizes)
        lines.append(f"- samples: {len(args_sizes):,}")
        lines.append(
            f"- p50: {pcts[50]:,} | p90: {pcts[90]:,} | p95: {pcts[95]:,} | p99: {pcts[99]:,} | max: {max(args_sizes):,}"
        )
    else:
        lines.append(
            "_(no args data — telemetry recently added; data accumulates from this point)_"
        )
    lines.append("")

    lines.append("## User prompt size distribution")
    lines.append("")
    if user_prompt_sizes:
        pcts = _percentiles(user_prompt_sizes)
        lines.append(f"- samples: {len(user_prompt_sizes):,}")
        lines.append(
            f"- p50: {pcts[50]:,} | p90: {pcts[90]:,} | p95: {pcts[95]:,} | p99: {pcts[99]:,} | max: {max(user_prompt_sizes):,}"
        )
    else:
        lines.append("_(no user-prompt data)_")
    lines.append("")

    lines.append("## Spill re-fetch attempts (file_read on tool_results paths)")
    lines.append("")
    if refetch_total:
        rate = refetch_attempts / refetch_total
        lines.append(f"- file_read calls observed: {refetch_total:,}")
        lines.append(f"- spill re-fetch attempts: {refetch_attempts:,} ({rate:.1%})")
        if refetch_attempts:
            lines.append("")
            lines.append(
                "  _Note: re-fetch attempts currently fail at workspace boundary. "
                "A non-zero rate signals the agent **wants** to re-read spilled output — "
                "indicates spill threshold may be too tight or the boundary needs widening._"
            )
    else:
        lines.append("_(no file_read instrumentation data)_")
    lines.append("")

    lines.append("## On-disk spill artifacts")
    lines.append("")
    if disk_count:
        sizes_kb = [s // 1024 for s in disk_sizes]
        pcts = _percentiles(sizes_kb)
        total_mb = sum(disk_sizes) / (1024 * 1024)
        lines.append(f"- files: {disk_count:,}")
        lines.append(f"- total: {total_mb:.1f} MB")
        lines.append(
            f"- size (KB) p50/p95/p99/max: {pcts[50]} / {pcts[95]} / {pcts[99]} / {max(sizes_kb)}"
        )
    else:
        lines.append(f"_(no artifacts under `{spill_dir}`)_")
    lines.append("")

    lines.append("## Reading the report")
    lines.append("")
    lines.append(
        "- **High spill_rate + low savings_total** → threshold may be too tight; minor overflow from a slightly oversized tool."
    )
    lines.append(
        "- **High spill_rate + high savings_total** → tool routinely emits large output; consider per-tool override at p90."
    )
    lines.append(
        "- **Low spill_rate + p99 << threshold** → tool comfortably under budget; default fits."
    )
    lines.append(
        "- **L2 `fallback_to_summarize` dominates skip_reasons** → spill alone can't keep pressure under threshold; summarization is loaded too often → raise spill_ratio or lower spill_threshold."
    )
    lines.append(
        "- **Re-fetch attempt rate > 0** → agent is asking for spilled content; signals threshold cuts off useful detail."
    )
    lines.append("")
    return "\n".join(lines)


def _parse_since(since: str | None) -> int | None:
    if since is None:
        return None
    try:
        dt = datetime.fromisoformat(since).replace(tzinfo=UTC)
    except ValueError as e:
        raise SystemExit(f"--since must be ISO date/datetime, got {since!r}: {e}") from e
    return int(dt.timestamp() * 1_000_000_000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB, help="Path to spans SQLite DB")
    parser.add_argument(
        "--spill-dir", type=Path, default=_DEFAULT_SPILL_DIR, help="Tool-results spill directory"
    )
    parser.add_argument(
        "--since", type=str, default=None, help="ISO date/datetime lower bound (UTC)"
    )
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="Output directory")
    parser.add_argument(
        "--include-pytest",
        action="store_true",
        help="Include pytest/eval spans in calibration (default: production only)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB not found: {args.db}")

    since_ns = _parse_since(args.since)
    include_pytest = args.include_pytest

    with sqlite3.connect(args.db) as conn:
        l1 = _query_l1_spans(conn, since_ns, include_pytest=include_pytest)
        l2 = _query_l2_spans(conn, since_ns, include_pytest=include_pytest)
        args_spans = _query_tool_args_spans(conn, since_ns, include_pytest=include_pytest)
        user_prompts = _query_user_prompt_spans(conn, since_ns, include_pytest=include_pytest)
        refetch_total, refetch_attempts = _query_refetch_attempts(
            conn, since_ns, include_pytest=include_pytest
        )

    per_tool = _aggregate_per_tool(l1)
    l2_agg = _aggregate_l2(l2)
    recs = _recommend_per_tool(per_tool)
    disk_count, disk_sizes = _scan_disk_artifacts(args.spill_dir)

    report = _format_report(
        db_path=args.db,
        spill_dir=args.spill_dir,
        since=args.since,
        include_pytest=include_pytest,
        per_tool=per_tool,
        l2=l2_agg,
        args_spans=args_spans,
        user_prompt_sizes=user_prompts,
        refetch_total=refetch_total,
        refetch_attempts=refetch_attempts,
        disk_count=disk_count,
        disk_sizes=disk_sizes,
        recs=recs,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_path = args.out / f"REPORT-spill-calibration-{stamp}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
