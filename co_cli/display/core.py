"""Themed terminal display — console, semantic styles, display helpers, Frontend, TerminalFrontend."""

from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from prompt_toolkit import ANSI, print_formatted_text
from prompt_toolkit.application import run_in_terminal
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from co_cli.deps import ApprovalSubject

if TYPE_CHECKING:
    from prompt_toolkit import Application

    from co_cli.tools.tool_io import ToolResultPayload

# -- Theme palettes (keyed by theme name) ------------------------------------

_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "status": "yellow",
        "info": "cyan",
        "accent": "bold cyan",
        "shell_exec": "dim",
        "error": "bold red",
        "success": "green",
        "warning": "orange3",
        "hint": "dim",
        "thinking": "dim italic",
    },
    "light": {
        "status": "dark_orange",
        "info": "blue",
        "accent": "bold blue",
        "shell_exec": "dim",
        "error": "bold red",
        "success": "green",
        "warning": "orange3",
        "hint": "dim",
        "thinking": "dim italic",
    },
}

# -- Console (single instance, themed) --------------------------------------

console = Console(theme=Theme(_THEMES["light"]))

# Tracks the active theme so render_to_ansi can build a styling-identical sink
# console; updated by set_theme alongside the module console's pushed theme.
_active_theme: Theme = Theme(_THEMES["light"])

# -- Indicators ------------------------------------------------------------

PROMPT_CHAR = "❯"
BULLET = "▸"
SUCCESS = "✦"
ERROR = "✖"
INFO = "◈"

# -- Theme switching -------------------------------------------------------


def set_theme(name: str) -> None:
    """Switch the console theme at runtime (e.g. from --theme flag)."""
    global _active_theme
    _active_theme = Theme(_THEMES.get(name, _THEMES["light"]))
    console.push_theme(_active_theme)


# -- Rich → ANSI bridge (the single renderable→string primitive) ------------


def render_to_ansi(renderable: RenderableType, *, width: int) -> str:
    """Render a Rich renderable to an ANSI string.

    The sole renderable→string routine: both the committed-streaming path and
    the in-flight control go through it, so they cannot diverge in width/color.
    Stateless and pure — `width` is supplied by the caller (resolved from the
    bound app), never read from an ambient terminal or get_app(). Forces a
    terminal color system so the captured string carries ANSI styling even when
    the process stdout is not a tty.
    """
    buffer = StringIO()
    sink = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        theme=_active_theme,
    )
    sink.print(renderable)
    return buffer.getvalue()


# -- Display helpers -------------------------------------------------------


def display_status(message: str, style: str | None = None) -> None:
    """Themed bullet + message."""
    s = style or "status"
    console.print(f"[{s}]{BULLET} {message}[/{s}]")


def display_error(message: str, hint: str | None = None) -> None:
    """Red-bordered panel with optional recovery hint."""
    body = f"[bold red]{ERROR} {message}[/bold red]"
    if hint:
        body += f"\n[dim]{hint}[/dim]"
    console.print(Panel(body, border_style="red", title="Error", title_align="left"))


def display_info(message: str) -> None:
    """Themed info message."""
    console.print(f"[info]{INFO} {message}[/info]")


def make_table(*columns: str) -> Table:
    """Minimal borderless table — the standard list style across all commands."""
    t = Table(box=None, expand=False, show_header=False, pad_edge=False, style="on default")
    for col in columns:
        t.add_column(col)
    return t


def _render_selection(items: list[str], selected: int, current: str | None) -> None:
    """Re-render the selection menu in-place, moving cursor up to overwrite."""
    import sys

    sys.stdout.write(f"\x1b[{len(items)}A")
    for idx, name in enumerate(items):
        marker = " *" if name == current else ""
        if idx == selected:
            sys.stdout.write(f"\x1b[2K  \x1b[1;36m❯ {name}{marker}\x1b[0m\n")
        else:
            sys.stdout.write(f"\x1b[2K    {name}{marker}\n")
    sys.stdout.flush()


def _read_key() -> str:
    """Read a single keypress from stdin, including escape sequences."""
    import sys
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch += sys.stdin.read(2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _run_selection(items: list[str], title: str, current: str | None) -> str | None:
    """The raw termios arrow-key menu — assumes it owns the terminal in cooked/raw mode."""
    import sys

    idx = 0
    if current and current in items:
        idx = items.index(current)

    # Initial render — write items to establish screen lines
    console.print(f"[dim]{title} — ↑↓ navigate, Enter select, q cancel[/dim]")
    for i, name in enumerate(items):
        marker = " *" if name == current else ""
        if i == idx:
            sys.stdout.write(f"  \x1b[1;36m❯ {name}{marker}\x1b[0m\n")
        else:
            sys.stdout.write(f"    {name}{marker}\n")
    sys.stdout.flush()

    try:
        while True:
            key = _read_key()
            if key == "\x1b[A":  # Up
                idx = (idx - 1) % len(items)
                _render_selection(items, idx, current)
            elif key == "\x1b[B":  # Down
                idx = (idx + 1) % len(items)
                _render_selection(items, idx, current)
            elif key in ("\r", "\n"):  # Enter
                return items[idx]
            elif key in ("q", "\x1b", "\x03"):  # q, Esc, Ctrl-C
                return None
    except (EOFError, KeyboardInterrupt):
        return None


async def prompt_selection(
    items: list[str],
    *,
    title: str = "Select",
    current: str | None = None,
) -> str | None:
    """Interactive arrow-key menu for selecting from a list.

    Up/Down to navigate, Enter to select, q/Esc to cancel.
    Returns the selected item string, or None if cancelled.

    When an owned Application is running (in-REPL), the raw termios read runs
    inside run_in_terminal so the app suspends and restores terminal ownership
    cleanly; otherwise (non-REPL callers) it reads inline (CD-m-3, mirroring
    hermes cli.py:7103-7117).
    """
    from prompt_toolkit.application import get_app_or_none

    if not items:
        return None

    if get_app_or_none() is not None:
        return await run_in_terminal(lambda: _run_selection(items, title, current))
    return _run_selection(items, title, current)


# -- Frontend — abstraction for display + user interaction ----------


@dataclass(frozen=True)
class QuestionPrompt:
    """Structured question prompt for mid-execution user input."""

    question: str
    options: list[str] | None = None
    multiple: bool = False


@dataclass(frozen=True)
class StatusSnapshot:
    """Typed contract for bottom-toolbar footer content."""

    session_label: str
    mode: Literal["idle", "active"]
    context_pct: float | None
    background_task_count: int
    approval_count: int
    queue_depth: int = 0


@runtime_checkable
class Frontend(Protocol):
    """Display and interaction contract for the orchestration layer."""

    def on_text_delta(self, accumulated: str) -> None:
        """Incremental Markdown render (called at throttled FPS)."""
        ...

    def on_text_commit(self, final: str) -> None:
        """Final text render + tear down any live display."""
        ...

    def on_thinking_delta(self, accumulated: str) -> None:
        """Thinking panel update (verbose mode only)."""
        ...

    def on_thinking_commit(self, final: str) -> None:
        """Final thinking panel render."""
        ...

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        """Tool lifecycle: tool invocation started."""
        ...

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        """Tool lifecycle: in-progress update from a running tool."""
        ...

    def on_tool_complete(self, tool_id: str, result: "ToolResultPayload") -> None:
        """Tool lifecycle: tool completed with optional final result payload."""
        ...

    def on_status(self, message: str) -> None:
        """Status messages (e.g. 'Co is thinking...')."""
        ...

    def on_reasoning_progress(self, text: str) -> None:
        """Reasoning progress update (e.g. intermediate reasoning steps)."""
        ...

    def on_final_output(self, text: str) -> None:
        """Fallback Markdown render when streaming didn't emit text."""
        ...

    async def prompt_approval(self, subject: ApprovalSubject) -> str:
        """Prompt user for approval. Returns 'y', 'n', or 'a'."""
        ...

    async def prompt_question(self, prompt: QuestionPrompt) -> str:
        """Prompt user for a free-text or constrained answer. Returns the user's answer."""
        ...

    async def prompt_confirm(self, message: str) -> bool:
        """Prompt user for a yes/no confirmation. Returns True if confirmed."""
        ...

    def update_status(self, snapshot: "StatusSnapshot") -> None:
        """Push a status snapshot for the bottom-toolbar footer."""
        ...

    def clear_status(self) -> None:
        """Dismiss the status surface so the next prompt starts on a clean line."""
        ...

    def cleanup(self) -> None:
        """Exception/cancellation cleanup — restore terminal state."""
        ...


# -- TerminalFrontend (Frontend implementation) --------------------

_CHOICES_HINT = " [[green]y[/green]=once  [yellow]a[/yellow]=session  [red]n[/red]=deny]"


class TerminalFrontend:
    """Single-owner terminal frontend implementing Frontend.

    Drives one persistent ``prompt_toolkit.Application``: in-flight streaming
    surfaces (assistant text, thinking, tool labels, status) render into a single
    ANSI buffer shown by the app's in-flight window and updated via
    ``app.invalidate()``; committed output (final text, thinking, tool result
    panels) is printed to scrollback via ``print_formatted_text(ANSI(...))``.

    The four live surfaces are mutually exclusive in time (StreamRenderer commits
    text before any tool/thinking surface renders), so one in-flight region is
    sufficient. The app handle is supplied by ``bind_app`` — this frontend never
    calls prompt_toolkit's ``get_app()`` itself (F3), keeping it stub-testable.
    """

    def __init__(self) -> None:
        self._app: Application | None = None
        # Single in-flight ANSI buffer for the live region; "" when no surface active.
        self._inflight: str = ""
        # Which surface owns the in-flight buffer — drives commit-on-supersede for status.
        self._inflight_kind: str = "none"
        # tool_call_id → current display label (overwritten by progress messages)
        self._active_tools: dict[str, str] = {}
        # tool_call_id → stable original label (set once in on_tool_start, never overwritten)
        self._tool_labels: dict[str, str] = {}
        # Last pushed status snapshot; drives render_footer_toolbar
        self._footer_snapshot: StatusSnapshot | None = None

    def bind_app(self, app: "Application") -> None:
        """Bind the owning Application so the frontend can request repaints (F3).

        Concrete to TerminalFrontend — not on the Frontend protocol; HeadlessFrontend
        has no app and _chat_loop constructs the concrete frontend directly.
        """
        self._app = app

    def get_inflight(self) -> str:
        """Return the current in-flight ANSI buffer (read by the app's in-flight window)."""
        return self._inflight

    def _width(self) -> int:
        """Resolve render width from the bound app's output; fall back to the console."""
        if self._app is not None:
            try:
                return self._app.output.get_size().columns
            except Exception:
                pass
        return console.width

    def _invalidate(self) -> None:
        if self._app is not None:
            self._app.invalidate()

    def _set_inflight(self, renderable: RenderableType, kind: str) -> None:
        """Render a live surface into the in-flight buffer and request a repaint."""
        self._inflight = render_to_ansi(renderable, width=self._width())
        self._inflight_kind = kind
        self._invalidate()

    def _clear_inflight(self) -> None:
        self._inflight = ""
        self._inflight_kind = "none"
        self._invalidate()

    def _commit(self, renderable: RenderableType) -> None:
        """Commit a renderable to scrollback above the app (under patch_stdout)."""
        print_formatted_text(ANSI(render_to_ansi(renderable, width=self._width())))

    def _commit_status(self) -> None:
        """Commit an in-flight status line to scrollback before another surface
        supersedes it — reproducing the old transient=False status persistence."""
        if self._inflight_kind == "status" and self._inflight:
            print_formatted_text(ANSI(self._inflight))
        if self._inflight_kind == "status":
            self._inflight = ""
            self._inflight_kind = "none"

    def update_status(self, snapshot: StatusSnapshot) -> None:
        self._footer_snapshot = snapshot
        # Repaint so a status push reflects immediately even with no co-located
        # render event (the queue drain fires inside a done-callback with none).
        # _invalidate is a no-op when no app is bound (headless/stub paths).
        self._invalidate()

    def render_footer_toolbar(self) -> str:
        if self._footer_snapshot is None:
            return ""
        s = self._footer_snapshot
        parts: list[str] = [s.session_label, s.mode]
        if s.queue_depth > 0:
            parts.append(f"{s.queue_depth} queued")
        if s.context_pct is not None:
            parts.append(f"ctx {int(s.context_pct * 100)}%")
        if s.background_task_count > 0:
            parts.append(f"{s.background_task_count} bg")
        if s.approval_count > 0:
            noun = "approval" if s.approval_count == 1 else "approvals"
            parts.append(f"{s.approval_count} {noun}")
        return " · ".join(parts)

    def clear_status(self) -> None:
        """Commit any pending status then clear the in-flight region."""
        self._commit_status()
        self._clear_inflight()

    def on_text_delta(self, accumulated: str) -> None:
        self._commit_status()
        self._set_inflight(Markdown(accumulated), "text")

    def on_text_commit(self, final: str) -> None:
        self._commit(Markdown(final))
        self._clear_inflight()

    def on_thinking_delta(self, accumulated: str) -> None:
        self._commit_status()
        self._set_inflight(Text(accumulated or "...", style="thinking"), "thinking")

    def on_thinking_commit(self, final: str) -> None:
        # Transient parity: erase the in-flight thinking region (it never lands in
        # scrollback), then commit the final dim thinking text below it.
        self._clear_inflight()
        if final:
            self._commit(Text(final, style="thinking"))

    def _refresh_tool_inflight(self) -> None:
        """Render all active tool labels as one in-flight Text block."""
        if not self._active_tools:
            self._clear_inflight()
            return
        lines = "\n".join(f"  {label}" for label in self._active_tools.values())
        self._set_inflight(Text(lines, style="dim"), "tool")

    def _close_tool(self, tool_id: str) -> str:
        """Pop both dicts and refresh the in-flight block. Returns stable label for panel title."""
        self._active_tools.pop(tool_id, None)
        label = self._tool_labels.pop(tool_id, tool_id)
        self._refresh_tool_inflight()
        return label

    def _render_tool_panel(self, label: str, result: "ToolResultPayload") -> None:
        """Single dispatch point for all result types.

        Extracted so new result types (v2) require one edit here, not in on_tool_complete.
        """
        if isinstance(result, str) and result.strip():
            self._commit(Panel(result.rstrip(), title=label, border_style="shell_exec"))

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        """Tool lifecycle: tool invocation started."""
        self._commit_status()
        label = args_display if args_display else name
        self._active_tools[tool_id] = label
        self._tool_labels[tool_id] = label
        self._refresh_tool_inflight()

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        """Tool lifecycle: in-progress update from a running tool."""
        self._active_tools[tool_id] = message
        self._refresh_tool_inflight()

    def on_tool_complete(self, tool_id: str, result: "ToolResultPayload") -> None:
        """Tool lifecycle: tool completed with optional final result payload."""
        label = self._close_tool(tool_id)
        self._render_tool_panel(label, result)

    def on_status(self, message: str) -> None:
        if self._app is None:
            # Bootstrap / no owned app yet — print directly so startup status is visible.
            console.print(Text(message, style="dim"))
            return
        self._set_inflight(Text(message, style="dim"), "status")

    def on_reasoning_progress(self, text: str) -> None:
        if not text or not text.strip():
            return
        if self._app is None:
            console.print(Text(text, style="status"))
            return
        self._set_inflight(Text(text, style="status"), "status")

    def on_final_output(self, text: str) -> None:
        self._commit_status()
        self._commit(Markdown(text))

    def _build_approval_panel(self, subject: ApprovalSubject) -> Panel:
        """Build the Rich Panel renderable for an approval prompt."""
        parts: list[object] = [Text(subject.display)]
        if subject.preview is not None:
            parts.append(Rule(style="dim"))
            parts.append(Text(subject.preview, style="dim"))
        return Panel(
            Group(*parts),
            title=subject.tool_name,
            border_style="warning",
            title_align="left",
        )

    async def prompt_approval(self, subject: ApprovalSubject) -> str:
        """Prompt for y/n/a, suspending the owned app via run_in_terminal.

        run_in_terminal restores cooked-mode terminal ownership for the blocking
        read, so the old SIGINT-handler swap is no longer needed.
        """
        self.clear_status()

        def _ask() -> str:
            console.print(self._build_approval_panel(subject))
            console.print("Allow?" + _CHOICES_HINT, end=" ")
            return Prompt.ask(
                "",
                choices=["y", "n", "a"],
                default="n",
                show_choices=False,
                show_default=False,
                console=console,
            )

        return await run_in_terminal(_ask)

    async def prompt_question(self, prompt: QuestionPrompt) -> str:
        """Prompt for a constrained or free-text answer, suspending the app via run_in_terminal."""
        self.clear_status()

        def _ask() -> str:
            if prompt.options:
                suffix = " (select multiple, comma-separated)" if prompt.multiple else ""
                body = f"[accent]{prompt.question}[/accent]\n[hint]Options: {' | '.join(prompt.options)}{suffix}[/hint]"
            else:
                body = f"[accent]{prompt.question}[/accent]"
            console.print(Panel(body, title="Question", border_style="info", title_align="left"))
            if prompt.options and not prompt.multiple:
                return Prompt.ask("", choices=prompt.options, console=console)
            if prompt.options and prompt.multiple:
                return Prompt.ask("Select (comma-separated)", console=console)
            return Prompt.ask("Answer", console=console)

        return await run_in_terminal(_ask)

    async def prompt_confirm(self, message: str) -> bool:
        """Prompt for a yes/no confirmation, suspending the app via run_in_terminal."""
        response = await run_in_terminal(lambda: console.input(message))
        return response.strip().lower() == "y"

    def cleanup(self) -> None:
        """Clear the in-flight region and tool state on exception/cancellation."""
        self._active_tools.clear()
        self._tool_labels.clear()
        self._clear_inflight()
