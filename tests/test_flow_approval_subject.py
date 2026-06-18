"""Tests for approval subject resolution and session approval rules."""

import json

import pytest
from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.orchestrate import _collect_deferred_tool_approvals
from co_cli.deps import ApprovalKindEnum, CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.tools.approvals import (
    is_auto_approved,
    remember_tool_approval,
    resolve_approval_subject,
)
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.user_input import clarify


def _fresh_deps() -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, session=CoSessionState())


def test_resolve_shell_scopes_to_utility():
    """Shell approval must scope to the first token (utility) so all git commands share one rule."""
    subject = resolve_approval_subject("shell_exec", {"cmd": "git status --porcelain"})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.value == "git"
    assert subject.can_remember is True
    assert "git status" in subject.display


def test_resolve_shell_empty_cmd_cannot_remember():
    """Empty shell command must produce can_remember=False since there is no utility to scope."""
    subject = resolve_approval_subject("shell_exec", {"cmd": ""})
    assert subject.kind == ApprovalKindEnum.SHELL
    assert subject.can_remember is False


def test_resolve_file_write_scopes_to_parent_directory():
    """file_write approval must scope to the parent directory for directory-wide coverage."""
    subject = resolve_approval_subject(
        "file_write", {"path": "/home/user/project/main.py", "content": "x"}
    )
    assert subject.kind == ApprovalKindEnum.PATH
    assert subject.value == "/home/user/project"
    assert subject.can_remember is True


def test_resolve_web_fetch_scopes_to_domain():
    """web_fetch approval must scope to the hostname for domain-wide coverage."""
    subject = resolve_approval_subject("web_fetch", {"url": "https://example.com/some/path?q=1"})
    assert subject.kind == ApprovalKindEnum.DOMAIN
    assert subject.value == "example.com"
    assert subject.can_remember is True


def test_resolve_unknown_tool_uses_tool_kind():
    """Unrecognized tools must fall back to TOOL kind scoped to the tool name."""
    subject = resolve_approval_subject("memory_create", {"kind": "note", "title": "test"})
    assert subject.kind == ApprovalKindEnum.TOOL
    assert subject.value == "memory_create"
    assert subject.can_remember is True


def test_is_auto_approved_false_with_no_stored_rule():
    """is_auto_approved must return False when no matching rule exists in the session."""
    deps = _fresh_deps()
    subject = resolve_approval_subject("shell_exec", {"cmd": "git status"})
    assert is_auto_approved(subject, deps) is False


def test_remember_and_then_auto_approved():
    """remember_tool_approval must persist a rule that is_auto_approved subsequently matches."""
    deps = _fresh_deps()
    subject = resolve_approval_subject("shell_exec", {"cmd": "git status"})
    remember_tool_approval(subject, deps)
    assert is_auto_approved(subject, deps) is True


def test_remember_no_op_when_cannot_remember():
    """remember_tool_approval must not store a rule when subject.can_remember is False."""
    deps = _fresh_deps()
    subject = resolve_approval_subject("shell_exec", {"cmd": ""})
    remember_tool_approval(subject, deps)
    assert len(deps.session.session_approval_rules) == 0


def test_approval_rule_matches_cross_tool_same_directory():
    """An 'always' rule set by file_write must satisfy file_patch approval for the same directory."""
    deps = _fresh_deps()
    write_subj = resolve_approval_subject("file_write", {"path": "/a/b/x.py", "content": "x"})
    remember_tool_approval(write_subj, deps)
    patch_subj = resolve_approval_subject(
        "file_patch", {"path": "/a/b/y.py", "old_string": "x", "new_string": "y"}
    )
    assert is_auto_approved(patch_subj, deps) is True


def test_approval_rule_does_not_match_different_directory():
    """A rule for one directory must not auto-approve writes in a different directory."""
    deps = _fresh_deps()
    write_subj = resolve_approval_subject("file_write", {"path": "/a/b/x.py", "content": "x"})
    remember_tool_approval(write_subj, deps)
    other_subj = resolve_approval_subject("file_write", {"path": "/a/c/x.py", "content": "x"})
    assert is_auto_approved(other_subj, deps) is False


@pytest.mark.asyncio
async def test_clarify_deferred_approval_routing() -> None:
    """_collect_deferred_tool_approvals must route QuestionRequired to prompt_question and inject the answer.

    Failure mode: "questions" key not detected in metadata → falls through to standard
    approval path → prompt_question never called → user_answers not injected →
    clarify returns error instead of structured output.
    """
    tool_call_id = "clarify-unit-test"
    questions = [
        {
            "question": "Which format?",
            "options": [
                {"label": "json", "description": "JSON"},
                {"label": "text", "description": "Text"},
            ],
        }
    ]

    dtr = DeferredToolRequests(
        approvals=[
            ToolCallPart(
                tool_name="clarify",
                args=json.dumps({"questions": questions}),
                tool_call_id=tool_call_id,
            )
        ],
        metadata={tool_call_id: {"questions": questions}},
    )
    deps = _fresh_deps()
    frontend = HeadlessFrontend(question_answer="json")

    result = await _collect_deferred_tool_approvals(AgentRunResult(dtr), deps, frontend)

    # routing: prompt_question called once per question with correct content
    assert frontend.question_call_count == 1
    assert frontend.last_question is not None
    assert frontend.last_question.question == "Which format?"
    assert frontend.last_question.options == ["json", "text"]

    # injection: answers stashed in runtime keyed by tool_call_id, and the approval is
    # bare (no override_args) so the original `questions` args survive resume validation.
    assert deps.runtime.clarify_answers == {tool_call_id: ["json"]}
    approved = result.approvals.get(tool_call_id)
    assert isinstance(approved, ToolApproved)
    assert approved.override_args is None


@pytest.mark.asyncio
async def test_clarify_resume_returns_answers_as_tool_output() -> None:
    """The approved resume must execute the clarify body and return answers as output.

    Regression guard for the override_args footgun: override_args REPLACES the whole
    args dict, dropping the required `questions` field, so resume validation fails with
    a RetryPromptPart and the answers never reach the model. The deps-injection design
    (bare ToolApproved + runtime.clarify_answers) preserves the original args, so the
    tool re-runs approved and emits a clean ToolReturnPart. This drives the real two-
    run deferred flow through the production clarify tool, no LLM.
    """
    tool_call_id = "clarify-resume"
    call_count = {"n": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="clarify",
                        args={"questions": [{"question": "Which format?"}]},
                        tool_call_id=tool_call_id,
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(model_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
    )
    agent.tool(clarify)
    deps = _fresh_deps()

    # Run 1: model calls clarify → QuestionRequired → deferred approval request.
    result = await agent.run("ask me", deps=deps)
    assert isinstance(result.output, DeferredToolRequests)
    assert [c.tool_name for c in result.output.approvals] == ["clarify"]

    # Orchestrator behaviour: stash answers in runtime, approve with no override_args.
    deps.runtime.clarify_answers[tool_call_id] = ["json"]
    approvals = DeferredToolResults()
    approvals.approvals[tool_call_id] = ToolApproved()

    # Run 2: resume must run the tool body and return the answers as output.
    result2 = await agent.run(
        message_history=result.all_messages(),
        deps=deps,
        deferred_tool_results=approvals,
    )
    returns = [
        part
        for message in result2.new_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "clarify"
    ]
    retries = [
        part
        for message in result2.new_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, RetryPromptPart)
    ]
    assert not retries, f"resume must not produce a validation retry: {retries}"
    assert len(returns) == 1
    assert returns[0].content == json.dumps(["json"])
