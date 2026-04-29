"""Headless frontend — full Frontend protocol implementation for evals and tests."""

import time
from typing import Any

from co_cli.display.core import QuestionPrompt
from co_cli.tools.approvals import ApprovalSubject


class HeadlessFrontend:
    """No-op frontend implementing the full Frontend protocol.

    Suitable for evals and tests where there is no terminal to render to.

    Configurable responses:
        approval_response: returned by prompt_approval ("y" auto-approves everything)
        confirm_response:  returned by prompt_confirm
        question_answer:   returned by prompt_question
        verbose:           print status messages to stdout as they arrive

    Recorded state (inspect after a run):
        statuses:               all on_status messages in order
        approval_calls:         display text of each prompt_approval call
        status_timeline:        (elapsed_ms_since_construction, message) for each status
        last_approval_subject:  most recent ApprovalSubject passed to prompt_approval
        last_question:          most recent QuestionPrompt passed to prompt_question
        question_call_count:    total prompt_question invocations
    """

    def __init__(
        self,
        *,
        approval_response: str = "y",
        confirm_response: bool = False,
        question_answer: str = "",
        verbose: bool = False,
    ) -> None:
        self._approval_response = approval_response
        self._confirm_response = confirm_response
        self._question_answer = question_answer
        self._verbose = verbose
        self._t0 = time.monotonic()

        self.statuses: list[str] = []
        self.approval_calls: list[str] = []
        self.status_timeline: list[tuple[float, str]] = []
        self.last_approval_subject: ApprovalSubject | None = None
        self.last_question: QuestionPrompt | None = None
        self.question_call_count: int = 0

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_reasoning_progress(self, message: str) -> None:
        pass

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        pass

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        pass

    def on_tool_complete(self, tool_id: str, result: Any) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)
        self.status_timeline.append(((time.monotonic() - self._t0) * 1000, message))
        if self._verbose:
            print(f"    STATUS: {message}")  # noqa: T201 — intentional verbose output for eval diagnostics

    def on_final_output(self, text: str) -> None:
        pass

    def prompt_approval(self, subject: ApprovalSubject) -> str:
        self.last_approval_subject = subject
        self.approval_calls.append(subject.display)
        return self._approval_response

    def prompt_question(self, prompt: QuestionPrompt) -> str:
        self.last_question = prompt
        self.question_call_count += 1
        return self._question_answer

    def prompt_confirm(self, message: str) -> bool:
        return self._confirm_response

    def clear_status(self) -> None:
        pass

    def set_input_active(self, active: bool) -> None:
        pass

    def cleanup(self) -> None:
        pass
