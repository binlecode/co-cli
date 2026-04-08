"""Real-time span tail viewer — polls OTel SQLite DB and prints spans."""

import json
import sqlite3
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.text import Text

from co_cli.config._core import LOGS_DB
from co_cli.observability._viewer import format_duration, get_span_type

TYPE_STYLES = {
    "agent": "cyan",
    "model": "magenta",
    "tool": "yellow",
}

# Wide enough for the longest span names (e.g. "chat qwen3:30b-a3b-thinking-2507-q8_0-agentic")
SPAN_NAME_WIDTH = 48

DB_PATH = LOGS_DB


def _extract_output_messages(attrs: dict) -> list[tuple[str, str]]:
    """Return (part_type, content) pairs from gen_ai.output.messages."""
    raw = attrs.get("gen_ai.output.messages", "")
    if not raw:
        return []
    try:
        messages = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    parts: list[tuple[str, str]] = []
    for msg in messages:
        for part in msg.get("parts", []):
            ptype = part.get("type", "")
            content = part.get("content", "")
            if ptype in ("text", "thinking") and content:
                parts.append((ptype, content))
    return parts


def _extract_attrs(span_type: str, attrs: dict) -> str:
    """Compact single-line attribute summary shown on every span."""
    parts: list[str] = []

    if span_type == "agent":
        # pydantic-ai stores model name under "model_name" on invoke_agent spans
        model = attrs.get("model_name", "") or attrs.get("gen_ai.request.model", "")
        if model:
            parts.append(f"model={model}")
        input_tok = attrs.get("gen_ai.usage.input_tokens")
        output_tok = attrs.get("gen_ai.usage.output_tokens")
        if input_tok is not None and output_tok is not None:
            parts.append(f"tokens={input_tok}\u2192{output_tok}")

    elif span_type == "model":
        input_tok = attrs.get("gen_ai.usage.input_tokens")
        output_tok = attrs.get("gen_ai.usage.output_tokens")
        if input_tok is not None:
            parts.append(f"in={input_tok}")
        if output_tok is not None:
            parts.append(f"out={output_tok}")
        finish = attrs.get("gen_ai.response.finish_reasons", "")
        if finish:
            parts.append(f"finish={finish}")

    elif span_type == "tool":
        tool_name = attrs.get("gen_ai.tool.name", "")
        if tool_name:
            parts.append(f"tool={tool_name}")
        # pydantic-ai v3 OTel attribute name
        tool_args = attrs.get("gen_ai.tool.call.arguments", "")
        if tool_args:
            raw = tool_args if isinstance(tool_args, str) else json.dumps(tool_args)
            truncated = raw[:80] + ("\u2026" if len(raw) > 80 else "")
            parts.append(f"args={truncated}")

    return "  ".join(parts)


def _vline(content: str, text_style: str = "white") -> Text:
    t = Text()
    t.append("           \u2502 ", style="dim")
    t.append(content, style=text_style)
    return t


def _verbose_detail_lines(span_type: str, attrs: dict) -> list[Text]:
    """Indented detail block appended after the summary line in verbose mode."""
    lines: list[Text] = []

    if span_type == "agent":
        final = attrs.get("final_result", "")
        if final:
            lines.append(_vline("[final]", text_style="dim"))
            for tl in final.splitlines():
                lines.append(_vline(f"  {tl}", text_style="green"))

    elif span_type == "tool":
        raw_args = attrs.get("gen_ai.tool.call.arguments", "")
        if raw_args:
            arg_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
            try:
                arg_str = json.dumps(json.loads(arg_str), indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
            lines.append(_vline("args:", text_style="dim"))
            for tl in arg_str.splitlines():
                lines.append(_vline(f"  {tl}"))

        result = attrs.get("gen_ai.tool.call.result", "")
        if result:
            result_str = result if isinstance(result, str) else json.dumps(result)
            try:
                result_str = json.dumps(json.loads(result_str), indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
            lines.append(_vline("result:", text_style="dim"))
            for tl in result_str.splitlines():
                lines.append(_vline(f"  {tl}"))

    elif span_type == "model":
        input_raw = attrs.get("gen_ai.input.messages", "")
        if input_raw:
            try:
                input_msgs = json.loads(input_raw) if isinstance(input_raw, str) else input_raw
                # System prompt: first line only — identifies which persona/agent is active
                for msg in input_msgs:
                    if msg.get("role") == "system":
                        for part in msg.get("parts", []):
                            if part.get("type") == "text":
                                first_line = part.get("content", "").split("\n")[0][:120]
                                lines.append(_vline(f"[system] {first_line}", text_style="dim"))
                        break
                # Last user message — what triggered this model call
                for msg in reversed(input_msgs):
                    if msg.get("role") == "user":
                        lines.append(_vline("[user]", text_style="dim"))
                        for part in msg.get("parts", []):
                            if part.get("type") == "text":
                                for tl in part.get("content", "").splitlines():
                                    lines.append(_vline(f"  {tl}", text_style="cyan"))
                        break
            except (json.JSONDecodeError, TypeError):
                pass

        for ptype, content in _extract_output_messages(attrs):
            if ptype == "thinking":
                lines.append(_vline("[thinking]", text_style="dim italic"))
                for tl in content.splitlines():
                    lines.append(_vline(f"  {tl}", text_style="dim italic"))
                lines.append(_vline("", text_style="dim"))
            else:
                lines.append(_vline("[response]", text_style="dim"))
                for tl in content.splitlines():
                    lines.append(_vline(f"  {tl}", text_style="white"))

    return lines


def _format_span_line(row: sqlite3.Row, verbose: bool = False) -> list[Text]:
    """Format a span row into one summary line plus optional verbose detail lines."""
    name = row["name"]
    span_type = get_span_type(name)
    style = TYPE_STYLES.get(span_type, "dim")
    status_code = row["status_code"] or "UNSET"
    duration_ms = row["duration_ms"]

    start_ns = row["start_time"]
    ts = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).astimezone()
    ts_str = ts.strftime("%H:%M:%S")

    attrs: dict = {}
    if row["attributes"]:
        try:
            attrs = json.loads(row["attributes"])
        except (json.JSONDecodeError, TypeError):
            pass

    attr_str = _extract_attrs(span_type, attrs)
    dur_str = format_duration(duration_ms)

    line = Text()
    line.append(ts_str, style="dim")
    line.append("  ")
    line.append(f"{span_type:<6}", style=style)
    line.append(" ")
    line.append(f"{name:<{SPAN_NAME_WIDTH}}", style=f"bold {style}")
    line.append(" ")
    if attr_str:
        line.append(attr_str, style="dim")
        line.append("  ")
    line.append(dur_str, style="dim")
    if status_code == "ERROR":
        line.append("  ")
        line.append("ERROR", style="bold red")

    lines = [line]
    if verbose:
        lines.extend(_verbose_detail_lines(span_type, attrs))

    return lines


def _trace_separator(trace_id: str, start_ns: int) -> Text:
    ts = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).astimezone()
    ts_str = ts.strftime("%H:%M:%S")
    short_id = trace_id[:8] if trace_id else "?"
    label = f" trace:{short_id}  {ts_str} "
    bar = "\u2500" * 60
    t = Text()
    t.append(f"\u250c\u2500\u2500{label}{bar}", style="dim")
    return t


def _print_spans(
    console: Console,
    rows: list[sqlite3.Row],
    verbose: bool,
    last_trace_id: str | None = None,
) -> str | None:
    """Print rows with trace separators; return the last trace_id seen."""
    for row in rows:
        tid = row["trace_id"]
        if tid != last_trace_id:
            console.print(_trace_separator(tid, row["start_time"]))
            last_trace_id = tid
        for line in _format_span_line(row, verbose=verbose):
            console.print(line)
    return last_trace_id


def _query_recent(
    conn: sqlite3.Connection,
    limit: int,
    trace_id: str | None,
    span_filter: str | None,
) -> list[sqlite3.Row]:
    where_clauses = []
    params: list = []
    if trace_id:
        where_clauses.append("trace_id = ?")
        params.append(trace_id)
    if span_filter == "tools":
        where_clauses.append("name LIKE '%tool%'")
    elif span_filter == "models":
        where_clauses.append("(name LIKE '%model%' OR name LIKE '%chat%')")
    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    rows = conn.execute(
        f"SELECT * FROM spans {where} ORDER BY start_time DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return list(reversed(rows))


def _query_new(
    conn: sqlite3.Connection,
    high_water: int,
    trace_id: str | None,
    span_filter: str | None,
) -> list[sqlite3.Row]:
    where_clauses = ["start_time > ?"]
    params: list = [high_water]
    if trace_id:
        where_clauses.append("trace_id = ?")
        params.append(trace_id)
    if span_filter == "tools":
        where_clauses.append("name LIKE '%tool%'")
    elif span_filter == "models":
        where_clauses.append("(name LIKE '%model%' OR name LIKE '%chat%')")
    where = f"WHERE {' AND '.join(where_clauses)}"
    return conn.execute(
        f"SELECT * FROM spans {where} ORDER BY start_time ASC",
        params,
    ).fetchall()


def run_tail(
    trace_id: str | None = None,
    tools_only: bool = False,
    models_only: bool = False,
    poll_interval: float = 1.0,
    no_follow: bool = False,
    last: int = 20,
    verbose: bool = False,
) -> None:
    """Main tail loop — prints recent spans then polls for new ones."""
    console = Console()

    if not DB_PATH.exists():
        console.print("[yellow]No database found. Run 'co chat' first.[/yellow]")
        return

    span_filter: str | None = None
    if tools_only:
        span_filter = "tools"
    elif models_only:
        span_filter = "models"

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    recent = _query_recent(conn, last, trace_id, span_filter)
    high_water = 0
    last_trace_id: str | None = None

    if recent:
        last_trace_id = _print_spans(console, recent, verbose)
        high_water = max(row["start_time"] for row in recent)
    else:
        console.print("[dim]No spans found.[/dim]")

    if no_follow:
        conn.close()
        return

    console.print("[dim]Following new spans... (Ctrl+C to stop)[/dim]")
    try:
        while True:
            time.sleep(poll_interval)
            new_rows = _query_new(conn, high_water, trace_id, span_filter)
            if new_rows:
                last_trace_id = _print_spans(console, new_rows, verbose, last_trace_id)
                high_water = max(row["start_time"] for row in new_rows)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
    finally:
        conn.close()
