"""Stream rendering state machine — text/thinking buffering and flush policy.

StreamRenderer owns all text/thinking buffer state for one stream segment.
It is instantiated once per _execute_stream_segment() call and is not shared
across segments or turns.

Ownership contract:
- append_text / append_thinking: accumulate content, throttle live renders at 20 FPS
- flush_for_tool_output: commit all buffers before a tool surface renders
- finish: commit remaining buffers at normal segment completion
- install_progress / clear_progress: manage the turn-scoped progress callback
"""

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.display._core import Frontend

_RENDER_INTERVAL = 0.05  # 20 FPS


class StreamRenderer:
    """Text/thinking buffer state machine for one stream segment.

    Accepts a Frontend and emits display events to it. The renderer
    tracks whether visible text was streamed (streamed_text property), which
    the orchestrator uses to decide whether on_final_output is needed.
    """

    def __init__(self, frontend: "Frontend") -> None:
        self._frontend = frontend
        self._text_buffer: str = ""
        self._last_text_render_at: float = 0.0
        self._thinking_buffer: str = ""
        self._last_thinking_render_at: float = 0.0
        self._thinking_active: bool = False
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

    def append_thinking(self, content: str, *, verbose: bool) -> None:
        """Append thinking content. Hidden when verbose=False."""
        if not content or not verbose:
            return
        self._thinking_buffer += content
        now = time.monotonic()
        if now - self._last_thinking_render_at >= _RENDER_INTERVAL:
            self._thinking_active = True
            self._frontend.on_thinking_delta(self._thinking_buffer.rstrip() or "...")
            self._last_thinking_render_at = now

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

    def install_progress(self, deps: "CoDeps", tool_id: str) -> None:
        """Install the turn-scoped progress callback for the active tool surface.

        Called when a FunctionToolCallEvent arrives. The callback routes progress
        messages to the frontend for the specific tool_id.
        """
        deps.runtime.tool_progress_callback = (
            lambda msg, _tid=tool_id: self._frontend.on_tool_progress(_tid, msg)
        )

    def clear_progress(self, deps: "CoDeps") -> None:
        """Clear the turn-scoped progress callback.

        Called on tool completion (normal, retry/validation failure, or interrupt).
        """
        deps.runtime.tool_progress_callback = None

    def _flush_thinking(self) -> None:
        if self._thinking_buffer:
            self._frontend.on_thinking_commit(self._thinking_buffer.rstrip())
            self._thinking_buffer = ""
            self._last_thinking_render_at = 0.0
            self._thinking_active = False

    def _commit_text(self) -> None:
        if self._text_buffer:
            self._frontend.on_text_commit(self._text_buffer)
            self._text_buffer = ""
            self._last_text_render_at = 0.0
