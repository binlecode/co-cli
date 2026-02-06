"""Generate static HTML trace viewer with nested spans."""

import json
import sqlite3
from pathlib import Path
from co_cli.config import DATA_DIR

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Co CLI - Trace Viewer</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
            line-height: 1.5;
        }}
        h1 {{ color: #00d4ff; margin-bottom: 20px; }}
        .trace {{
            background: #16213e;
            border-radius: 8px;
            margin-bottom: 16px;
            overflow: hidden;
        }}
        .trace-header {{
            background: #0f3460;
            padding: 12px 16px;
            border-bottom: 1px solid #1a1a2e;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
        }}
        .trace-header:hover {{ background: #1a4a80; }}
        .trace-id {{
            font-size: 12px;
            color: #888;
            font-family: monospace;
        }}
        .trace-time {{ color: #00d4ff; font-size: 14px; }}
        .spans {{ padding: 8px 0; }}
        .spans.collapsed {{ display: none; }}
        .span {{
            padding: 6px 16px;
            border-left: 3px solid transparent;
        }}
        .span-row {{
            display: flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
        }}
        .span-row:hover {{ background: rgba(255,255,255,0.05); }}
        .span-toggle {{
            width: 16px;
            color: #666;
            font-size: 10px;
        }}
        .span-toggle.has-children {{ color: #00d4ff; cursor: pointer; }}
        .span-name {{
            font-weight: 500;
            flex: 1;
        }}
        .span-name.agent {{ color: #00d4ff; }}
        .span-name.tool {{ color: #f39c12; }}
        .span-name.model {{ color: #9b59b6; }}
        .span-duration {{
            font-size: 12px;
            color: #888;
            min-width: 80px;
            text-align: right;
        }}
        .span-status {{
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 4px;
            min-width: 50px;
            text-align: center;
        }}
        .span-status.OK {{ background: #27ae60; color: white; }}
        .span-status.ERROR {{ background: #e74c3c; color: white; }}
        .span-status.UNSET {{ background: #555; color: #aaa; }}
        .span-children {{ margin-left: 20px; }}
        .span-children.collapsed {{ display: none; }}
        .span-details {{
            margin: 8px 0 8px 36px;
            padding: 8px 12px;
            background: rgba(0,0,0,0.3);
            border-radius: 4px;
            font-size: 12px;
            display: none;
        }}
        .span-details.expanded {{ display: block; }}
        .span-attr {{
            display: flex;
            gap: 8px;
            margin: 4px 0;
        }}
        .span-attr-key {{ color: #888; min-width: 140px; }}
        .span-attr-value {{
            color: #aaa;
            word-break: break-all;
            max-width: 600px;
        }}
        .span-attr-value.truncated {{ cursor: pointer; }}
        .span-attr-value .full-value {{
            display: none;
            white-space: pre-wrap;
            background: #0a0a15;
            padding: 8px;
            border-radius: 4px;
            margin-top: 4px;
            max-height: 300px;
            overflow-y: auto;
        }}
        .span-attr-value.expanded .full-value {{ display: block; }}
        .span-attr-value.expanded .truncated-value {{ display: none; }}
        .show-more {{
            color: #00d4ff;
            font-size: 11px;
            margin-left: 4px;
            cursor: pointer;
        }}
        .show-more:hover {{ text-decoration: underline; }}
        .waterfall {{
            height: 4px;
            background: #333;
            border-radius: 2px;
            margin-top: 4px;
            position: relative;
            overflow: hidden;
        }}
        .waterfall-bar {{
            height: 100%;
            border-radius: 2px;
            position: absolute;
        }}
        .waterfall-bar.agent {{ background: #00d4ff; }}
        .waterfall-bar.tool {{ background: #f39c12; }}
        .waterfall-bar.model {{ background: #9b59b6; }}
        .empty {{
            text-align: center;
            padding: 40px;
            color: #666;
        }}
        .stats {{
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .stat {{
            background: #16213e;
            padding: 12px 20px;
            border-radius: 8px;
        }}
        .stat-value {{ font-size: 24px; color: #00d4ff; }}
        .stat-label {{ color: #888; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>Co CLI - Trace Viewer</h1>
    <div class="stats">
        <div class="stat">
            <div class="stat-value">{trace_count}</div>
            <div class="stat-label">Traces</div>
        </div>
        <div class="stat">
            <div class="stat-value">{span_count}</div>
            <div class="stat-label">Spans</div>
        </div>
        <div class="stat">
            <div class="stat-value">{tool_count}</div>
            <div class="stat-label">Tool Calls</div>
        </div>
    </div>
    {traces_html}
    <script>
        // Toggle trace spans
        document.querySelectorAll('.trace-header').forEach(header => {{
            header.addEventListener('click', () => {{
                header.nextElementSibling.classList.toggle('collapsed');
            }});
        }});
        // Toggle span children
        document.querySelectorAll('.span-toggle.has-children').forEach(toggle => {{
            toggle.addEventListener('click', (e) => {{
                e.stopPropagation();
                const span = toggle.closest('.span');
                const children = span.querySelector('.span-children');
                if (children) {{
                    children.classList.toggle('collapsed');
                    toggle.textContent = children.classList.contains('collapsed') ? '▶' : '▼';
                }}
            }});
        }});
        // Toggle span details on row click
        document.querySelectorAll('.span-row').forEach(row => {{
            row.addEventListener('click', () => {{
                const details = row.nextElementSibling?.nextElementSibling;
                if (details && details.classList.contains('span-details')) {{
                    details.classList.toggle('expanded');
                }}
            }});
        }});
        // Toggle truncated attribute values
        document.querySelectorAll('.show-more').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                e.stopPropagation();
                const attrValue = btn.closest('.span-attr-value');
                attrValue.classList.toggle('expanded');
                btn.textContent = attrValue.classList.contains('expanded') ? '[collapse]' : '[expand]';
            }});
        }});
    </script>
</body>
</html>
"""

TRACE_TEMPLATE = """
<div class="trace">
    <div class="trace-header">
        <div>
            <span class="trace-time">{time}</span>
            <span class="trace-id">trace: {trace_id}</span>
        </div>
        <div class="trace-duration">{total_duration}</div>
    </div>
    <div class="spans">
{spans_html}
    </div>
</div>
"""

SPAN_TEMPLATE = """<div class="span">
    <div class="span-row">
        <span class="span-toggle {toggle_class}">{toggle_icon}</span>
        <span class="span-name {span_type}">{name}</span>
        <span class="span-duration">{duration}</span>
        <span class="span-status {status}">{status}</span>
    </div>
    <div class="waterfall">
        <div class="waterfall-bar {span_type}" style="left: {bar_left}%; width: {bar_width}%;"></div>
    </div>
    <div class="span-details">
{attributes_html}
    </div>
    {children_html}
</div>
"""


def get_span_type(name: str) -> str:
    """Determine span type for styling."""
    if "agent" in name.lower():
        return "agent"
    elif "tool" in name.lower():
        return "tool"
    elif "model" in name.lower() or "chat" in name.lower():
        return "model"
    return "agent"


def get_span_color(span_type: str) -> str:
    """Get color for span type."""
    colors = {
        "agent": "#00d4ff",
        "tool": "#f39c12",
        "model": "#9b59b6",
    }
    return colors.get(span_type, "#00d4ff")


def format_duration(ms: float | None) -> str:
    """Format duration in human readable form."""
    if ms is None:
        return "-"
    if ms < 1:
        return f"{ms*1000:.0f}µs"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms/1000:.2f}s"


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_attr_value(value: str, truncate_at: int = 200) -> str:
    """Format attribute value with expandable view for long content."""
    value_str = str(value)

    if len(value_str) <= truncate_at:
        return f'<span class="span-attr-value">{escape_html(value_str)}</span>'

    # Try to pretty-print JSON for expanded view
    full_display = value_str
    try:
        parsed = json.loads(value_str)
        full_display = json.dumps(parsed, indent=2)
    except (json.JSONDecodeError, TypeError):
        pass

    truncated = escape_html(value_str[:truncate_at])
    return (
        f'<span class="span-attr-value truncated">'
        f'<span class="truncated-value">{truncated}…</span>'
        f'<span class="show-more">[expand]</span>'
        f'<div class="full-value">{escape_html(full_display)}</div>'
        f'</span>'
    )


def format_attributes(attrs: dict) -> str:
    """Format attributes as HTML."""
    if not attrs:
        return "<em>No attributes</em>"

    html_parts = []
    # Priority keys to show first (OTel GenAI semantic conventions)
    priority_keys = [
        "gen_ai.tool.name", "tool_arguments", "tool_response",
        "gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
        "gen_ai.input.messages", "gen_ai.output.messages",
    ]

    shown = set()
    for key in priority_keys:
        if key in attrs:
            value = attrs[key]
            html_parts.append(
                f'<div class="span-attr"><span class="span-attr-key">{escape_html(key)}</span>'
                f'{format_attr_value(value, truncate_at=200)}</div>'
            )
            shown.add(key)

    # Show remaining (up to 10 more), skip logfire.* internal attributes
    remaining = [(k, v) for k, v in attrs.items() if k not in shown and not k.startswith("logfire.")]
    for key, value in remaining[:10]:
        html_parts.append(
            f'<div class="span-attr"><span class="span-attr-key">{escape_html(key)}</span>'
            f'{format_attr_value(value, truncate_at=150)}</div>'
        )

    if len(remaining) > 10:
        html_parts.append(f'<div class="span-attr"><em>... and {len(remaining)-10} more</em></div>')

    return "\n".join(html_parts)


def build_span_tree(spans: list[dict]) -> list[dict]:
    """Build a tree of spans from flat list."""
    by_id = {s["id"]: s for s in spans}
    roots = []

    for span in spans:
        span["children"] = []
        parent_id = span.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(span)
        else:
            roots.append(span)

    # Sort children by start_time
    def sort_children(span):
        span["children"].sort(key=lambda s: s["start_time"] or 0)
        for child in span["children"]:
            sort_children(child)

    for root in roots:
        sort_children(root)

    return sorted(roots, key=lambda s: s["start_time"] or 0)


def render_span(span: dict, depth: int, trace_start: int, trace_duration: float) -> str:
    """Render a single span with its children."""
    span_type = get_span_type(span["name"])

    # Calculate waterfall bar position
    if trace_duration > 0 and span["start_time"]:
        bar_left = ((span["start_time"] - trace_start) / 1_000_000) / trace_duration * 100
        bar_width = (span["duration_ms"] or 0) / trace_duration * 100
    else:
        bar_left = 0
        bar_width = 100

    bar_left = max(0, min(100, bar_left))
    bar_width = max(1, min(100 - bar_left, bar_width))

    attrs = json.loads(span["attributes"]) if span["attributes"] else {}
    status = span.get("status_code") or "UNSET"
    children = span.get("children", [])
    has_children = len(children) > 0

    # Render children first
    children_html = ""
    if has_children:
        children_parts = []
        for child in children:
            children_parts.append(render_span(child, depth + 1, trace_start, trace_duration))
        children_html = f'<div class="span-children">{"".join(children_parts)}</div>'

    html = SPAN_TEMPLATE.format(
        toggle_class="has-children" if has_children else "",
        toggle_icon="▼" if has_children else "·",
        span_type=span_type,
        name=span["name"],
        duration=format_duration(span["duration_ms"]),
        status=status,
        bar_left=bar_left,
        bar_width=bar_width,
        attributes_html=format_attributes(attrs),
        children_html=children_html,
    )

    return html


def generate_trace_html() -> str:
    """Generate complete HTML for trace viewer."""
    db_path = DATA_DIR / "co-cli.db"
    if not db_path.exists():
        return HTML_TEMPLATE.format(
            trace_count=0,
            span_count=0,
            tool_count=0,
            traces_html='<div class="empty">No traces yet. Run some commands first.</div>'
        )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # Get unique traces (last 20)
        traces = conn.execute("""
            SELECT DISTINCT trace_id,
                   MIN(start_time) as trace_start,
                   MAX(end_time) as trace_end
            FROM spans
            WHERE trace_id IS NOT NULL
            GROUP BY trace_id
            ORDER BY trace_start DESC
            LIMIT 20
        """).fetchall()

        if not traces:
            return HTML_TEMPLATE.format(
                trace_count=0,
                span_count=0,
                tool_count=0,
                traces_html='<div class="empty">No traces yet. Run some commands first.</div>'
            )

        # Stats
        span_count = conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        tool_count = conn.execute(
            "SELECT COUNT(*) FROM spans WHERE name LIKE '%tool%'"
        ).fetchone()[0]

        traces_html = []
        for trace in traces:
            trace_id = trace["trace_id"]
            trace_start = trace["trace_start"] or 0
            trace_end = trace["trace_end"] or trace_start
            trace_duration = (trace_end - trace_start) / 1_000_000  # ms

            # Get all spans for this trace
            spans = conn.execute("""
                SELECT * FROM spans
                WHERE trace_id = ?
                ORDER BY start_time
            """, (trace_id,)).fetchall()

            spans = [dict(s) for s in spans]
            tree = build_span_tree(spans)

            # Render spans
            spans_html = ""
            for root in tree:
                spans_html += render_span(root, 0, trace_start, trace_duration)

            # Format time
            import datetime
            try:
                time_str = datetime.datetime.fromtimestamp(
                    trace_start / 1_000_000_000
                ).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                time_str = "Unknown"

            traces_html.append(TRACE_TEMPLATE.format(
                time=time_str,
                trace_id=trace_id[:16] + "...",
                total_duration=format_duration(trace_duration),
                spans_html=spans_html,
            ))

        return HTML_TEMPLATE.format(
            trace_count=len(traces),
            span_count=span_count,
            tool_count=tool_count,
            traces_html="\n".join(traces_html),
        )


def write_trace_html(output_path: Path | None = None) -> Path:
    """Write trace HTML to file and return path."""
    if output_path is None:
        output_path = DATA_DIR / "traces.html"

    html = generate_trace_html()
    output_path.write_text(html)
    return output_path
