"""Themed terminal display — console, semantic styles, display helpers, Frontend, TerminalFrontend."""

import signal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from rich.theme import Theme

from co_cli.config import settings

if TYPE_CHECKING:
    from co_cli.tools.tool_output import ToolResultPayload

# -- Theme palettes (keyed by theme name) ------------------------------------

_THEMES: dict[str, dict[str, str]] = {
    "dark":  {"status": "yellow",      "info": "cyan", "accent": "bold cyan",  "shell": "dim", "error": "bold red", "success": "green", "warning": "orange3", "hint": "dim", "thinking": "dim italic"},
    "light": {"status": "dark_orange", "info": "blue", "accent": "bold blue",  "shell": "dim", "error": "bold red", "success": "green", "warning": "orange3", "hint": "dim", "thinking": "dim italic"},
}

# -- Console (single instance, themed) --------------------------------------

console = Console(theme=Theme(_THEMES.get(settings.theme, _THEMES["light"])))

# -- Indicators ------------------------------------------------------------

PROMPT_CHAR = "❯"
BULLET      = "▸"
SUCCESS     = "✦"
ERROR       = "✖"
INFO        = "◈"

# -- Theme switching -------------------------------------------------------


def set_theme(name: str) -> None:
    """Switch the console theme at runtime (e.g. from --theme flag)."""
    console.push_theme(Theme(_THEMES.get(name, _THEMES["light"])))


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


def prompt_selection(
    items: list[str],
    *,
    title: str = "Select",
    current: str | None = None,
) -> str | None:
    """Interactive arrow-key menu for selecting from a list.

    Up/Down to navigate, Enter to select, q/Esc to cancel.
    Returns the selected item string, or None if cancelled.
    """
    import sys
    import tty
    import termios

    if not items:
        return None

    # Start with current item highlighted, or first item
    idx = 0
    if current and current in items:
        idx = items.index(current)

    def _read_key() -> str:
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

    def _render(selected: int) -> None:
        # Move cursor up to overwrite previous render (except first time)
        sys.stdout.write(f"\x1b[{len(items)}A")
        for i, name in enumerate(items):
            marker = " *" if name == current else ""
            if i == selected:
                sys.stdout.write(f"\x1b[2K  \x1b[1;36m❯ {name}{marker}\x1b[0m\n")
            else:
                sys.stdout.write(f"\x1b[2K    {name}{marker}\n")
        sys.stdout.flush()

    # Initial render
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
                _render(idx)
            elif key == "\x1b[B":  # Down
                idx = (idx + 1) % len(items)
                _render(idx)
            elif key in ("\r", "\n"):  # Enter
                return items[idx]
            elif key in ("q", "\x1b", "\x03"):  # q, Esc, Ctrl-C
                return None
    except (EOFError, KeyboardInterrupt):
        return None


# -- Frontend — abstraction for display + user interaction ----------


@runtime_checkable
class Frontend(Protocol):
    """Display and interaction contract for the orchestration layer.

    Implementations: TerminalFrontend (Rich/prompt-toolkit), RecordingFrontend (tests).
    """

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

    def prompt_approval(self, description: str) -> str:
        """Prompt user for approval. Returns 'y', 'n', or 'a'."""
        ...

    def cleanup(self) -> None:
        """Exception/cancellation cleanup — restore terminal state."""
        ...


# -- TerminalFrontend (Frontend implementation) --------------------

_CHOICES_HINT = " [[green]y[/green]=once  [yellow]a[/yellow]=session  [red]n[/red]=deny]"


class TerminalFrontend:
    """Rich-based terminal frontend implementing Frontend.

    Manages Live instances for streaming text and thinking panels,
    and SIGINT handler swapping for synchronous approval prompts.
    """

    def __init__(self) -> None:
        self._live: Live | None = None
        self._thinking_live: Live | None = None
        self._status_live: Live | None = None
        self._tool_live: Live | None = None
        # tool_call_id → current display label (overwritten by progress messages)
        self._active_tools: dict[str, str] = {}
        # tool_call_id → stable original label (set once in on_tool_start, never overwritten)
        self._tool_labels: dict[str, str] = {}
        # last text set via on_status or on_reasoning_progress; None when status surface is cleared
        self._status_text: str | None = None

    def active_surface(self) -> str:
        """Return the currently active public display surface name."""
        if self._live is not None:
            return "text"
        if self._thinking_live is not None:
            return "thinking"
        if self._tool_live is not None:
            return "tool"
        if self._status_live is not None:
            return "status"
        return "none"

    def active_tool_messages(self) -> tuple[str, ...]:
        """Return the currently rendered tool labels/messages."""
        return tuple(self._active_tools.values())

    def active_status_text(self) -> str | None:
        """Return the text currently shown in the status surface, or None if inactive."""
        return self._status_text

    def _clear_status_live(self) -> None:
        if self._status_live is not None:
            self._status_live.stop()
            self._status_live = None
            self._status_text = None
            console.print("")

    def on_text_delta(self, accumulated: str) -> None:
        self._clear_status_live()
        if self._live is None:
            self._live = Live(
                Markdown(accumulated), console=console, auto_refresh=False,
            )
            self._live.start()
        else:
            self._live.update(Markdown(accumulated))
            self._live.refresh()

    def on_text_commit(self, final: str) -> None:
        self._clear_status_live()
        if self._live:
            self._live.update(Markdown(final))
            self._live.refresh()
            self._live.stop()
            self._live = None

    def on_thinking_delta(self, accumulated: str) -> None:
        self._clear_status_live()
        renderable = Text(accumulated or "...", style="thinking")
        if self._thinking_live is None:
            self._thinking_live = Live(
                renderable, console=console, auto_refresh=False, transient=True,
            )
            self._thinking_live.start()
        else:
            self._thinking_live.update(renderable)
            self._thinking_live.refresh()

    def on_thinking_commit(self, final: str) -> None:
        self._clear_status_live()
        if self._thinking_live:
            self._thinking_live.stop()
            self._thinking_live = None
        if final:
            console.print(Text(final, style="thinking"))

    def _stop_tool_live(self) -> None:
        if self._tool_live is not None:
            self._tool_live.stop()
            self._tool_live = None

    def _refresh_tool_live(self) -> None:
        """Render all active tool labels as one Text block in a single Live instance."""
        if not self._active_tools:
            self._stop_tool_live()
            return
        lines = "\n".join(f"  {label}" for label in self._active_tools.values())
        renderable = Text(lines, style="dim")
        if self._tool_live is None:
            self._tool_live = Live(
                renderable, console=console, auto_refresh=False, transient=False,
            )
            self._tool_live.start()
        else:
            self._tool_live.update(renderable)
        self._tool_live.refresh()

    def _close_tool(self, tool_id: str) -> str:
        """Pop both dicts and manage _tool_live. Returns stable label for panel title."""
        self._active_tools.pop(tool_id, None)
        label = self._tool_labels.pop(tool_id, tool_id)
        if not self._active_tools:
            self._stop_tool_live()
        else:
            self._refresh_tool_live()
        return label

    def _render_tool_panel(self, label: str, result: "ToolResultPayload") -> None:
        """Single dispatch point for all result types.

        Extracted so new result types (v2) require one edit here, not in on_tool_complete.
        """
        if isinstance(result, str) and result.strip():
            console.print(Panel(result.rstrip(), title=label, border_style="shell"))

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        """Tool lifecycle: tool invocation started."""
        self._clear_status_live()
        label = args_display if args_display else name
        self._active_tools[tool_id] = label
        self._tool_labels[tool_id] = label
        self._refresh_tool_live()

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        """Tool lifecycle: in-progress update from a running tool."""
        self._clear_status_live()
        self._active_tools[tool_id] = message
        self._refresh_tool_live()

    def on_tool_complete(self, tool_id: str, result: "ToolResultPayload") -> None:
        """Tool lifecycle: tool completed with optional final result payload."""
        label = self._close_tool(tool_id)
        self._render_tool_panel(label, result)

    def on_status(self, message: str) -> None:
        self._status_text = message
        renderable = Text(message, style="dim")
        if self._status_live is None:
            self._status_live = Live(
                renderable, console=console, auto_refresh=False, transient=False,
            )
            self._status_live.start()
        else:
            self._status_live.update(renderable)
        self._status_live.refresh()

    def on_reasoning_progress(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._status_text = text
        renderable = Text(text, style="status")
        if self._status_live is None:
            self._status_live = Live(
                renderable, console=console, auto_refresh=False, transient=False,
            )
            self._status_live.start()
        else:
            self._status_live.update(renderable)
        self._status_live.refresh()

    def on_final_output(self, text: str) -> None:
        self._clear_status_live()
        console.print(Markdown(text))

    def prompt_approval(self, description: str) -> str:
        """Prompt user for y/n with SIGINT handler swap for blocking input."""
        self._clear_status_live()
        console.print(f"Allow [bold]{description}[/bold]?" + _CHOICES_HINT, end=" ")

        prev_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            choice = Prompt.ask(
                "", choices=["y", "n", "a"], default="n",
                show_choices=False, show_default=False, console=console,
            )
        finally:
            signal.signal(signal.SIGINT, prev_handler)

        return choice

    def cleanup(self) -> None:
        """Stop any active Live instances to restore terminal state."""
        for lv in (self._tool_live, self._status_live, self._thinking_live, self._live):
            if lv:
                try:
                    lv.stop()
                except Exception:
                    pass
        self._tool_live = None
        self._status_live = None
        self._thinking_live = None
        self._live = None
        self._status_text = None
        self._active_tools.clear()
        self._tool_labels.clear()
