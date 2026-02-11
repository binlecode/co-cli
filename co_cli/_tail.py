"""Real-time span tail viewer — polls OTel SQLite DB and prints spans."""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.text import Text

from co_cli.config import DATA_DIR
from co_cli._trace_viewer import format_duration, get_span_type

# Rich style per span type (matches trace_viewer.py color scheme)
TYPE_STYLES = {
    "agent": "cyan",
    "model": "magenta",
    "tool": "yellow",
}

DB_PATH = DATA_DIR / "co-cli.db"


def _extract_messages(attrs: dict) -> list[tuple[str, str]]:
    """Extract output message parts from model span attributes.

    Returns list of (part_type, content) tuples — e.g. ("text", "Hello ..."),
    ("thinking", "Let me consider ...").
    """
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
    """Extract key attributes as a compact string based on span type."""
    parts: list[str] = []

    if span_type == "agent":
        model = attrs.get("gen_ai.request.model", "")
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

    elif span_type == "tool":
        tool_name = attrs.get("gen_ai.tool.name", "")
        if tool_name:
            parts.append(f"tool={tool_name}")
        tool_args = attrs.get("tool_arguments", "")
        if tool_args:
            truncated = tool_args[:80] + ("\u2026" if len(tool_args) > 80 else "")
            parts.append(f"args={truncated}")

    return "  ".join(parts)


def _format_span_line(row: sqlite3.Row, verbose: bool = False) -> list[Text]:
    """Format a single span row into Rich Text lines.

    Returns a list — normally one line, but in verbose mode model spans
    get additional indented lines for LLM output content.
    """
    name = row["name"]
    span_type = get_span_type(name)
    style = TYPE_STYLES.get(span_type, "dim")
    status_code = row["status_code"] or "UNSET"
    duration_ms = row["duration_ms"]

    # Timestamp: nanoseconds epoch → local HH:MM:SS
    start_ns = row["start_time"]
    ts = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).astimezone()
    ts_str = ts.strftime("%H:%M:%S")

    # Parse attributes
    attrs: dict = {}
    if row["attributes"]:
        try:
            attrs = json.loads(row["attributes"])
        except (json.JSONDecodeError, TypeError):
            pass

    attr_str = _extract_attrs(span_type, attrs)
    dur_str = format_duration(duration_ms)

    # Build the header line
    line = Text()
    line.append(ts_str, style="dim")
    line.append("  ")
    line.append(f"{span_type:<6}", style=style)
    line.append(" ")
    line.append(f"{name:<30}", style=f"bold {style}")
    line.append(" ")
    if attr_str:
        line.append(attr_str, style="dim")
        line.append("  ")
    line.append(dur_str, style="dim")

    if status_code == "ERROR":
        line.append("  ")
        line.append("ERROR", style="bold red")

    lines = [line]

    # Verbose: append LLM output for model spans
    if verbose and span_type == "model":
        for ptype, content in _extract_messages(attrs):
            tag_style = "dim italic" if ptype == "thinking" else "white"
            tag = "[thinking] " if ptype == "thinking" else ""
            for text_line in content.splitlines():
                vline = Text()
                vline.append("           │ ", style="dim")
                if tag:
                    vline.append(tag, style="dim italic")
                    tag = ""  # only on first line
                vline.append(text_line, style=tag_style)
                lines.append(vline)

    return lines


def _query_recent(
    conn: sqlite3.Connection,
    limit: int,
    trace_id: str | None,
    span_filter: str | None,
) -> list[sqlite3.Row]:
    """Fetch the N most recent spans (for startup display)."""
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
    """Fetch spans newer than high_water_mark."""
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
    db_path = DB_PATH

    if not db_path.exists():
        console.print("[yellow]No database found. Run 'co chat' first.[/yellow]")
        return

    # Determine span filter
    span_filter: str | None = None
    if tools_only:
        span_filter = "tools"
    elif models_only:
        span_filter = "models"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Show recent spans
    recent = _query_recent(conn, last, trace_id, span_filter)
    high_water = 0

    if recent:
        for row in recent:
            for line in _format_span_line(row, verbose=verbose):
                console.print(line)
            if row["start_time"] > high_water:
                high_water = row["start_time"]
    else:
        console.print("[dim]No spans found.[/dim]")

    if no_follow:
        conn.close()
        return

    # Follow mode
    console.print("[dim]Following new spans... (Ctrl+C to stop)[/dim]")
    try:
        while True:
            time.sleep(poll_interval)
            new_rows = _query_new(conn, high_water, trace_id, span_filter)
            for row in new_rows:
                for line in _format_span_line(row, verbose=verbose):
                    console.print(line)
                if row["start_time"] > high_water:
                    high_water = row["start_time"]
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
    finally:
        conn.close()
