"""Stream rendering state machine — text/thinking buffering and flush policy.

StreamRenderer owns all text/thinking buffer state for one stream segment.
It is instantiated once per _execute_stream_segment() call and is not shared
across segments or turns.

Ownership contract:
- append_text / append_thinking: accumulate content, throttle live renders at 20 FPS
- flush_for_tool_output: commit all buffers before a tool surface renders
- finish: commit remaining buffers at normal segment completion

Reasoning display modes:
- off: thinking stream is silently dropped
- collapsed: thinking is surfaced only as a live `Thinking… Ns` header and commits a
             single durable `Thought for Ns` line; the raw body is never shown
- full: the `Thinking… Ns` header is shown with the raw thinking body streamed below it,
        and the body + `Thought for Ns` footer are committed

The live `Thinking… Ns` counter advances on a wall-clock ticker (a thinking-scoped
background task that repaints once per second) so it ticks even when the model emits
reasoning in bursts or goes silent between deltas. The committed `Thought for Ns` is
measured at thinking-end (wall-clock accurate). With no running event loop (sync/headless
callers), the ticker is skipped and the counter degrades to delta-driven updates.
"""

import asyncio
import time
from typing import TYPE_CHECKING

from co_cli.config.core import (
    DEFAULT_REASONING_DISPLAY,
    REASONING_DISPLAY_COLLAPSED,
    REASONING_DISPLAY_FULL,
    REASONING_DISPLAY_OFF,
)

if TYPE_CHECKING:
    from co_cli.display.core import Frontend

_RENDER_INTERVAL = 0.05  # 20 FPS
_TICK_INTERVAL = 1.0  # wall-clock repaint cadence for the live thinking counter


class StreamRenderer:
    """Text/thinking buffer state machine for one stream segment.

    Accepts a Frontend and emits display events to it. The renderer
    tracks whether visible text was streamed (streamed_text property), which
    the orchestrator uses to decide whether on_final_output is needed.
    """

    def __init__(
        self, frontend: "Frontend", *, reasoning_display: str = DEFAULT_REASONING_DISPLAY
    ) -> None:
        self._frontend = frontend
        self._reasoning_display = reasoning_display
        self._text_buffer: str = ""
        self._last_text_render_at: float = 0.0
        self._thinking_buffer: str = ""
        self._last_thinking_render_at: float = 0.0
        self._thinking_active: bool = False
        self._thinking_started_at: float | None = None
        self._ticker_task: asyncio.Task[None] | None = None
        self._streamed_text: bool = False

    @property
    def streamed_text(self) -> bool:
        return self._streamed_text

    def append_text(self, content: str) -> None:
        """Append streamed text content. Flushes pending thinking first."""
        if not content:
            return
        if self._thinking_active or self._thinking_buffer:
            self._flush_thinking()
        self._text_buffer += content
        self._streamed_text = True
        now = time.monotonic()
        if now - self._last_text_render_at >= _RENDER_INTERVAL:
            self._frontend.on_text_delta(self._text_buffer)
            self._last_text_render_at = now

    def append_thinking(self, content: str) -> None:
        """Append thinking content. Behavior depends on reasoning_display mode."""
        if not content or self._reasoning_display == REASONING_DISPLAY_OFF:
            return
        if self._thinking_started_at is None:
            self._thinking_started_at = time.monotonic()
            self._start_ticker()
        self._thinking_buffer += content
        now = time.monotonic()
        if now - self._last_thinking_render_at < _RENDER_INTERVAL:
            return
        self._last_thinking_render_at = now
        self._render_thinking_header()

    def _render_thinking_header(self) -> None:
        """Emit the live `Thinking… Ns` header from current wall-clock elapsed.

        Shared by delta arrival (append_thinking) and the wall-clock ticker. In full
        mode the streamed body trails the header; collapsed mode shows the header only.
        """
        if self._thinking_started_at is None:
            return
        self._thinking_active = True
        elapsed = time.monotonic() - self._thinking_started_at
        header = f"Thinking… {_format_elapsed(elapsed)}"
        if self._reasoning_display == REASONING_DISPLAY_FULL:
            self._frontend.on_thinking_delta(f"{header}\n\n{self._thinking_buffer.rstrip()}")
        else:
            self._frontend.on_thinking_delta(header)

    def _start_ticker(self) -> None:
        """Spawn the wall-clock repaint task, if a running loop is available.

        Without a running loop (sync/headless callers) the counter falls back to
        delta-driven updates — no ticker, no error.
        """
        if self._ticker_task is not None and not self._ticker_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ticker_task = loop.create_task(self._tick())

    async def _tick(self) -> None:
        """Repaint the live header on the wall-clock cadence until thinking ends."""
        try:
            while self._thinking_started_at is not None:
                await asyncio.sleep(_TICK_INTERVAL)
                if self._thinking_started_at is None:
                    return
                self._render_thinking_header()
        except asyncio.CancelledError:
            return

    def _stop_ticker(self) -> None:
        if self._ticker_task is not None:
            self._ticker_task.cancel()
            self._ticker_task = None

    def close(self) -> None:
        """Stop the live ticker — idempotent; called on segment teardown (all paths)."""
        self._stop_ticker()

    def flush_for_tool_output(self) -> None:
        """Flush thinking/text before inline tool annotations and output panels."""
        if self._thinking_active or self._thinking_buffer:
            self._flush_thinking()
        self._commit_text()

    def finish(self) -> bool:
        """Commit remaining buffers at normal segment completion.

        Returns streamed_text — True if visible text was emitted this segment.
        """
        if self._thinking_active or self._thinking_buffer:
            self._flush_thinking()
        self._commit_text()
        return self._streamed_text

    def _flush_thinking(self) -> None:
        self._stop_ticker()
        if self._thinking_started_at is not None:
            elapsed = time.monotonic() - self._thinking_started_at
            footer = f"Thought for {_format_elapsed(elapsed)}"
            if self._reasoning_display == REASONING_DISPLAY_FULL and self._thinking_buffer:
                self._frontend.on_thinking_commit(f"{self._thinking_buffer.rstrip()}\n{footer}")
            elif self._reasoning_display == REASONING_DISPLAY_COLLAPSED:
                self._frontend.on_thinking_commit(footer)
        # off: thinking_started_at stays None (early return in append_thinking), nothing emitted
        self._thinking_buffer = ""
        self._last_thinking_render_at = 0.0
        self._thinking_active = False
        self._thinking_started_at = None

    def _commit_text(self) -> None:
        if self._text_buffer:
            self._frontend.on_text_commit(self._text_buffer)
            self._text_buffer = ""
            self._last_text_render_at = 0.0


def _format_elapsed(seconds: float) -> str:
    """Render an elapsed-seconds count as a compact human label (`8s`, `1m4s`)."""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m{total % 60}s"
