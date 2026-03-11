"""Eval frontend stubs implementing FrontendProtocol for agent interaction."""

from typing import Any


class SilentFrontend:
    """Minimal frontend that captures status messages.

    Pass ``approval_response`` to control tool approval behaviour:
      - ``"y"`` (default): auto-approve everything
      - ``"n"``: deny everything
    """

    def __init__(self, *, approval_response: str = "y"):
        self.statuses: list[str] = []
        self.final_text: str | None = None
        self._approval_response = approval_response

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_call(self, name: str, args_display: str) -> None:
        pass

    def on_tool_result(self, title: str, content: Any) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)

    def on_final_output(self, text: str) -> None:
        self.final_text = text

    def prompt_approval(self, description: str) -> str:
        return self._approval_response

    def cleanup(self) -> None:
        pass


class CapturingFrontend(SilentFrontend):
    """Frontend that records approval calls for assertions.

    Extends SilentFrontend (which already records statuses) with an
    ``approval_calls`` list. Pass ``verbose=True`` to also print status
    messages to stdout as they arrive — useful for diagnosing grace-turn
    and approval-path failures.
    """

    def __init__(self, *, approval_response: str = "y", verbose: bool = False):
        super().__init__(approval_response=approval_response)
        self.approval_calls: list[str] = []
        self._verbose = verbose

    def on_status(self, message: str) -> None:
        super().on_status(message)
        if self._verbose:
            print(f"    STATUS: {message}")

    def prompt_approval(self, description: str) -> str:
        self.approval_calls.append(description)
        return self._approval_response
