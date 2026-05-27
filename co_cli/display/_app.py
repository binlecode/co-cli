"""prompt_toolkit Application factory — the single-owner inline REPL driver.

Builds the persistent ``Application(full_screen=False)`` that owns the terminal:
a streaming in-flight window, the input ``TextArea`` (completion + history), and
the bottom-toolbar window. Imports ``render_to_ansi`` from ``core`` — never the
reverse (F2). ``__init__.py`` stays docstring-only per the package convention.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_toolkit import ANSI, Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import History
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style, default_ui_style, merge_styles
from prompt_toolkit.widgets import TextArea

from co_cli.commands.completer import SlashCommandCompleter
from co_cli.display.core import PROMPT_CHAR, TerminalFrontend

if TYPE_CHECKING:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent

    from co_cli.main import _IterationState

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
class _ReplRuntime:
    """Single mutable owner of chat-loop turn state (F7).

    Shared by the ``accept_handler`` (drop submissions while a turn is active)
    and the Ctrl+C key binding (cancel the active turn). Created in ``_chat_loop``
    scope and passed by reference — never a module global. Holds the turn-task
    reference so the ``c-c`` binding can cancel a running turn (BC2 mid-turn).
    """

    state: "_IterationState"
    turn_task: "asyncio.Task[None] | None" = None
    control_tasks: "set[asyncio.Task[None]]" = field(default_factory=set)

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


def build_key_bindings(*, runtime: _ReplRuntime, dispatch: Dispatch) -> KeyBindings:
    """Build the REPL key bindings.

    ``c-c``: if a turn is active, cancel it (BC2 — blunt mid-turn turn-cancel is
    kept; interrupt-with-requeue/steering is deferred to repl-input-queue), then
    route to ``_handle_one_input(user_input=None)`` so the 2 s double-press-exit
    window is armed identically to today.
    ``c-d``: route to ``_handle_one_input(eof=True)``.
    """
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event: "KeyPressEvent") -> None:
        if runtime.turn_active and runtime.turn_task is not None:
            runtime.turn_task.cancel()
        runtime.schedule_control(dispatch(user_input=None, eof=False))

    @kb.add("c-d")
    def _(event: "KeyPressEvent") -> None:
        runtime.schedule_control(dispatch(user_input=None, eof=True))

    return kb


def build_repl_app(
    *,
    frontend: TerminalFrontend,
    completer: SlashCommandCompleter,
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
        prompt=f"Co {PROMPT_CHAR} ",
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
