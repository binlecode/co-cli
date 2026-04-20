#!/usr/bin/env python3
"""Audit LLM runtime health from the OTel trace DB.

Combines performance, session health, tool usage, and role delegation into a
single report. Queries production co-cli spans and writes
docs/REPORT-llm-audit-runtime-YYYYMMDD-HHMMSS.md.

Usage:
    uv run python scripts/llm_audit_runtime.py
    uv run python scripts/llm_audit_runtime.py --since 2026-04-01
    uv run python scripts/llm_audit_runtime.py --since 2026-04-01 --until 2026-04-30
    uv run python scripts/llm_audit_runtime.py --db ~/.co-cli/co-cli-logs.db --out docs/
    uv run python scripts/llm_audit_runtime.py --log .pytest-logs/...full.log
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

from _audit_utils import FlowChatSpan, _default_log_path, _match_spans, _parse_log, _query_db_spans

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DB = Path.home() / ".co-cli" / "co-cli-logs.db"
_DEFAULT_OUT = _REPO_ROOT / "docs"
_PROD_FILTER = (
    '(resource LIKE \'%"service.name": "co-cli"%\''
    " AND resource NOT LIKE '%co-cli-pytest%'"
    " AND resource NOT LIKE '%co-cli-eval%')"
)
_MOCK_NAME_PREFIX = "chat function::"


# ---------------------------------------------------------------------------
# NamedTuples
# ---------------------------------------------------------------------------


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


class ToolSpan(NamedTuple):
    tool_name: str
    duration_ms: float
    result_size: int | None
    requires_approval: bool | None
    source: str | None
    rag_backend: str | None
    is_error: bool
    start_time_ns: int


class RoleSpan(NamedTuple):
    role: str
    model: str | None
    duration_ms: float
    requests_used: int | None
    request_limit: int | None
    input_tokens: int | None
    output_tokens: int | None
    start_time_ns: int


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


def _fmt(val: int | float | None) -> str:
    return "—" if val is None else str(val)


def _parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
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


def _time_filter_sql(since_ns: int | None, until_ns: int | None) -> tuple[str, list[int]]:
    sql = ""
    params: list[int] = []
    if since_ns is not None:
        sql += " AND start_time >= ?"
        params.append(since_ns)
    if until_ns is not None:
        sql += " AND start_time <= ?"
        params.append(until_ns)
    return sql, params


def _span_time_range(start_times: list[int]) -> str:
    if not start_times:
        return "no spans found"
    actual_start = datetime.fromtimestamp(min(start_times) / 1e9, tz=UTC).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    actual_end = datetime.fromtimestamp(max(start_times) / 1e9, tz=UTC).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    return f"`{actual_start}` → `{actual_end}`"


def _date_labels(since_ns: int | None, until_ns: int | None) -> tuple[str, str]:
    since_label = (
        datetime.fromtimestamp(since_ns / 1e9, tz=UTC).strftime("%Y-%m-%d")
        if since_ns
        else "all time"
    )
    until_label = (
        datetime.fromtimestamp(until_ns / 1e9, tz=UTC).strftime("%Y-%m-%d") if until_ns else "now"
    )
    return since_label, until_label


# ---------------------------------------------------------------------------
# Span attribute parsers
# ---------------------------------------------------------------------------


def _parse_session_attrs(
    attrs: dict,
) -> tuple[int | None, int | None, str | None, bool | None, int | None, bool]:
    input_tokens = _parse_optional_int(attrs.get("turn.input_tokens"))
    output_tokens = _parse_optional_int(attrs.get("turn.output_tokens"))
    outcome: str | None = attrs.get("turn.outcome")
    interrupted = _parse_optional_bool(attrs.get("turn.interrupted"))
    http_status = _parse_optional_int(attrs.get("http.status_code"))
    has_error = (
        attrs.get("error") is not None
        or attrs.get("provider_error") is not None
        or (http_status is not None and http_status >= 400)
        or outcome not in ("success", "continue", None)
    )
    return input_tokens, output_tokens, outcome, interrupted, http_status, has_error


def _parse_finish_reasons(attrs: dict) -> list[str]:
    raw = attrs.get("gen_ai.response.finish_reasons", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [raw]
    return raw if isinstance(raw, list) else []


def _detect_thinking(attrs: dict) -> bool:
    output_msgs_raw = attrs.get("gen_ai.output.messages")
    if not isinstance(output_msgs_raw, str):
        return False
    try:
        msgs = json.loads(output_msgs_raw)
        if isinstance(msgs, list):
            return any(
                part.get("type") == "thinking"
                for msg in msgs
                if isinstance(msg, dict)
                for part in msg.get("parts", [])
                if isinstance(part, dict)
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return False


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------


def _query_all_spans(
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
) -> tuple[list[ChatSpan], list[SessionSpan], list[ToolSpan], list[RoleSpan], list[dict]]:
    """Open the DB once and run all four span queries plus orchestration counts.

    Returns (chat_spans, session_spans, tool_spans, role_spans, orch_rows).
    """
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    time_sql, time_params = _time_filter_sql(since_ns, until_ns)

    with sqlite3.connect(db_path) as conn:
        chat_rows = conn.execute(
            f"""
            SELECT name, duration_ms, attributes, start_time
            FROM spans
            WHERE name LIKE 'chat %'
              AND name NOT LIKE '{_MOCK_NAME_PREFIX}%'
              AND {_PROD_FILTER}
              {time_sql}
            ORDER BY start_time
            """,
            time_params,
        ).fetchall()

        session_rows = conn.execute(
            f"""
            SELECT name, duration_ms, attributes, start_time
            FROM spans
            WHERE name IN ('co.turn', 'ctx_overflow_check', 'restore_session')
              AND {_PROD_FILTER}
              {time_sql}
            ORDER BY start_time
            """,
            time_params,
        ).fetchall()

        tool_rows = conn.execute(
            f"""
            SELECT name, duration_ms, attributes, start_time
            FROM spans
            WHERE name LIKE 'execute_tool %'
              AND {_PROD_FILTER}
              {time_sql}
            ORDER BY start_time
            """,
            time_params,
        ).fetchall()

        role_rows = conn.execute(
            f"""
            SELECT name, duration_ms, attributes, start_time
            FROM spans
            WHERE attributes LIKE '%"agent.role"%'
              AND {_PROD_FILTER}
              {time_sql}
            ORDER BY start_time
            """,
            time_params,
        ).fetchall()

        orch_count_rows = conn.execute(
            f"""
            SELECT name, COUNT(*) as cnt
            FROM spans
            WHERE name IN (
                'invoke_agent agent', 'co.turn', 'ctx_overflow_check',
                'restore_session', 'sync_knowledge'
            )
              AND {_PROD_FILTER}
              {time_sql}
            GROUP BY name
            ORDER BY cnt DESC
            """,
            time_params,
        ).fetchall()

    def _parse_attrs(attributes_json: str | None) -> dict:
        try:
            return json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            return {}

    chat_spans: list[ChatSpan] = []
    for name, duration_ms, attributes_json, start_time_ns in chat_rows:
        attrs = _parse_attrs(attributes_json)
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
                finish_reasons=_parse_finish_reasons(attrs),
                input_tokens=attrs.get("gen_ai.usage.input_tokens"),
                output_tokens=attrs.get("gen_ai.usage.output_tokens"),
                has_thinking=_detect_thinking(attrs),
                start_time_ns=start_time_ns or 0,
            )
        )

    session_spans: list[SessionSpan] = []
    for name, duration_ms, attributes_json, start_time_ns in session_rows:
        attrs = _parse_attrs(attributes_json)
        input_tokens, output_tokens, outcome, interrupted, http_status, has_error = (
            _parse_session_attrs(attrs)
        )
        session_spans.append(
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

    tool_spans: list[ToolSpan] = []
    for name, duration_ms, attributes_json, start_time_ns in tool_rows:
        attrs = _parse_attrs(attributes_json)
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
        tool_spans.append(
            ToolSpan(
                tool_name=tool_name,
                duration_ms=duration_ms or 0.0,
                result_size=result_size,
                requires_approval=requires_approval,
                source=attrs.get("co.tool.source"),
                rag_backend=attrs.get("rag.backend"),
                is_error=str(attrs.get("status_code", "")).upper() == "ERROR",
                start_time_ns=start_time_ns or 0,
            )
        )

    role_spans: list[RoleSpan] = []
    for _name, duration_ms, attributes_json, start_time_ns in role_rows:
        attrs = _parse_attrs(attributes_json)
        role: str | None = attrs.get("agent.role")
        if not role:
            continue
        role_spans.append(
            RoleSpan(
                role=role,
                model=attrs.get("agent.model"),
                duration_ms=duration_ms or 0.0,
                requests_used=_parse_optional_int(attrs.get("agent.requests_used")),
                request_limit=_parse_optional_int(attrs.get("agent.request_limit")),
                input_tokens=_parse_optional_int(attrs.get("gen_ai.usage.input_tokens")),
                output_tokens=_parse_optional_int(attrs.get("gen_ai.usage.output_tokens")),
                start_time_ns=start_time_ns or 0,
            )
        )

    orch_rows = [{"name": row[0], "count": row[1]} for row in orch_count_rows]
    return chat_spans, session_spans, tool_spans, role_spans, orch_rows


# ---------------------------------------------------------------------------
# Report section builders
# ---------------------------------------------------------------------------


def _section_perf(
    spans: list[ChatSpan],
    since_ns: int | None,
    until_ns: int | None,
) -> str:
    if not spans:
        return "## 2. LLM Performance\n\n_No chat spans found._\n"

    total_in = sum(s.input_tokens for s in spans if s.input_tokens is not None)
    total_out = sum(s.output_tokens for s in spans if s.output_tokens is not None)
    io_ratio = f"{total_in / total_out:.1f}" if total_out > 0 else "—"
    finish_counts: dict[str, int] = defaultdict(int)
    for s in spans:
        for r in s.finish_reasons:
            finish_counts[r] += 1
    thinking_count = sum(1 for s in spans if s.has_thinking)

    finish_lines = (
        "\n".join(f"  - `{count}` × `{reason}`" for reason, count in sorted(finish_counts.items()))
        or "  - (none)"
    )

    by_model: dict[str, list[ChatSpan]] = defaultdict(list)
    for s in spans:
        by_model[s.model or "unknown"].append(s)

    model_rows = []
    for model, mspans in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        m_durs = sorted(s.duration_ms for s in mspans)
        m_in = sum(s.input_tokens for s in mspans if s.input_tokens is not None)
        m_out = sum(s.output_tokens for s in mspans if s.output_tokens is not None)
        m_ratio = f"{m_in / m_out:.1f}" if m_out > 0 else "—"
        model_rows.append(
            f"| `{model}` | {len(mspans)} | {_pct(len(mspans), len(spans))}"
            f" | {m_in if m_in else '—'} | {m_out if m_out else '—'} | {m_ratio}"
            f" | {_dur_s(_percentile(m_durs, 50))} | {_dur_s(_percentile(m_durs, 95))} |"
        )
    model_table = (
        "| Model | Calls | % | In Tokens | Out Tokens | I/O Ratio | p50 Latency | p95 Latency |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n" + "\n".join(model_rows)
    )

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

    latency_section = "\n\n".join(
        [
            _latency_block("All spans", spans),
            _latency_block(
                "Reasoning mode (thinking blocks present)", [s for s in spans if s.has_thinking]
            ),
            _latency_block(
                "No-reason mode (no thinking blocks)", [s for s in spans if not s.has_thinking]
            ),
        ]
    )

    throughputs = [
        s.output_tokens / s.duration_ms * 1000
        for s in spans
        if s.output_tokens is not None and s.duration_ms > 0
    ]
    tps_lines = (
        f"- Median tokens/s: `{statistics.median(throughputs):.1f}`\n"
        f"- Max tokens/s: `{max(throughputs):.1f}`\n"
        f"- Mean tokens/s: `{statistics.mean(throughputs):.1f}`"
        if throughputs
        else "— (no token data)"
    )

    models = sorted({s.model for s in spans if s.model})
    providers = sorted({s.provider for s in spans if s.provider})
    apis = sorted({s.api for s in spans if s.api})

    return f"""\
## 2. LLM Performance

### 2.1 Summary

- Total LLM calls: `{len(spans)}`
- Models: {", ".join(f"`{m}`" for m in models) if models else "N/A"}
- Providers: {", ".join(f"`{p}`" for p in providers) if providers else "N/A"}
- APIs: {", ".join(f"`{a}`" for a in apis) if apis else "N/A"}
- Total input tokens: `{total_in if total_in else "—"}`
- Total output tokens: `{total_out if total_out else "—"}`
- Input/output ratio: `{io_ratio}` to 1
- Tool-call finish: `{finish_counts.get("tool_call", 0)}` ({_pct(finish_counts.get("tool_call", 0), len(spans))})
- Stop finish: `{finish_counts.get("stop", 0)}` ({_pct(finish_counts.get("stop", 0), len(spans))})
- Length-truncated: `{finish_counts.get("length", 0)}`
- Spans with thinking blocks: `{thinking_count}` ({_pct(thinking_count, len(spans))})
- Finish reasons:
{finish_lines}

### 2.2 Per-Model Breakdown

{model_table}

### 2.3 Latency Profile

{latency_section}

### 2.4 Throughput

{tps_lines}
"""


def _section_session(spans: list[SessionSpan]) -> str:
    turn_spans = [s for s in spans if s.name == "co.turn"]
    restore_spans = [s for s in spans if s.name == "restore_session"]
    overflow_spans = [s for s in spans if s.name == "ctx_overflow_check"]

    if not turn_spans:
        return "## 3. Session Health\n\n_No session spans found._\n"

    session_count = (len(restore_spans) + 1) if turn_spans else 0

    # Provider reliability
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

    # Session depth
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

    depth_lines: list[str] = []
    expected_sessions = len(restore_spans) + 1
    actual_turns = len(turn_spans)
    if abs(expected_sessions - actual_turns) > 1:
        depth_lines.append(
            f"> Warning: span count mismatch — {len(restore_spans)} restore_session span(s)"
            f" imply {expected_sessions} session(s) but only {actual_turns} co.turn span(s) found."
        )
    if session_turn_counts:
        sorted_depths = sorted(session_turn_counts)
        depth_lines.append(
            f"- Sessions with turn data: `{len(sorted_depths)}`\n"
            f"- p50 turns/session: `{_percentile(sorted_depths, 50):.1f}`\n"
            f"- p95 turns/session: `{_percentile(sorted_depths, 95):.1f}`\n"
            f"- Max turns/session: `{max(sorted_depths)}`\n"
            f"- Min turns/session: `{min(sorted_depths)}`"
        )
    else:
        depth_lines.append("- No session depth data available.")
    depth_block = "\n".join(depth_lines)

    # Token accumulation
    in_tokens = sorted(s.input_tokens for s in turn_spans if s.input_tokens is not None)
    out_tokens = sorted(s.output_tokens for s in turn_spans if s.output_tokens is not None)

    def _token_block(vals: list[int], label: str) -> str:
        if not vals:
            return f"  - (no {label} data)"
        return (
            f"  - p50: `{int(_percentile(vals, 50))}`\n"
            f"  - p95: `{int(_percentile(vals, 95))}`\n"
            f"  - Max: `{max(vals)}`"
        )

    overflow_rate = len(overflow_spans) / session_count if session_count > 0 else 0.0

    return f"""\
## 3. Session Health

### 3.1 Provider Reliability

- Turns (co.turn): `{len(turn_spans)}`
- Sessions (restore_session + 1): `{session_count}`
- Turns with error indicators: `{len(error_turns)}` ({_pct(len(error_turns), len(turn_spans))})
- HTTP error status breakdown:
{status_lines}
- Turn outcome breakdown:
{outcome_lines}

### 3.2 Context Pressure

- ctx_overflow_check spans: `{len(overflow_spans)}`
- Overflow checks per session: `{overflow_rate:.2f}`

### 3.3 Session Depth

{depth_block}

### 3.4 Token Accumulation

- Input tokens per turn (n={len(in_tokens)}):
{_token_block(in_tokens, "turn.input_tokens")}
- Output tokens per turn (n={len(out_tokens)}):
{_token_block(out_tokens, "turn.output_tokens")}
"""


def _section_tools(spans: list[ToolSpan]) -> str:
    if not spans:
        return "## 4. Tool Usage\n\n_No tool spans found._\n"

    distinct_tools = sorted({s.tool_name for s in spans})
    by_tool: dict[str, list[ToolSpan]] = defaultdict(list)
    for s in spans:
        by_tool[s.tool_name].append(s)

    # Error rate by tool
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

    # Latency by tool (top 15)
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

    # Result size
    sizes_with_tool: list[tuple[str, int]] = [
        (s.tool_name, s.result_size) for s in spans if s.result_size is not None
    ]
    sizes_all = sorted(sz for _, sz in sizes_with_tool)
    if sizes_all:
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
        size_summary = (
            f"- Spans with result_size: `{len(sizes_all)}`\n"
            f"- p50: `{int(_percentile(sizes_all, 50))}` bytes\n"
            f"- p95: `{int(_percentile(sizes_all, 95))}` bytes\n"
            f"- Max: `{max(sizes_all)}` bytes"
        )
        size_table = (
            "| Tool | n | p50 (bytes) | p95 (bytes) | Max (bytes) |\n"
            "|---|---:|---:|---:|---:|\n" + "\n".join(size_tool_rows)
        )
    else:
        size_summary = "- No `co.tool.result_size` attribute found in spans."
        size_table = "_No result_size data available._"

    # Approval & source
    approval_spans = [s for s in spans if s.requires_approval is not None]
    approved_count = sum(1 for s in approval_spans if s.requires_approval)
    mcp_count = sum(1 for s in spans if s.source == "mcp")
    native_count = len(spans) - mcp_count

    # RAG backend
    rag_spans = [s for s in spans if s.rag_backend is not None]
    if rag_spans:
        rag_counts: dict[str, int] = defaultdict(int)
        for s in rag_spans:
            rag_counts[s.rag_backend or "unknown"] += 1
        rag_rows = "\n".join(
            f"| `{backend}` | {count} | {_pct(count, len(rag_spans))} |"
            for backend, count in sorted(rag_counts.items(), key=lambda kv: -kv[1])
        )
        rag_table = "| Backend | Calls | Share |\n|---|---:|---:|\n" + rag_rows
    else:
        rag_table = "_No spans with `rag.backend` attribute found._"

    return f"""\
## 4. Tool Usage

- Total tool calls: `{len(spans)}`
- Distinct tools: `{len(distinct_tools)}`
- Tools seen: {", ".join(f"`{t}`" for t in distinct_tools) if distinct_tools else "N/A"}

### 4.1 Error Rate by Tool

- Total errors: `{total_errors}` ({_pct(total_errors, len(spans))})

{error_table}

### 4.2 Latency by Tool

{latency_table}

### 4.3 Result Size Distribution

{size_summary}

{size_table}

### 4.4 Approval & Source Profile

- Spans with requires_approval attribute: `{len(approval_spans)}`
- Requires approval: `{approved_count}` ({_pct(approved_count, len(approval_spans))})
- MCP tools: `{mcp_count}` ({_pct(mcp_count, len(spans))})
- Native tools: `{native_count}` ({_pct(native_count, len(spans))})

### 4.5 RAG Backend Distribution

- Total RAG spans: `{len(rag_spans)}`

{rag_table}
"""


def _section_roles(spans: list[RoleSpan]) -> str:
    if not spans:
        return "## 5. Role Delegation\n\n_No role spans found._\n"

    distinct_roles = sorted({s.role for s in spans})
    by_role: dict[str, list[RoleSpan]] = defaultdict(list)
    for s in spans:
        by_role[s.role].append(s)

    # Usage
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

    # Saturation
    saturation_rows = []
    flagged_roles: list[str] = []
    for role in sorted(by_role):
        role_spans = by_role[role]
        sat_vals = [
            s.requests_used / s.request_limit
            for s in role_spans
            if s.requests_used is not None and s.request_limit is not None and s.request_limit > 0
        ]
        if not sat_vals:
            saturation_rows.append(f"| `{role}` | {len(role_spans)} | — | — | — | — |")
            continue
        sorted_sat = sorted(sat_vals)
        p50 = _percentile(sorted_sat, 50)
        p95 = _percentile(sorted_sat, 95)
        flag = " ⚠" if p95 > 0.8 else ""
        if p95 > 0.8:
            flagged_roles.append(role)
        saturation_rows.append(
            f"| `{role}` | {len(role_spans)} | {len(sat_vals)}"
            f" | {p50:.2f} | {p95:.2f}{flag} | {max(sorted_sat):.2f} |"
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

    # Latency
    latency_rows = []
    for role in sorted(by_role, key=lambda r: -len(by_role[r])):
        durs = sorted(s.duration_ms for s in by_role[role])
        latency_rows.append(
            f"| `{role}` | {len(durs)}"
            f" | {_dur_s(_percentile(durs, 50))}"
            f" | {_dur_s(_percentile(durs, 95))}"
            f" | {_dur_s(max(durs))} |"
        )
    latency_table = (
        "| Role | Invocations | p50 | p95 | Max |\n"
        "|---|---:|---:|---:|---:|\n" + "\n".join(latency_rows)
    )

    # Token cost
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
            f" | {_fmt(in_p50)} | {_fmt(out_p50)}"
            f" | {total_in if in_vals else '—'} | {total_out if out_vals else '—'}"
            f" | {efficiency} |"
        )
    token_table = (
        "| Role | n | p50 In | p50 Out | Total In | Total Out | Out/In Ratio |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n" + "\n".join(token_rows)
    )

    return f"""\
## 5. Role Delegation

- Total role invocations: `{len(spans)}`
- Distinct roles: `{len(distinct_roles)}`
- Roles seen: {", ".join(f"`{r}`" for r in distinct_roles) if distinct_roles else "N/A"}

### 5.1 Role Usage

{usage_table}

### 5.2 Request Limit Saturation

{saturation_table}
{flagged_note}

### 5.3 Per-Role Latency

{latency_table}

### 5.4 Per-Role Token Cost

{token_table}
"""


def _section_orch(orch_rows: list[dict]) -> str:
    if not orch_rows:
        return "## 6. Orchestration Events\n\n_No orchestration spans found._\n"
    rows_md = "\n".join(f"| `{row['name']}` | {row['count']} |" for row in orch_rows)
    table = "| Event | Count |\n|---|---:|\n" + rows_md
    return f"## 6. Orchestration Events\n\n{table}\n"


def _section_flow_latency(flow_spans: list[FlowChatSpan]) -> str:
    if not flow_spans:
        return "## 7. Per-Flow Latency\n\n_No log-correlated spans found._\n"
    by_flow: dict[str, list[float]] = defaultdict(list)
    for s in flow_spans:
        by_flow[s.flow].append(s.duration_ms)
    rows = []
    for flow, durs in sorted(by_flow.items()):
        sdurs = sorted(durs)
        rows.append(
            f"| {flow} | {len(durs)}"
            f" | {_dur_s(_percentile(sdurs, 50))}"
            f" | {_dur_s(_percentile(sdurs, 95))}"
            f" | {_dur_s(max(sdurs))} |"
        )
    table = "| Flow | Calls | p50 | p95 | Max |\n|---|---:|---:|---:|---:|\n" + "\n".join(rows)
    return f"## 7. Per-Flow Latency\n\n{table}\n"


def _section_flow_cost(flow_spans: list[FlowChatSpan]) -> str:
    if not flow_spans:
        return "## 8. Per-Flow Cost\n\n_No log-correlated spans found._\n"
    by_flow: dict[str, list[FlowChatSpan]] = defaultdict(list)
    for s in flow_spans:
        by_flow[s.flow].append(s)
    rows = []
    for flow, fspans in sorted(by_flow.items()):
        in_toks = [s.input_tokens for s in fspans if s.input_tokens is not None]
        out_toks = [s.output_tokens for s in fspans if s.output_tokens is not None]
        total_in = sum(in_toks) if in_toks else None
        total_out = sum(out_toks) if out_toks else None
        median_in = round(statistics.median(in_toks)) if in_toks else None
        median_out = round(statistics.median(out_toks)) if out_toks else None
        eff = f"{total_out / total_in:.3f}" if total_in and total_out else "—"
        rows.append(
            f"| {flow} | {len(fspans)}"
            f" | {_fmt(total_in)} | {_fmt(total_out)}"
            f" | {_fmt(median_in)} | {_fmt(median_out)}"
            f" | {eff} |"
        )
    table = (
        "| Flow | Calls | Total In | Total Out | Median In | Median Out | Out/In |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n" + "\n".join(rows)
    )
    return f"## 8. Per-Flow Cost\n\n{table}\n"


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _generate_report(
    chat_spans: list[ChatSpan],
    session_spans: list[SessionSpan],
    tool_spans: list[ToolSpan],
    role_spans: list[RoleSpan],
    orch_rows: list[dict],
    db_path: Path,
    since_ns: int | None,
    until_ns: int | None,
    flow_spans: list[FlowChatSpan] | None = None,
) -> str:
    today = date.today().isoformat()
    since_label, until_label = _date_labels(since_ns, until_ns)

    all_start_times = (
        [s.start_time_ns for s in chat_spans]
        + [s.start_time_ns for s in session_spans]
        + [s.start_time_ns for s in tool_spans]
        + [s.start_time_ns for s in role_spans]
    )
    time_range = _span_time_range([t for t in all_start_times if t > 0])

    turn_count = sum(1 for s in session_spans if s.name == "co.turn")
    restore_count = sum(1 for s in session_spans if s.name == "restore_session")
    overflow_count = sum(1 for s in session_spans if s.name == "ctx_overflow_check")

    header = f"""\
# REPORT: LLM Runtime Audit

**Date:** {today}
**Source:** `{db_path}`
**Filter:** `{since_label}` → `{until_label}` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: {time_range}
- Chat spans: `{len(chat_spans)}`
- Session spans — co.turn: `{turn_count}` · restore_session: `{restore_count}` · ctx_overflow_check: `{overflow_count}`
- Tool spans: `{len(tool_spans)}`
- Role spans: `{len(role_spans)}`
"""

    sections = [
        header,
        _section_perf(chat_spans, since_ns, until_ns),
        _section_session(session_spans),
        _section_tools(tool_spans),
        _section_roles(role_spans),
        _section_orch(orch_rows),
    ]
    if flow_spans is not None:
        sections.append(_section_flow_latency(flow_spans))
        sections.append(_section_flow_cost(flow_spans))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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
        "--out", type=Path, default=_DEFAULT_OUT, help="output directory (default: docs/)"
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", help="include spans from this date (UTC, inclusive)"
    )
    parser.add_argument(
        "--until", metavar="YYYY-MM-DD", help="include spans up to this date (UTC, inclusive)"
    )
    parser.add_argument(
        "--log",
        type=Path,
        metavar="PATH",
        help="pytest log file; appends §7 Per-Flow Latency and §8 Per-Flow Cost",
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
    chat_spans, session_spans, tool_spans, role_spans, orch_rows = _query_all_spans(
        db_path, since_ns, until_ns
    )
    print(
        f"  {len(chat_spans)} chat, {sum(1 for s in session_spans if s.name == 'co.turn')} turns,"
        f" {len(tool_spans)} tools, {len(role_spans)} roles"
    )

    if not any([chat_spans, session_spans, tool_spans, role_spans]):
        raise SystemExit(
            "No spans found. Check --since/--until or whether the DB has production spans."
        )

    flow_spans: list[FlowChatSpan] | None = None
    log_arg = args.log or _default_log_path()
    if log_arg:
        log_path = log_arg.resolve()
        if not log_path.exists():
            print(f"WARNING: --log path not found: {log_path} — skipping per-flow sections")
        else:
            print(f"Parsing:  {log_path}")
            log_spans, run_start, run_end = _parse_log(log_path)
            if log_spans:
                db_log_spans = _query_db_spans(db_path, run_start, run_end)
                flow_spans = _match_spans(log_spans, db_log_spans)
                print(
                    f"  {len(flow_spans)} log spans — "
                    f"{sum(1 for s in flow_spans if s.finish_reasons)} DB-matched"
                )
            else:
                print("  No parseable chat spans in log — per-flow sections skipped")

    report = _generate_report(
        chat_spans,
        session_spans,
        tool_spans,
        role_spans,
        orch_rows,
        db_path,
        since_ns,
        until_ns,
        flow_spans,
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"REPORT-llm-audit-runtime-{stamp}.md"
    out_path.write_text(report)
    print(f"Written:  {out_path}")


if __name__ == "__main__":
    main()
