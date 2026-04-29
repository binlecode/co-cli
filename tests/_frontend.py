"""Silent frontend implementing the Frontend protocol for test use with run_turn()."""

from typing import Any

from co_cli.display.core import QuestionPrompt
from co_cli.tools.approvals import ApprovalSubject


class SilentFrontend:
    """No-op frontend for tests. Pass approval_response to control deferral behaviour:
    - "y" (default): auto-approve all deferred tools
    - "n": deny all deferred tools
    Pass question_answer to control the response for prompt_question calls.
    """

    def __init__(
        self,
        *,
        approval_response: str = "y",
        confirm_response: bool = False,
        question_answer: str = "",
    ) -> None:
        self.statuses: list[str] = []
        self._approval_response = approval_response
        self._confirm_response = confirm_response
        self._question_answer = question_answer
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

    def prompt_approval(self, subject: ApprovalSubject) -> str:
        self.last_approval_subject = subject
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
