"""Shared types for slash-command handlers and dispatch."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests

from co_cli.deps import CoDeps
from co_cli.display.core import Frontend


@dataclass
class CommandContext:
    """Input bag passed to every slash-command handler."""

    message_history: list[Any]
    deps: CoDeps
    agent: Agent[CoDeps, str | DeferredToolRequests]
    # Holds the live SlashCommandCompleter from chat_loop() — typed Any to keep this
    # module free of prompt_toolkit imports (design boundary). None outside REPL context.
    completer: Any = None
    frontend: Frontend | None = None
    # Live REPL input queue (the same deque _ReplRuntime owns), passed by reference
    # so /queue can inspect/mutate pending items. None outside REPL context
    # (headless, tests that don't exercise the queue).
    input_queue: deque[str] | None = None


@dataclass(frozen=True)
class LocalOnly:
    """Built-in or unknown slash command ran locally; return to prompt."""


@dataclass(frozen=True)
class ReplaceTranscript:
    """Transcript-management command replaced message history."""

    history: list[Any]
    compaction_applied: bool = False


@dataclass(frozen=True)
class DelegateToAgent:
    """Skill command delegated into an agent turn."""

    delegated_input: str
    skill_env: dict[str, str]
    skill_name: str | None


type SlashOutcome = LocalOnly | ReplaceTranscript | DelegateToAgent
