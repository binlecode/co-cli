"""Silent frontend implementing the Frontend protocol for test use with run_turn()."""

from typing import Any


class SilentFrontend:
    """No-op frontend for tests. Pass approval_response to control deferral behaviour:
    - "y" (default): auto-approve all deferred tools
    - "n": deny all deferred tools
    """

    def __init__(self, *, approval_response: str = "y") -> None:
        self.statuses: list[str] = []
        self._approval_response = approval_response

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        pass

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        pass

    def on_tool_complete(self, tool_id: str, result: Any) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)

    def on_reasoning_progress(self, text: str) -> None:
        pass

    def on_final_output(self, text: str) -> None:
        pass

    def prompt_approval(self, description: str) -> str:
        return self._approval_response

    def cleanup(self) -> None:
        pass
