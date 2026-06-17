"""Snapshot tree view for a single trace_id from the structured-log spans file.

Reads all records matching the given ``trace_id`` from the current spans log
plus any rotated backups, builds a parent/child tree using ``parent_span_id``,
and renders an indented depth-uncapped tree (completed traces are bounded).

Distinct from ``co tail`` (live append-only follow): this is a one-shot
snapshot read with no follow mode.
"""

import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from co_cli.config.core import LOGS_DIR
from co_cli.display.core import console

_SPANS_LOG = LOGS_DIR / "co-cli-spans.jsonl"

KIND_STYLES = {
    "agent": "cyan",
    "model": "magenta",
    "tool": "yellow",
    "co": "white",
}


def _read_records_for_trace(trace_id: str, *, log_path: Path | None = None) -> list[dict]:
    """Read all records matching ``trace_id`` from the live log and rotated backups."""
    base = log_path or _SPANS_LOG
    candidates: list[Path] = []
    if base.exists():
        candidates.append(base)
    parent = base.parent
    if parent.exists():
        candidates.extend(sorted(parent.glob(f"{base.name}.*")))

    records: list[dict] = []
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw_line in f:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        rec = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("trace_id") == trace_id:
                        records.append(rec)
        except OSError:
            continue
    return records


def _build_tree(records: list[dict]) -> list[dict]:
    """Group records by ``parent_span_id`` and attach a ``children`` list to each.

    Returns the list of root records (those whose ``parent_span_id`` is either
    None or points outside the set). Siblings are sorted by ``start_ts``.
    """
    by_id: dict[str, dict] = {}
    for rec in records:
        sid = rec.get("span_id")
        if sid:
            entry = dict(rec)
            entry["children"] = []
            by_id[sid] = entry

    roots: list[dict] = []
    for entry in by_id.values():
        parent_id = entry.get("parent_span_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(entry)
        else:
            roots.append(entry)

    def _sort_key(rec: dict) -> str:
        return rec.get("start_ts") or ""

    roots.sort(key=_sort_key)
    for entry in by_id.values():
        entry["children"].sort(key=_sort_key)

    return roots


def _format_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms < 1:
        return f"{ms * 1000:.0f}µs"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _summary_attrs(kind: str, attrs: dict) -> str:
    parts: list[str] = []
    if kind == "model":
        in_tok = attrs.get("co.model.tokens.input")
        out_tok = attrs.get("co.model.tokens.output")
        if in_tok is not None:
            parts.append(f"in={in_tok}")
        if out_tok is not None:
            parts.append(f"out={out_tok}")
    elif kind == "tool":
        tool_name = attrs.get("co.tool.name")
        if tool_name:
            parts.append(f"tool={tool_name}")
    elif kind == "agent":
        req = attrs.get("co.agent.requests_used")
        if req is not None:
            parts.append(f"req={req}")
    return "  ".join(parts)


def _format_node_line(rec: dict, depth: int) -> Text:
    indent = "  " * depth
    branch = "└─ " if depth > 0 else ""
    kind = rec.get("kind", "co")
    style = KIND_STYLES.get(kind, "dim")
    name = rec.get("name", "?")
    duration_ms = rec.get("duration_ms")
    status = rec.get("status", "OK")
    attrs = rec.get("attributes") or {}

    line = Text()
    line.append(f"{indent}{branch}", style="dim")
    line.append(f"{kind:<6}", style=style)
    line.append(" ")
    line.append(name, style=f"bold {style}")
    attr_str = _summary_attrs(kind, attrs)
    if attr_str:
        line.append("  ")
        line.append(attr_str, style="dim")
    line.append("  ")
    line.append(_format_duration(duration_ms), style="dim")
    if status == "ERROR":
        line.append("  ")
        line.append("ERROR", style="bold red")
        status_msg = rec.get("status_msg")
        if status_msg:
            line.append(f": {status_msg}", style="red")
    return line


def _render_tree(console: Console, roots: list[dict], depth: int = 0) -> None:
    for rec in roots:
        console.print(_format_node_line(rec, depth))
        children = rec.get("children") or []
        _render_tree(console, children, depth + 1)


def _header(trace_id: str, records: list[dict]) -> Text:
    starts = [r.get("start_ts") for r in records if r.get("start_ts")]
    when = ""
    if starts:
        try:
            dt = datetime.fromisoformat(starts[0].replace("Z", "+00:00"))
            when = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            when = ""
    label = f"trace {trace_id}"
    if when:
        label += f"  ({when})"
    label += f"  · {len(records)} record(s)"
    return Text(label, style="bold cyan")


def render_trace(trace_id: str, *, log_path: Path | None = None) -> None:
    """Render one trace as a snapshot tree."""
    records = _read_records_for_trace(trace_id, log_path=log_path)
    if not records:
        console.print(f"[yellow]No records for trace {trace_id}[/yellow]")
        return
    console.print(_header(trace_id, records))
    roots = _build_tree(records)
    _render_tree(console, roots)


__all__ = ["render_trace"]
