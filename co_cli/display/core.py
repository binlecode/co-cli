"""Themed terminal display — console, semantic styles, display helpers, Frontend, TerminalFrontend."""

import asyncio
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from prompt_toolkit import ANSI, print_formatted_text
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
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
    queue_head_preview: str | None = None


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

    def on_final_output(self, text: str) -> None:
        """Fallback Markdown render when streaming didn't emit text."""
        ...

    async def prompt_approval(self, subject: ApprovalSubject) -> str:
        """Prompt user for approval. Returns 'y', 'n', or 'a'."""
        ...

    async def prompt_question(self, prompt: QuestionPrompt) -> str:
        """Prompt user for a free-text or constrained answer. Returns the user's answer."""
        ...

    async def prompt_selection(
        self, items: list[str], *, title: str = "Select", current: str | None = None
    ) -> str | None:
        """Prompt user to pick one item from a list. Returns the choice, or None if cancelled."""
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


class TerminalFrontend:
    """Single-owner terminal frontend implementing Frontend.

    Drives one persistent ``prompt_toolkit.Application``: in-flight streaming
    surfaces (assistant text, thinking, tool labels, status) render into a single
    ANSI buffer shown by the app's in-flight window and updated via
    ``app.invalidate()``; committed output (final text, thinking, tool result
    panels) is printed to scrollback via ``print_formatted_text(ANSI(...))``.

    The live surfaces (text, thinking, tool labels, status, and the y/n/a approval
    prompt) are mutually exclusive in time — StreamRenderer commits text before any
    tool/thinking surface renders, and the prompt surface only shows while the turn
    is paused at an approval gate — so one in-flight region is sufficient. The app
    handle is supplied by ``bind_app`` — this frontend never calls prompt_toolkit's
    ``get_app()`` itself (F3), keeping it stub-testable.
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
        # In-app single-key prompt state (approval / confirm). The owned
        # Application's own key bindings resolve _prompt_future via resolve_prompt;
        # the prompt panel renders in the in-flight region. This replaces the old
        # run_in_terminal + Rich Prompt.ask path, which deadlocked the owned app
        # waiting on a terminal cursor-position handshake (the prompt never surfaced).
        self._prompt_future: asyncio.Future[str] | None = None
        self._prompt_valid_keys: frozenset[str] = frozenset()
        self._prompt_default: str = ""
        # In-app list-picker state (selection menu). Navigation keys are
        # intercepted by the app's selection-mode key bindings (move_selection /
        # resolve_selection); the menu renders in the in-flight region. Same
        # owned-app, no-terminal-suspend mechanism as the single-key prompt above.
        self._selection_future: asyncio.Future[str | None] | None = None
        self._selection_items: list[str] = []
        self._selection_index: int = 0
        self._selection_title: str = ""
        self._selection_current: str | None = None
        # In-app free-text input state (clarify question). The answer is typed
        # into the existing input area; Enter routes through accept_handler to
        # resolve_question. The question/options hint renders in the in-flight region.
        self._question_future: asyncio.Future[str] | None = None

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
            if s.queue_head_preview:
                parts.append(f'{s.queue_depth} queued: "{s.queue_head_preview}"')
            else:
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

    @property
    def prompt_active(self) -> bool:
        """True while an in-app single-key prompt is awaiting a keystroke.

        The app's prompt-mode key bindings (build_key_bindings) gate on this so
        y/n/a/Enter resolve the prompt instead of typing into the input area.
        """
        return self._prompt_future is not None and not self._prompt_future.done()

    @property
    def selection_active(self) -> bool:
        """True while an in-app list picker is awaiting navigation/selection.

        The app's selection-mode key bindings gate on this so up/down/enter/esc
        drive the menu instead of typing into the input area.
        """
        return self._selection_future is not None and not self._selection_future.done()

    @property
    def question_active(self) -> bool:
        """True while a free-text question is awaiting a typed answer.

        The accept_handler gates on this so Enter resolves the question instead
        of arming or queueing a turn.
        """
        return self._question_future is not None and not self._question_future.done()

    def resolve_prompt(self, key: str) -> None:
        """Resolve an active key-prompt with ``key`` (Enter/unknown → default).

        Called from the owned Application's prompt-mode key bindings, which only
        fire while ``prompt_active`` — the None/done guard is defensive belt-and-
        suspenders, never expected to trip on the single event loop.
        """
        future = self._prompt_future
        if future is None or future.done():
            return
        future.set_result(key if key in self._prompt_valid_keys else self._prompt_default)

    async def _prompt_keys(
        self, renderable: RenderableType, *, valid_keys: frozenset[str], default: str
    ) -> str:
        """Render ``renderable`` in the in-flight region and await one keystroke.

        The keystroke is delivered by the running Application's own event loop via
        resolve_prompt — no terminal suspend, so the prompt always surfaces. With
        no owned app bound (headless/stub), returns ``default`` without blocking.
        """
        if self._app is None:
            return default
        self.clear_status()
        self._prompt_valid_keys = valid_keys
        self._prompt_default = default
        self._prompt_future = asyncio.get_running_loop().create_future()
        self._set_inflight(renderable, "prompt")
        try:
            return await self._prompt_future
        finally:
            self._prompt_future = None
            self._prompt_valid_keys = frozenset()
            self._prompt_default = ""
            self._clear_inflight()

    def _render_selection_menu(self) -> RenderableType:
        """Render the list picker (title hint + cursor/current markers) for the in-flight region."""
        lines: list[RenderableType] = [
            Text(f"{self._selection_title} — ↑↓ navigate, Enter select, q cancel", style="hint")
        ]
        for idx, name in enumerate(self._selection_items):
            marker = " *" if name == self._selection_current else ""
            if idx == self._selection_index:
                lines.append(Text(f"▸ {name}{marker}", style="accent"))
            else:
                lines.append(Text(f"  {name}{marker}"))
        return Group(*lines)

    def move_selection(self, delta: int) -> None:
        """Advance the picker cursor by ``delta`` (wrapping) and re-render in-flight."""
        if not self.selection_active:
            return
        self._selection_index = (self._selection_index + delta) % len(self._selection_items)
        self._set_inflight(self._render_selection_menu(), "selection")

    def resolve_selection(self, *, accept: bool) -> None:
        """Resolve the active picker with the highlighted item, or None if cancelled."""
        future = self._selection_future
        if future is None or future.done():
            return
        future.set_result(self._selection_items[self._selection_index] if accept else None)

    async def prompt_selection(
        self, items: list[str], *, title: str = "Select", current: str | None = None
    ) -> str | None:
        """Render a list picker in the in-flight region and await a selection.

        Navigation is delivered by the running Application's own event loop via
        the selection-mode key bindings — no terminal suspend. Returns the chosen
        item, or None if cancelled / empty list / no owned app (headless/stub).
        """
        if not items or self._app is None:
            return None
        self.clear_status()
        self._selection_items = list(items)
        self._selection_title = title
        self._selection_current = current
        self._selection_index = items.index(current) if current in items else 0
        self._selection_future = asyncio.get_running_loop().create_future()
        self._set_inflight(self._render_selection_menu(), "selection")
        try:
            return await self._selection_future
        finally:
            self._selection_future = None
            self._selection_items = []
            self._selection_index = 0
            self._selection_title = ""
            self._selection_current = None
            self._clear_inflight()

    def resolve_question(self, text: str) -> None:
        """Resolve the active free-text question with the typed answer."""
        future = self._question_future
        if future is None or future.done():
            return
        future.set_result(text)

    async def _prompt_free_text(self, renderable: RenderableType) -> str:
        """Render ``renderable`` in the in-flight region and await a typed answer.

        The answer is typed into the existing input area; Enter routes through the
        accept_handler to resolve_question. Returns "" if no owned app (headless/stub).
        """
        if self._app is None:
            return ""
        self.clear_status()
        self._question_future = asyncio.get_running_loop().create_future()
        self._set_inflight(renderable, "question")
        try:
            return await self._question_future
        finally:
            self._question_future = None
            self._clear_inflight()

    async def prompt_approval(self, subject: ApprovalSubject) -> str:
        """Prompt for y/n/a inside the owned app (in-flight panel + key bindings)."""
        hint = Text.assemble(
            "Allow? ",
            ("y", "green"),
            "=once  ",
            ("a", "yellow"),
            "=session  ",
            ("n", "red"),
            "=deny",
        )
        renderable = Group(self._build_approval_panel(subject), hint)
        return await self._prompt_keys(
            renderable, valid_keys=frozenset({"y", "n", "a"}), default="n"
        )

    async def prompt_question(self, prompt: QuestionPrompt) -> str:
        """Prompt for a constrained or free-text answer inside the owned app.

        Single-select with options is a list picker (delegates to prompt_selection);
        multi-select and free-text are typed answers (in-flight hint + input area).
        All paths resolve through the app's own event loop — no terminal suspend.
        """
        if prompt.options and not prompt.multiple:
            choice = await self.prompt_selection(prompt.options, title=prompt.question)
            return choice if choice is not None else prompt.options[0]

        if prompt.options:
            body = (
                f"[accent]{prompt.question}[/accent]\n"
                f"[hint]Options: {' | '.join(prompt.options)} "
                "(select multiple, comma-separated)[/hint]"
            )
        else:
            body = f"[accent]{prompt.question}[/accent]"
        panel = Panel(body, title="Question", border_style="info", title_align="left")
        return await self._prompt_free_text(panel)

    async def prompt_confirm(self, message: str) -> bool:
        """Prompt for a yes/no confirmation inside the owned app."""
        renderable = Text.assemble(message.rstrip() + " ", ("y", "green"), "/", ("n", "red"))
        choice = await self._prompt_keys(renderable, valid_keys=frozenset({"y", "n"}), default="n")
        return choice == "y"

    def cleanup(self) -> None:
        """Clear the in-flight region and tool state on exception/cancellation."""
        self._active_tools.clear()
        self._tool_labels.clear()
        self._clear_inflight()
