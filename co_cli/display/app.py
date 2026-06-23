"""prompt_toolkit Application factory — the single-owner inline REPL driver.

Builds the persistent ``Application(full_screen=False)`` that owns the terminal:
a streaming in-flight window, the input ``TextArea`` (completion + history), and
the bottom-toolbar window. Imports ``render_to_ansi`` from ``core`` — never the
reverse (F2). ``__init__.py`` stays docstring-only per the package convention.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_toolkit import ANSI, Application
from prompt_toolkit.completion import Completer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import History
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style, default_ui_style, merge_styles
from prompt_toolkit.widgets import TextArea

from co_cli.display.core import TerminalFrontend, glyphs

if TYPE_CHECKING:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent

    from co_cli.main import IterationState

# Completion-menu styling — carried over verbatim from the old PromptSession so
# the slash-completion menu looks identical under the owned Application (CD-m-4).
_COMPLETION_STYLE = Style.from_dict(
    {
        "completion-menu": "bg:default",
        "completion-menu.completion": "bg:default",
        "completion-menu.completion.current": "bold bg:default",
        "completion-menu.meta.completion": "fg:#888888 bg:default",
        "completion-menu.meta.completion.current": "fg:#aaaaaa bg:default bold",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "fg:#888888 bg:default",
    }
)

# Dispatch coroutine: (user_input, eof) -> awaitable that runs one chat-loop
# iteration via _handle_one_input and applies its result.
Dispatch = Callable[..., Awaitable[None]]


@dataclass
class ReplRuntime:
    """Single mutable owner of chat-loop turn state (F7).

    Shared by the ``accept_handler`` (enqueue submissions while a turn is active)
    and the key bindings (``Esc`` cancels the active turn, ``c-c`` arms exit).
    Created in ``_chat_loop`` scope and passed by reference — never a module
    global. Holds the turn-task reference so ``Esc`` can cancel a running turn,
    and the ``queue`` of input strings buffered while a turn is active (drained
    one item per turn boundary). The queue element type is ``str`` by design but
    not a frozen contract — see the plan's HLD.
    """

    state: "IterationState"
    turn_task: "asyncio.Task[None] | None" = None
    control_tasks: "set[asyncio.Task[None]]" = field(default_factory=set)
    queue: "deque[str]" = field(default_factory=deque)

    @property
    def turn_active(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()

    def schedule_control(self, coro: Awaitable[None]) -> "asyncio.Task[None]":
        """Schedule a control coroutine (interrupt/EOF dispatch), retaining a
        strong reference until it finishes so it is not garbage-collected."""
        task = asyncio.ensure_future(coro)
        self.control_tasks.add(task)
        task.add_done_callback(self.control_tasks.discard)
        return task


def build_key_bindings(
    *, runtime: ReplRuntime, dispatch: Dispatch, frontend: TerminalFrontend
) -> KeyBindings:
    """Build the REPL key bindings.

    Prompt mode (``y``/``a``/``n``/``enter``, filtered by ``frontend.prompt_active``,
    ``eager`` so they resolve before the input area inserts the char): an in-app
    single-key prompt (approval / confirm) renders in the in-flight region and is
    resolved here via ``frontend.resolve_prompt`` on the running app's own event
    loop — replacing the run_in_terminal path that deadlocked the owned app.
    ``escape``: if a turn is active, cancel it; the turn task's done-callback
    drains the next queued item, so Esc interrupts and advances the queue (C4).
    Idle: no-op (this binding overrides prompt_toolkit's default escape, so the
    idle branch inherits no free line-clear — the no-op is intentional, C6).
    ``c-c``: route to ``_handle_one_input(user_input=None)`` to arm the 2 s
    double-press-exit window — **without** cancelling the turn (interrupt moved
    to Esc). A double-press exit tears the app down, which cancels any in-flight
    turn. Dispatched via ``schedule_control`` so it is not routed through the
    turn-arming path (only turns get the drain callback) and is not GC'd.
    ``c-d``: route to ``_handle_one_input(eof=True)``.
    """
    kb = KeyBindings()
    prompt_active = Condition(lambda: frontend.prompt_active)
    selection_active = Condition(lambda: frontend.selection_active)

    @kb.add("y", filter=prompt_active, eager=True)
    @kb.add("a", filter=prompt_active, eager=True)
    @kb.add("n", filter=prompt_active, eager=True)
    def _(event: "KeyPressEvent") -> None:
        frontend.resolve_prompt(event.key_sequence[0].data)

    @kb.add("enter", filter=prompt_active, eager=True)
    def _(event: "KeyPressEvent") -> None:
        frontend.resolve_prompt("")

    # Selection-mode bindings (list picker): up/down navigate, enter selects,
    # escape/q cancel. Eager so they resolve before the input area sees the key.
    @kb.add("up", filter=selection_active, eager=True)
    def _(event: "KeyPressEvent") -> None:
        frontend.move_selection(-1)

    @kb.add("down", filter=selection_active, eager=True)
    def _(event: "KeyPressEvent") -> None:
        frontend.move_selection(1)

    @kb.add("enter", filter=selection_active, eager=True)
    def _(event: "KeyPressEvent") -> None:
        frontend.resolve_selection(accept=True)

    @kb.add("escape", filter=selection_active, eager=True)
    @kb.add("q", filter=selection_active, eager=True)
    def _(event: "KeyPressEvent") -> None:
        frontend.resolve_selection(accept=False)

    @kb.add("escape")
    def _(event: "KeyPressEvent") -> None:
        if runtime.turn_active and runtime.turn_task is not None:
            runtime.turn_task.cancel()

    @kb.add("c-c")
    def _(event: "KeyPressEvent") -> None:
        runtime.schedule_control(dispatch(user_input=None, eof=False))

    @kb.add("c-d")
    def _(event: "KeyPressEvent") -> None:
        runtime.schedule_control(dispatch(user_input=None, eof=True))

    return kb


def build_repl_app(
    *,
    frontend: TerminalFrontend,
    completer: Completer,
    history: History,
    accept_handler: "Callable[[Buffer], bool]",
    key_bindings: KeyBindings,
) -> Application:
    """Assemble the inline single-owner REPL Application.

    Layout (top → bottom): the in-flight streaming window (shown only while the
    frontend holds in-flight content), the input ``TextArea``, and the
    bottom-toolbar window. Committed transcript output is printed to scrollback
    above the app via ``print_formatted_text`` (under ``patch_stdout``).
    """
    input_area = TextArea(
        prompt=f"{glyphs().prompt} ",
        multiline=False,
        completer=completer,
        complete_while_typing=True,
        history=history,
        accept_handler=accept_handler,
        height=1,
        wrap_lines=True,
    )
    inflight_window = ConditionalContainer(
        Window(FormattedTextControl(lambda: ANSI(frontend.get_inflight()))),
        filter=Condition(lambda: bool(frontend.get_inflight())),
    )
    toolbar_window = Window(
        FormattedTextControl(lambda: frontend.render_footer_toolbar()),
        height=1,
        style="class:bottom-toolbar",
    )
    layout = Layout(
        HSplit([inflight_window, input_area, toolbar_window]),
        focused_element=input_area,
    )
    style = merge_styles([default_ui_style(), _COMPLETION_STYLE])
    return Application(
        layout=layout,
        key_bindings=key_bindings,
        style=style,
        full_screen=False,
        mouse_support=False,
    )
