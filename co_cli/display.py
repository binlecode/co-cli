"""Themed terminal display — console, semantic styles, display helpers."""

import signal
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.theme import Theme

from co_cli.config import settings

# -- Theme palettes (keyed by theme name) ------------------------------------

_THEMES: dict[str, dict[str, str]] = {
    "dark":  {"status": "yellow",      "info": "cyan", "accent": "bold cyan",  "yolo": "bold orange3", "shell": "dim", "error": "bold red", "success": "green", "warning": "orange3", "hint": "dim", "thinking": "dim italic"},
    "light": {"status": "dark_orange", "info": "blue", "accent": "bold blue",  "yolo": "bold orange3", "shell": "dim", "error": "bold red", "success": "green", "warning": "orange3", "hint": "dim", "thinking": "dim italic"},
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


# -- TerminalFrontend (FrontendProtocol implementation) --------------------

_CHOICES_HINT = " [[green]y[/green]/[red]n[/red]/[bold orange3]a[/bold orange3](yolo)]"


class TerminalFrontend:
    """Rich-based terminal frontend implementing FrontendProtocol.

    Manages Live instances for streaming text and thinking panels,
    and SIGINT handler swapping for synchronous approval prompts.
    """

    def __init__(self) -> None:
        self._live: Live | None = None
        self._thinking_live: Live | None = None

    def on_text_delta(self, accumulated: str) -> None:
        if self._live is None:
            self._live = Live(
                Markdown(accumulated), console=console, auto_refresh=False,
            )
            self._live.start()
        else:
            self._live.update(Markdown(accumulated))
            self._live.refresh()

    def on_text_commit(self, final: str) -> None:
        if self._live:
            self._live.update(Markdown(final))
            self._live.refresh()
            self._live.stop()
            self._live = None

    def on_thinking_delta(self, accumulated: str) -> None:
        renderable = Panel(
            accumulated or "...",
            title="thinking", border_style="thinking",
        )
        if self._thinking_live is None:
            self._thinking_live = Live(
                renderable, console=console, auto_refresh=False, transient=True,
            )
            self._thinking_live.start()
        else:
            self._thinking_live.update(renderable)
            self._thinking_live.refresh()

    def on_thinking_commit(self, final: str) -> None:
        if self._thinking_live:
            self._thinking_live.stop()
            self._thinking_live = None
        if final:
            console.print(Panel(
                final, title="thinking", border_style="thinking",
            ))

    def on_tool_call(self, name: str, args_display: str) -> None:
        if args_display:
            console.print(f"[dim]  {name}({args_display})[/dim]")
        else:
            console.print(f"[dim]  {name}()[/dim]")

    def on_tool_result(self, title: str, content: str | dict[str, Any]) -> None:
        if isinstance(content, dict) and "display" in content:
            console.print(Panel(
                content["display"], title=title, border_style="shell",
            ))
        elif isinstance(content, str):
            console.print(Panel(
                content.rstrip(), title=f"$ {title}", border_style="shell",
            ))

    def on_status(self, message: str) -> None:
        console.print(f"[dim]{message}[/dim]")

    def on_final_output(self, text: str) -> None:
        console.print(Markdown(text))

    def prompt_approval(self, description: str) -> str:
        """Prompt user for y/n/a with SIGINT handler swap for blocking input."""
        console.print(f"Approve [bold]{description}[/bold]?" + _CHOICES_HINT, end=" ")

        prev_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            choice = Prompt.ask(
                "", choices=["y", "n", "a"], default="n",
                show_choices=False, show_default=False, console=console,
            )
        finally:
            signal.signal(signal.SIGINT, prev_handler)

        if choice == "a":
            console.print("[bold orange3]YOLO mode enabled — auto-approving for this session[/bold orange3]")
        return choice

    def cleanup(self) -> None:
        """Stop any active Live instances to restore terminal state."""
        for lv in (self._thinking_live, self._live):
            if lv:
                try:
                    lv.stop()
                except Exception:
                    pass
        self._thinking_live = None
        self._live = None
