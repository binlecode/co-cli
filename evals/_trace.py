"""OTel span collection, analysis, and diagnostic output for evals.

Span data flows:
  bootstrap_telemetry(db_path)   — set up OTel + SQLite exporter once per eval run
  time.time_ns()                 — capture start_ns before each run_turn()
  provider.force_flush()         — drain BatchSpanProcessor after each run_turn()
  collect_spans_for_run()        — query spans written since start_ns
  analyze_turn_spans()           — parse span tree → TurnTrace
  print_timeline()               — emit per-turn step-by-step output
  print_rca()                    — emit full failure dump for diagnosis
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals._checks import score_response


# ---------------------------------------------------------------------------
# Span row type
# ---------------------------------------------------------------------------


@dataclass
class SpanRow:
    id: str
    trace_id: str
    parent_id: str | None
    name: str
    kind: str | None
    start_time: int
    end_time: int | None
    duration_ms: float | None
    status_code: str | None
    attributes: dict[str, Any]
    events: list[dict[str, Any]]


def _parse_span_row(row: tuple) -> SpanRow:
    (
        span_id,
        trace_id,
        parent_id,
        name,
        kind,
        start_time,
        end_time,
        duration_ms,
        status_code,
        attributes_json,
        events_json,
    ) = row
    attrs: dict[str, Any] = json.loads(attributes_json) if attributes_json else {}
    events: list[dict[str, Any]] = json.loads(events_json) if events_json else []
    return SpanRow(
        id=span_id,
        trace_id=trace_id,
        parent_id=parent_id,
        name=name,
        kind=kind,
        start_time=start_time,
        end_time=end_time,
        duration_ms=duration_ms,
        status_code=status_code,
        attributes=attrs,
        events=events,
    )


def _get_attr(span: SpanRow, key: str, default: Any = None) -> Any:
    return span.attributes.get(key, default)


def _parse_messages_attr(span: SpanRow, key: str) -> list[dict[str, Any]]:
    raw = _get_attr(span, key)
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, list):
        return raw
    return []


def extract_thinking(messages: list[dict[str, Any]]) -> str:
    """Extract the first thinking content from output messages."""
    for msg in messages:
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "thinking":
                content = part.get("content", "") or part.get("thinking", "")
                if content:
                    return content
    return ""


def extract_text(messages: list[dict[str, Any]]) -> str:
    """Extract the last text content from output messages."""
    text = ""
    for msg in messages:
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                content = part.get("content", "") or part.get("text", "")
                if content:
                    text = content
    return text


def extract_tool_calls_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract tool-call parts from output messages.

    pydantic-ai OTel spans use type="tool_call" (underscore) in output messages.
    """
    calls: list[dict[str, Any]] = []
    for msg in messages:
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("tool_call", "tool-call"):
                calls.append(part)
    return calls


# ---------------------------------------------------------------------------
# Structured span data types
# ---------------------------------------------------------------------------


@dataclass
class ModelRequestData:
    span: SpanRow
    request_index: int
    input_tokens: int
    output_tokens: int
    finish_reason: str
    # thinking_excerpt: first 200 chars for timeline summary
    thinking_excerpt: str
    # thinking_full: untruncated thinking content
    thinking_full: str
    text_response: str
    tool_calls: list[dict[str, Any]]
    request_model: str = ""
    response_model: str = ""
    response_id: str = ""
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    server_address: str = ""
    server_port: int | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_messages: list[dict[str, Any]] = field(default_factory=list)
    system_instructions: list[dict[str, Any]] = field(default_factory=list)
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolSpanData:
    span: SpanRow
    tool_name: str
    arguments: dict[str, Any]
    duration_ms: float | None
    result_preview: str
    tool_call_id: str = ""
    result_full: str = ""
    exception_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TimelineRow:
    elapsed_ms: int
    duration_ms: str
    span_name: str
    detail: str


@dataclass
class TurnTrace:
    """Per-turn trace from a single agent.run() call."""
    spans: list[SpanRow]
    root_span: SpanRow | None
    model_requests: list[ModelRequestData]
    tool_spans: list[ToolSpanData]
    timeline: list[TimelineRow]
    wall_time_s: float
    response_text: str
    failed_checks: list[str]
    error: str | None = None
    system_instructions: list[dict[str, Any]] = field(default_factory=list)
    all_messages_raw: list[dict[str, Any]] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Telemetry bootstrap and span collection
# ---------------------------------------------------------------------------


def bootstrap_telemetry(db_path: str) -> Any:
    """Set up OTel TracerProvider with SQLite exporter and instrument pydantic-ai.

    Returns the TracerProvider. Uses lazy imports so OTel is only required when
    trace collection is actually used.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from pydantic_ai import Agent
    from pydantic_ai.agent import InstrumentationSettings
    from co_cli._telemetry import SQLiteSpanExporter

    exporter = SQLiteSpanExporter(db_path=db_path)
    resource = Resource.create({"service.name": "co-cli", "service.version": "eval"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider, version=3))
    return provider


def collect_spans_for_run(start_ns: int, db_path: str) -> list[SpanRow]:
    """Query spans written after start_ns, find the root trace, return its subtree."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        rows = conn.execute(
            """
            SELECT id, trace_id, parent_id, name, kind, start_time, end_time,
                   duration_ms, status_code, attributes, events
            FROM spans
            WHERE start_time >= ?
            ORDER BY start_time ASC
            """,
            (start_ns,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    spans = [_parse_span_row(r) for r in rows]

    # Prefer a root span with no parent and "agent" in name; fall back to earliest
    root_trace_id: str | None = None
    for s in spans:
        if s.parent_id is None and "agent" in s.name.lower():
            root_trace_id = s.trace_id
            break
    if root_trace_id is None:
        root_trace_id = spans[0].trace_id

    return [s for s in spans if s.trace_id == root_trace_id]


def find_root_span(spans: list[SpanRow]) -> SpanRow | None:
    """Return the span with no parent_id, or the one whose parent is outside the set."""
    ids = {s.id for s in spans}
    for s in spans:
        if s.parent_id is None or s.parent_id not in ids:
            return s
    return spans[0] if spans else None


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_timeline(spans: list[SpanRow], root: SpanRow | None) -> list[TimelineRow]:
    """Build a timeline table from sorted spans."""
    if not spans:
        return []

    base_ns = root.start_time if root else spans[0].start_time
    rows: list[TimelineRow] = []

    for s in spans:
        elapsed_ms = int((s.start_time - base_ns) / 1_000_000)
        dur_str = f"{int(s.duration_ms):,}" if s.duration_ms is not None else "—"

        detail_parts: list[str] = []

        in_tok = _get_attr(s, "gen_ai.usage.input_tokens")
        out_tok = _get_attr(s, "gen_ai.usage.output_tokens")
        if in_tok is not None and out_tok is not None:
            detail_parts.append(f"tokens in={in_tok} out={out_tok}")

        finish = _get_attr(s, "gen_ai.response.finish_reasons")
        if finish:
            if isinstance(finish, list) and finish:
                detail_parts.append(f"finish={finish[0]}")
            elif isinstance(finish, str):
                detail_parts.append(f"finish={finish}")

        tool_name = _get_attr(s, "gen_ai.tool.name") or _get_attr(s, "tool_name")
        if tool_name is None and s.name.startswith("execute_tool "):
            tool_name = s.name.replace("execute_tool ", "").strip()
        if tool_name:
            args_raw = (
                _get_attr(s, "gen_ai.tool.call.arguments")
                or _get_attr(s, "tool_arguments")
                or _get_attr(s, "gen_ai.tool.arguments")
                or "{}"
            )
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                    first_key = next(iter(args), None)
                    if first_key:
                        first_val = str(args[first_key])[:50]
                        detail_parts.append(f'{first_key}="{first_val}"')
                except (json.JSONDecodeError, TypeError):
                    pass

        detail = "  ".join(detail_parts) if detail_parts else "—"

        rows.append(TimelineRow(
            elapsed_ms=elapsed_ms,
            duration_ms=dur_str,
            span_name=s.name,
            detail=detail,
        ))

    return rows


# ---------------------------------------------------------------------------
# Span analysis
# ---------------------------------------------------------------------------


def analyze_turn_spans(
    prompt: str,
    checks: list[dict[str, Any]],
    spans: list[SpanRow],
    wall_time_s: float,
) -> TurnTrace:
    """Parse span tree for one agent.run() turn into structured trace data."""
    root = find_root_span(spans)
    sorted_spans = sorted(spans, key=lambda s: s.start_time)

    model_requests: list[ModelRequestData] = []
    tool_spans_list: list[ToolSpanData] = []
    turn_system_instructions: list[dict[str, Any]] = []
    all_messages_raw: list[dict[str, Any]] = []
    response_text = ""

    req_idx = 0
    for s in sorted_spans:
        name_lower = s.name.lower()

        # invoke_agent span — collect all messages and final result
        if "invoke_agent" in name_lower or name_lower == "agent":
            all_msgs = _parse_messages_attr(s, "pydantic_ai.all_messages")
            if all_msgs:
                all_messages_raw = all_msgs
            final_result = _get_attr(s, "final_result")
            if final_result and isinstance(final_result, str):
                response_text = final_result
            continue

        # Model request spans
        if any(kw in name_lower for kw in ("chat", "request", "completions", "generate")):
            out_msgs = _parse_messages_attr(s, "gen_ai.output.messages")
            thinking_raw = extract_thinking(out_msgs)
            text_raw = extract_text(out_msgs)
            tool_calls = extract_tool_calls_from_messages(out_msgs)

            input_tokens = int(_get_attr(s, "gen_ai.usage.input_tokens", 0) or 0)
            output_tokens = int(_get_attr(s, "gen_ai.usage.output_tokens", 0) or 0)

            finish_reasons = _get_attr(s, "gen_ai.response.finish_reasons")
            if isinstance(finish_reasons, list) and finish_reasons:
                finish_reason = str(finish_reasons[0])
            elif isinstance(finish_reasons, str):
                finish_reason = finish_reasons
            else:
                finish_reason = "unknown"

            # Cache tokens from gen_ai.usage.details
            cache_read = 0
            cache_write = 0
            usage_details_raw = _get_attr(s, "gen_ai.usage.details")
            if usage_details_raw:
                if isinstance(usage_details_raw, str):
                    try:
                        usage_details: dict[str, Any] = json.loads(usage_details_raw)
                    except (json.JSONDecodeError, TypeError):
                        usage_details = {}
                elif isinstance(usage_details_raw, dict):
                    usage_details = usage_details_raw
                else:
                    usage_details = {}
                cache_read = int(usage_details.get("cache_read_tokens", 0) or 0)
                cache_write = int(usage_details.get("cache_write_tokens", 0) or 0)

            server_address = str(_get_attr(s, "server.address") or "")
            server_port_raw = _get_attr(s, "server.port")
            server_port = int(server_port_raw) if server_port_raw is not None else None

            temp_raw = _get_attr(s, "gen_ai.request.temperature")
            temperature = float(temp_raw) if temp_raw is not None else None
            top_p_raw = _get_attr(s, "gen_ai.request.top_p")
            top_p = float(top_p_raw) if top_p_raw is not None else None
            max_tokens_raw = _get_attr(s, "gen_ai.request.max_tokens")
            max_tokens_val = int(max_tokens_raw) if max_tokens_raw is not None else None

            req_idx += 1
            model_requests.append(ModelRequestData(
                span=s,
                request_index=req_idx,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                thinking_excerpt=thinking_raw[:200] if thinking_raw else "",
                thinking_full=thinking_raw,
                text_response=text_raw,
                tool_calls=tool_calls,
                request_model=str(_get_attr(s, "gen_ai.request.model") or ""),
                response_model=str(_get_attr(s, "gen_ai.response.model") or ""),
                response_id=str(_get_attr(s, "gen_ai.response.id") or ""),
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens_val,
                server_address=server_address,
                server_port=server_port,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                input_messages=_parse_messages_attr(s, "gen_ai.input.messages"),
                system_instructions=[
                    m for m in _parse_messages_attr(s, "gen_ai.input.messages")
                    if isinstance(m, dict) and m.get("role") == "system"
                ],
                tool_definitions=_parse_messages_attr(s, "gen_ai.tool.definitions"),
            ))
            continue

        # Tool execution spans
        tool_name = (
            _get_attr(s, "gen_ai.tool.name")
            or _get_attr(s, "tool_name")
        )
        is_execute_tool = s.name.startswith("execute_tool ")
        if tool_name or is_execute_tool:
            if tool_name is None:
                tool_name = s.name.replace("execute_tool ", "").strip()

            args_raw = (
                _get_attr(s, "gen_ai.tool.call.arguments")
                or _get_attr(s, "tool_arguments")
                or _get_attr(s, "gen_ai.tool.arguments")
                or "{}"
            )
            if isinstance(args_raw, str):
                try:
                    args_dict = json.loads(args_raw)
                except (json.JSONDecodeError, TypeError):
                    args_dict = {"raw": args_raw}
            elif isinstance(args_raw, dict):
                args_dict = args_raw
            else:
                args_dict = {}

            result_raw = (
                _get_attr(s, "gen_ai.tool.call.result")
                or _get_attr(s, "tool_response")
                or _get_attr(s, "gen_ai.tool.result")
                or ""
            )
            if not isinstance(result_raw, str):
                result_raw = json.dumps(result_raw)

            exception_events = [
                e for e in s.events
                if isinstance(e, dict) and e.get("name") == "exception"
            ]

            tool_spans_list.append(ToolSpanData(
                span=s,
                tool_name=str(tool_name),
                arguments=args_dict,
                duration_ms=s.duration_ms,
                result_preview=result_raw[:300],
                tool_call_id=str(_get_attr(s, "gen_ai.tool.call.id") or ""),
                result_full=result_raw,
                exception_events=exception_events,
            ))

    # Fallback: if no final_result from invoke_agent, use last model request text
    if not response_text and model_requests:
        response_text = model_requests[-1].text_response

    failed_checks = score_response(response_text, checks)
    timeline = build_timeline(sorted_spans, root)
    total_input_tokens = sum(r.input_tokens for r in model_requests)
    total_output_tokens = sum(r.output_tokens for r in model_requests)

    return TurnTrace(
        spans=sorted_spans,
        root_span=root,
        model_requests=model_requests,
        tool_spans=tool_spans_list,
        timeline=timeline,
        wall_time_s=wall_time_s,
        response_text=response_text,
        failed_checks=failed_checks,
        error=None,
        system_instructions=turn_system_instructions,
        all_messages_raw=all_messages_raw,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
    )


# ---------------------------------------------------------------------------
# Diagnostic output
# ---------------------------------------------------------------------------


def print_timeline(label: str, trace: TurnTrace, *, verbose: bool = False) -> None:
    """Print a step-by-step span summary for one turn.

    Always prints tool call names, durations, and exception flags.
    With ``verbose=True`` (or when any dimension failed), also prints
    tool result previews, thinking excerpts, and the full span timeline.
    """
    print(f"\n  --- {label} trace ({trace.wall_time_s:.1f}s) ---")
    if not trace.spans:
        print("    (no spans collected — OTel flush may have been delayed)")
        return

    if trace.tool_spans:
        print(f"  Tool calls ({len(trace.tool_spans)}):")
        for ts in trace.tool_spans:
            first_kv = ""
            if ts.arguments:
                k = next(iter(ts.arguments))
                v = str(ts.arguments[k])[:60]
                first_kv = f"  {k}={v!r}"
            dur = f"{ts.duration_ms:.0f}ms" if ts.duration_ms is not None else "?ms"
            exc_flag = " [EXCEPTION]" if ts.exception_events else ""
            print(f"    [{dur}] {ts.tool_name}{first_kv}{exc_flag}")
            if verbose or ts.exception_events:
                if ts.exception_events:
                    for ev in ts.exception_events:
                        msg = ev.get("attributes", {}).get("exception.message", "")
                        print(f"      exception: {msg}")
                elif ts.result_preview:
                    print(f"      result: {ts.result_preview[:200]}")

    if trace.model_requests:
        print(f"  Model requests ({len(trace.model_requests)}):")
        for mr in trace.model_requests:
            tc_names = [p.get("tool_name", p.get("name", "?")) for p in mr.tool_calls]
            tc_str = f"  tools={tc_names}" if tc_names else ""
            print(
                f"    [req {mr.request_index}]"
                f"  in={mr.input_tokens} out={mr.output_tokens}"
                f"  finish={mr.finish_reason}{tc_str}"
            )
            if (verbose or tc_names) and mr.thinking_excerpt:
                print(f"      thinking: {mr.thinking_excerpt[:300]!r}")

    if verbose and trace.timeline:
        print("  Timeline:")
        for row in trace.timeline:
            print(
                f"    +{row.elapsed_ms:>6}ms  [{row.duration_ms}ms]  {row.span_name}"
                + (f"  {row.detail}" if row.detail and row.detail != "—" else "")
            )


def print_rca(label: str, trace: TurnTrace, dimensions: dict[str, bool]) -> None:
    """Print full RCA dump for a failed turn.

    Dumps: failed dimension names, full tool args + results (or exception
    tracebacks), full model thinking text, and the complete span timeline.
    """
    failed_dims = [k for k, v in dimensions.items() if not v]
    print(f"\n  !! RCA for {label} — failed dimensions: {failed_dims}")

    if trace.tool_spans:
        print("  Tool details:")
        for ts in trace.tool_spans:
            print(f"    {ts.tool_name}")
            if ts.arguments:
                print(f"      args: {ts.arguments}")
            if ts.exception_events:
                for ev in ts.exception_events:
                    attrs = ev.get("attributes", {})
                    print(
                        f"      EXCEPTION: {attrs.get('exception.type', '')} — "
                        f"{attrs.get('exception.message', '')}"
                    )
                    tb = attrs.get("exception.stacktrace", "")
                    if tb:
                        for ln in tb.strip().splitlines()[-5:]:
                            print(f"        {ln}")
            elif ts.result_full:
                print(f"      result: {ts.result_full[:500]}")

    if trace.model_requests:
        print("  Model thinking (full):")
        for mr in trace.model_requests:
            if mr.thinking_full:
                print(f"    [req {mr.request_index}] {mr.thinking_full[:800]!r}")
            elif mr.text_response:
                print(f"    [req {mr.request_index}] text: {mr.text_response[:400]!r}")

    if trace.timeline:
        print("  Full timeline:")
        for row in trace.timeline:
            print(
                f"    +{row.elapsed_ms:>6}ms  [{row.duration_ms}ms]  {row.span_name}"
                + (f"  {row.detail}" if row.detail and row.detail != "—" else "")
            )
