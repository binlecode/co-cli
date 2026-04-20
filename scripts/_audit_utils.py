#!/usr/bin/env python3
"""Shared helpers for co-cli LLM audit scripts.

Provides log-parsing and log+DB correlation utilities consumed by
llm_audit_quality.py and llm_audit_performance.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent


def _default_log_path() -> Path | None:
    """Return the most recently modified *.log in .pytest-logs/, or None."""
    logs_dir = _REPO_ROOT / ".pytest-logs"
    if not logs_dir.exists():
        return None
    logs = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


# Duration tolerance (ms) for fuzzy matching log spans to DB spans.
_DURATION_TOLERANCE_MS = 150.0

# Time window padding around estimated run boundaries when querying DB.
_WINDOW_BUFFER_S = 7200  # 2 hours on each side

# Summary line: [pytest-harness] <test_id> | key=val | ...
_SUMMARY_PAT = re.compile(r"^\[pytest-harness\] (\S+) \| (.*)")
# Detail line: [pytest-harness]   <dur>s | chat <model> | ...
_DETAIL_PAT = re.compile(r"^\[pytest-harness\]\s{3}([\d.]+)s \| (chat \S+)")
# Optional token/finish fields appended to detail lines by _co_harness._span_detail()
_DETAIL_IN_PAT = re.compile(r"\bin_tokens=(\d+)")
_DETAIL_OUT_PAT = re.compile(r"\bout_tokens=(\d+)")
_DETAIL_FINISH_PAT = re.compile(r"\bfinish=(\S+)")
# Pytest session summary line
_SESSION_PAT = re.compile(r"(\d+) passed in ([\d.]+)s")

# (test_id, chat_duration_ms, in_tokens, out_tokens, finish)
_LogSpan = tuple[str, float, int | None, int | None, str | None]


class FlowChatSpan(NamedTuple):
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
    output_msgs: str | None
    input_msgs: str | None
    tool_defs: str | None


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
    mod = parts[0].split("/")[-1] if parts else ""
    return re.sub(r"^test_|\.py$", "", mod).replace("_", " ") or "unknown"


def _parse_log(log_path: Path) -> tuple[list[_LogSpan], float, float]:
    """
    Parse a pytest harness log.

    Returns:
        log_spans: (test_id, chat_duration_ms, in_tokens, out_tokens, finish) per chat detail line
        run_start_ts: estimated unix timestamp of run start
        run_end_ts: estimated unix timestamp of run end
    """
    log_spans: list[_LogSpan] = []
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
                in_tok_m = _DETAIL_IN_PAT.search(line)
                out_tok_m = _DETAIL_OUT_PAT.search(line)
                finish_m = _DETAIL_FINISH_PAT.search(line)
                log_spans.append(
                    (
                        current_test,
                        float(m3.group(1)) * 1000,
                        int(in_tok_m.group(1)) if in_tok_m else None,
                        int(out_tok_m.group(1)) if out_tok_m else None,
                        finish_m.group(1) if finish_m else None,
                    )
                )

    mtime = log_path.stat().st_mtime
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


def _build_api(attrs: dict) -> str | None:
    host = attrs.get("server.address")
    port = attrs.get("server.port")
    if isinstance(host, str) and host:
        return f"{host}:{port}" if port is not None else host
    return None


def _query_db_spans(db_path: Path, run_start: float, run_end: float) -> list[dict]:
    """Query co-cli chat spans from DB within an expanded time window.

    Accepts both 'co-cli-pytest' and 'co-cli' resources: test-module imports of
    co_cli.main can override pydantic-ai instrumentation to the production provider,
    landing chat spans under 'co-cli' rather than 'co-cli-pytest'.
    """
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
              AND (resource LIKE '%co-cli-pytest%' OR resource LIKE '%"service.name": "co-cli"%')
              AND resource NOT LIKE '%co-cli-eval%'
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


def _match_spans(
    log_spans: list[_LogSpan],
    db_spans: list[dict],
) -> list[FlowChatSpan]:
    """Fuzzy-match log chat spans to DB spans by duration and build FlowChatSpan list."""
    used: set[int] = set()
    result: list[FlowChatSpan] = []

    for test_id, log_dur_ms, log_in_tokens, log_out_tokens, log_finish in log_spans:
        flow = _infer_flow(test_id)

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

            input_msgs_raw = attrs.get("gen_ai.input.messages")
            output_msgs_raw = attrs.get("gen_ai.output.messages")
            tool_defs_raw = attrs.get("gen_ai.tool.definitions")

            result.append(
                FlowChatSpan(
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
                    input_chars=len(input_msgs_raw) if isinstance(input_msgs_raw, str) else None,
                    output_chars=len(output_msgs_raw)
                    if isinstance(output_msgs_raw, str)
                    else None,
                    output_msgs=output_msgs_raw if isinstance(output_msgs_raw, str) else None,
                    input_msgs=input_msgs_raw if isinstance(input_msgs_raw, str) else None,
                    tool_defs=tool_defs_raw if isinstance(tool_defs_raw, str) else None,
                )
            )
        else:
            finish_reasons = [log_finish] if log_finish else []
            result.append(
                FlowChatSpan(
                    test_id=test_id,
                    flow=flow,
                    duration_ms=log_dur_ms,
                    model=None,
                    api=None,
                    provider=None,
                    system=None,
                    finish_reasons=finish_reasons,
                    input_tokens=log_in_tokens,
                    output_tokens=log_out_tokens,
                    input_chars=None,
                    output_chars=None,
                    output_msgs=None,
                    input_msgs=None,
                    tool_defs=None,
                )
            )

    return result
