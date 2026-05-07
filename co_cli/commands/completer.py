"""Slash-command completer for the prompt_toolkit REPL input bar."""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


class SlashCommandCompleter(Completer):
    """Auto-complete for '/' prefix: shows all commands on '/', filters by prefix as user types."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, str]] = []

    def update(self, entries: list[tuple[str, str]]) -> None:
        self._entries = entries

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        prefix = text[1:]
        for name, description in self._entries:
            if name.startswith(prefix):
                yield Completion(
                    f"/{name}",
                    start_position=-len(text),
                    display_meta=description,
                )
