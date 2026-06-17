"""Live tail viewer for the structured-log spans file.

Polls ``~/.co-cli/logs/co-cli-spans.jsonl`` and renders one summary line per
record. Detects rotation via inode change. Snapshot tree view is served by
``co trace <trace_id>`` — this command is append-only / live-follow only.
"""

import json
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from co_cli.config.core import LOGS_DIR
from co_cli.display.core import console

_SPANS_LOG = LOGS_DIR / "co-cli-spans.jsonl"

TYPE_STYLES = {
    "agent": "cyan",
    "model": "magenta",
    "tool": "yellow",
    "co": "white",
}

SPAN_NAME_WIDTH = 48


def _format_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms < 1:
        return f"{ms * 1000:.0f}µs"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _agent_attrs(attrs: dict) -> list[str]:
    parts: list[str] = []
    model = attrs.get("co.agent.model")
    if model:
        parts.append(f"model={model}")
    requests_used = attrs.get("co.agent.requests_used")
    if requests_used is not None:
        parts.append(f"req={requests_used}")
    return parts


def _model_attrs(attrs: dict) -> list[str]:
    parts: list[str] = []
    in_tok = attrs.get("co.model.tokens.input")
    out_tok = attrs.get("co.model.tokens.output")
    if in_tok is not None:
        parts.append(f"in={in_tok}")
    if out_tok is not None:
        parts.append(f"out={out_tok}")
    finish = attrs.get("co.model.finish_reason")
    if finish:
        parts.append(f"finish={finish}")
    return parts


def _tool_attrs(attrs: dict, *, args_truncate: int = 80) -> list[str]:
    parts: list[str] = []
    tool_name = attrs.get("co.tool.name")
    if tool_name:
        parts.append(f"tool={tool_name}")
    args = attrs.get("co.tool.args")
    if args:
        arg_str = args if isinstance(args, str) else json.dumps(args)
        if len(arg_str) > args_truncate:
            arg_str = arg_str[:args_truncate] + "…"
        parts.append(f"args={arg_str}")
    return parts


def _summary_attrs(kind: str, attrs: dict) -> str:
    if kind == "agent":
        return "  ".join(_agent_attrs(attrs))
    if kind == "model":
        return "  ".join(_model_attrs(attrs))
    if kind == "tool":
        return "  ".join(_tool_attrs(attrs))
    return ""


def _vline(content: str, text_style: str = "white") -> Text:
    t = Text()
    t.append("           │ ", style="dim")
    t.append(content, style=text_style)
    return t


def _render_agent_detail(attrs: dict) -> list[Text]:
    lines: list[Text] = []
    final = attrs.get("co.agent.final_result")
    if final:
        lines.append(_vline("[final]", text_style="dim"))
        for tl in str(final).splitlines():
            lines.append(_vline(f"  {tl}", text_style="green"))
    return lines


def _render_tool_detail(attrs: dict) -> list[Text]:
    lines: list[Text] = []
    raw_args = attrs.get("co.tool.args")
    if raw_args:
        arg_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
        try:
            arg_str = json.dumps(json.loads(arg_str), indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
        lines.append(_vline("args:", text_style="dim"))
        for tl in arg_str.splitlines():
            lines.append(_vline(f"  {tl}"))
    result = attrs.get("co.tool.result")
    if result:
        result_str = result if isinstance(result, str) else json.dumps(result)
        try:
            result_str = json.dumps(json.loads(result_str), indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
        lines.append(_vline("result:", text_style="dim"))
        for tl in result_str.splitlines():
            lines.append(_vline(f"  {tl}"))
    return lines


def _safe_json_load(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return value


def _render_model_input(raw_in: object) -> list[Text]:
    msgs = _safe_json_load(raw_in)
    if not isinstance(msgs, list):
        return []
    for msg in reversed(msgs):
        if not (isinstance(msg, dict) and msg.get("role") == "request"):
            continue
        for part in msg.get("parts", []):
            if isinstance(part, dict) and part.get("type") == "user":
                out: list[Text] = [_vline("[user]", text_style="dim")]
                for tl in str(part.get("content", "")).splitlines():
                    out.append(_vline(f"  {tl}", text_style="cyan"))
                return out
        break
    return []


def _render_model_output_part(part: dict) -> list[Text]:
    ptype = part.get("type", "")
    content = str(part.get("content", ""))
    if ptype == "thinking" and content:
        out = [_vline("[thinking]", text_style="dim italic")]
        for tl in content.splitlines():
            out.append(_vline(f"  {tl}", text_style="dim italic"))
        return out
    if ptype == "text" and content:
        out = [_vline("[response]", text_style="dim")]
        for tl in content.splitlines():
            out.append(_vline(f"  {tl}", text_style="white"))
        return out
    if ptype == "tool_call":
        return [_vline(f"[tool_call] {part.get('tool_name', '?')}", text_style="yellow")]
    return []


def _render_model_detail(attrs: dict) -> list[Text]:
    lines = _render_model_input(attrs.get("co.model.input"))
    output_parts = _safe_json_load(attrs.get("co.model.output"))
    if isinstance(output_parts, list):
        for part in output_parts:
            if isinstance(part, dict):
                lines.extend(_render_model_output_part(part))
    return lines


def _detail_lines(kind: str, attrs: dict) -> list[Text]:
    if kind == "agent":
        return _render_agent_detail(attrs)
    if kind == "tool":
        return _render_tool_detail(attrs)
    if kind == "model":
        return _render_model_detail(attrs)
    return []


def _format_line(record: dict, detail: bool) -> list[Text]:
    name = record.get("name", "?")
    kind = record.get("kind", "co")
    style = TYPE_STYLES.get(kind, "dim")
    status = record.get("status", "OK")
    duration_ms = record.get("duration_ms")
    attrs = record.get("attributes") or {}

    ts_str = ""
    start_ts = record.get("start_ts") or record.get("ts")
    if start_ts:
        try:
            dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
            ts_str = dt.astimezone().strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            ts_str = ""

    attr_str = _summary_attrs(kind, attrs)
    dur_str = _format_duration(duration_ms)

    line = Text()
    if ts_str:
        line.append(ts_str, style="dim")
        line.append("  ")
    line.append(f"{kind:<6}", style=style)
    line.append(" ")
    line.append(f"{name:<{SPAN_NAME_WIDTH}}", style=f"bold {style}")
    line.append(" ")
    if attr_str:
        line.append(attr_str, style="dim")
        line.append("  ")
    line.append(dur_str, style="dim")
    if status == "ERROR":
        line.append("  ")
        line.append("ERROR", style="bold red")

    lines = [line]
    if detail:
        lines.extend(_detail_lines(kind, attrs))
    return lines


def _record_passes_filters(
    record: dict,
    *,
    trace_id: str | None,
    tools_only: bool,
    models_only: bool,
) -> bool:
    if trace_id and record.get("trace_id") != trace_id:
        return False
    kind = record.get("kind", "co")
    if tools_only and kind != "tool":
        return False
    return not (models_only and kind != "model")


def _read_records_from(path: Path, start_byte: int = 0) -> tuple[list[dict], int]:
    """Read records from ``start_byte`` to EOF. Returns (records, new_byte_offset)."""
    if not path.exists():
        return [], start_byte
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        f.seek(start_byte)
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                records.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
        end_byte = f.tell()
    return records, end_byte


def _tail_last_n_records(path: Path, n: int) -> list[dict]:
    """Return the last N records by streaming the file (handles arbitrary file sizes)."""
    if not path.exists():
        return []
    from collections import deque

    keep: deque[dict] = deque(maxlen=n)
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                keep.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
    return list(keep)


def _inode_of(path: Path) -> int | None:
    try:
        return path.stat().st_ino
    except OSError:
        return None


def _print_records(
    console: Console,
    records: list[dict],
    detail: bool,
    last_trace_id: str | None,
) -> str | None:
    for rec in records:
        tid = rec.get("trace_id")
        if tid and tid != last_trace_id:
            console.print(_trace_separator(tid, rec.get("start_ts") or rec.get("ts", "")))
            last_trace_id = tid
        for line in _format_line(rec, detail=detail):
            console.print(line)
    return last_trace_id


def _trace_separator(trace_id: str, start_ts: str) -> Text:
    ts_str = ""
    if start_ts:
        try:
            dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
            ts_str = dt.astimezone().strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            ts_str = ""
    short_id = trace_id[:10] if trace_id else "?"
    label = f" trace:{short_id}  {ts_str} "
    bar = "─" * 60
    t = Text()
    t.append(f"┌──{label}{bar}", style="dim")
    return t


def run_tail(
    *,
    log_path: Path | None = None,
    trace_id: str | None = None,
    tools_only: bool = False,
    models_only: bool = False,
    poll_interval: float = 0.1,
    no_follow: bool = False,
    last: int = 20,
    detail: bool = False,
) -> None:
    """Main tail loop — print last N records, then follow new records via append + rotation."""
    path = log_path or _SPANS_LOG

    if not path.exists():
        console.print(f"[yellow]No spans log at {path}. Run 'co chat' first.[/yellow]")
        return

    initial = _tail_last_n_records(path, last)
    initial = [
        r
        for r in initial
        if _record_passes_filters(
            r, trace_id=trace_id, tools_only=tools_only, models_only=models_only
        )
    ]

    last_trace_id: str | None = None
    if initial:
        last_trace_id = _print_records(console, initial, detail, last_trace_id)
    else:
        console.print("[dim]No spans found.[/dim]")

    if no_follow:
        return

    console.print("[dim]Following new spans... (Ctrl+C to stop)[/dim]")
    offset = path.stat().st_size if path.exists() else 0
    current_inode = _inode_of(path)

    try:
        while True:
            time.sleep(poll_interval)
            new_inode = _inode_of(path)
            if new_inode is not None and new_inode != current_inode:
                offset = 0
                current_inode = new_inode
            records, offset = _read_records_from(path, offset)
            if not records:
                continue
            records = [
                r
                for r in records
                if _record_passes_filters(
                    r, trace_id=trace_id, tools_only=tools_only, models_only=models_only
                )
            ]
            last_trace_id = _print_records(console, records, detail, last_trace_id)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


__all__ = ["run_tail"]
