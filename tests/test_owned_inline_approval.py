"""Behavioral tests for the owned loop's inline approval collector.

Asserts observable decisions: a catalog approval-gated call is denied/approved/auto-approved
per the scripted choice, ``a`` records a session rule so a second same-subject call needs no
prompt, headless auto-denies, and a ``shell_exec`` REQUIRE_APPROVAL command prompts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ToolCallPart
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.approval import collect_inline_approvals
from co_cli.deps import (
    ApprovalSubject,
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.display.core import QuestionPrompt
from co_cli.tools.shell_backend import ShellBackend


class ScriptedFrontend:
    """Frontend stub returning fixed approval/question answers and recording prompts."""

    def __init__(self, choice: str = "n", answer: str = "ANSWER") -> None:
        self.choice = choice
        self.answer = answer
        self.prompts: list[ApprovalSubject] = []
        self.questions: list[QuestionPrompt] = []

    async def prompt_approval(self, subject: ApprovalSubject) -> str:
        self.prompts.append(subject)
        return self.choice

    async def prompt_question(self, prompt: QuestionPrompt) -> str:
        self.questions.append(prompt)
        return self.answer


def _info(name: str, *, approval: bool) -> ToolInfo:
    return ToolInfo(
        name=name,
        description="test",
        is_approval_required=approval,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
        is_concurrent_safe=True,
    )


def _deps(tmp_path: Path, catalog: dict[str, ToolInfo]) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path / "tool-results",
        tool_catalog=catalog,
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )


def _call(name: str, args: dict, tool_call_id: str = "c1") -> ToolCallPart:
    return ToolCallPart(tool_name=name, args=args, tool_call_id=tool_call_id)


@pytest.mark.asyncio
async def test_catalog_call_approved_on_yes(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"danger": _info("danger", approval=True)})
    frontend = ScriptedFrontend("y")
    res = await collect_inline_approvals([_call("danger", {})], deps, frontend)
    assert res.approved_ids == {"c1"}
    assert res.denials == {}


@pytest.mark.asyncio
async def test_catalog_call_denied_on_no(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"danger": _info("danger", approval=True)})
    frontend = ScriptedFrontend("n")
    res = await collect_inline_approvals([_call("danger", {})], deps, frontend)
    assert res.approved_ids == set()
    assert "c1" in res.denials
    assert res.denials["c1"].outcome == "denied"


@pytest.mark.asyncio
async def test_auto_approved_subject_skips_prompt(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"danger": _info("danger", approval=True)})
    frontend = ScriptedFrontend("n")
    # Pre-approve the tool subject this session.
    from co_cli.tools.approvals import remember_tool_approval, resolve_approval_subject

    subject = resolve_approval_subject("danger", {}, deps.tool_catalog["danger"])
    remember_tool_approval(subject, deps)

    res = await collect_inline_approvals([_call("danger", {})], deps, frontend)
    assert res.approved_ids == {"c1"}
    assert frontend.prompts == [], "auto-approved subject does not prompt"


@pytest.mark.asyncio
async def test_always_records_rule_for_second_call(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"danger": _info("danger", approval=True)})
    frontend = ScriptedFrontend("a")
    await collect_inline_approvals([_call("danger", {}, "first")], deps, frontend)

    # Second same-subject call with a deny-scripted frontend must auto-approve (rule stored).
    deny = ScriptedFrontend("n")
    res = await collect_inline_approvals([_call("danger", {}, "second")], deps, deny)
    assert res.approved_ids == {"second"}
    assert deny.prompts == [], "remembered subject is not re-prompted"


@pytest.mark.asyncio
async def test_headless_auto_denies(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"danger": _info("danger", approval=True)})
    res = await collect_inline_approvals([_call("danger", {})], deps, None)
    assert res.approved_ids == set()
    assert "c1" in res.denials


@pytest.mark.asyncio
async def test_shell_require_approval_prompts(tmp_path: Path) -> None:
    # shell_exec is NOT catalog-marked; the dynamic policy gate drives the prompt.
    deps = _deps(tmp_path, {"shell_exec": _info("shell_exec", approval=False)})
    frontend = ScriptedFrontend("y")
    res = await collect_inline_approvals(
        [_call("shell_exec", {"cmd": "rm -rf build"})], deps, frontend
    )
    assert len(frontend.prompts) == 1, "REQUIRE_APPROVAL shell command prompts"
    assert res.approved_ids == {"c1"}


@pytest.mark.asyncio
async def test_clarify_prompts_and_stashes_answers(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"clarify": _info("clarify", approval=False)})
    frontend = ScriptedFrontend(answer="blue")
    call = _call("clarify", {"questions": [{"question": "favorite color?"}]}, "clr")
    res = await collect_inline_approvals([call], deps, frontend)

    assert len(frontend.questions) == 1
    assert frontend.questions[0].question == "favorite color?"
    assert res.approved_ids == {"clr"}, "clarify is marked approved so its body runs"
    assert deps.runtime.clarify_answers["clr"] == ["blue"], "answers stashed by tool_call_id"


@pytest.mark.asyncio
async def test_clarify_collector_to_dispatch_returns_answers(tmp_path: Path) -> None:
    # End-to-end: collector stashes answers + approves, dispatch runs the real clarify
    # body, which reads the stash and returns the answers as positional JSON.
    import json

    from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
    from co_cli.agent.dispatch import dispatch_tools
    from co_cli.agent.turn_state import ToolCapState

    native, catalog = build_native_toolset()
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path / "tool-results",
        tool_catalog=catalog,
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = assemble_routing_toolset(native, [])

    frontend = ScriptedFrontend(answer="green")
    call = _call("clarify", {"questions": [{"question": "color?"}]}, "clr")
    res = await collect_inline_approvals([call], deps, frontend)
    parts = await dispatch_tools(
        [call], deps, cap_state=ToolCapState(), approved_ids=res.approved_ids
    )

    assert len(parts) == 1
    assert json.loads(parts[0].content) == ["green"], "clarify body returns stashed answers"


@pytest.mark.asyncio
async def test_clarify_headless_approves_with_empty(tmp_path: Path) -> None:
    # CD-m-5 asymmetry: headless clarify approves with empty answers, NOT auto-deny.
    deps = _deps(tmp_path, {"clarify": _info("clarify", approval=False)})
    call = _call("clarify", {"questions": [{"question": "q1?"}, {"question": "q2?"}]}, "clr")
    res = await collect_inline_approvals([call], deps, None)

    assert res.approved_ids == {"clr"}, "headless clarify is approved (not denied)"
    assert res.denials == {}
    assert deps.runtime.clarify_answers["clr"] == ["", ""], "empty answer per question"


@pytest.mark.asyncio
async def test_non_approval_call_untouched(tmp_path: Path) -> None:
    deps = _deps(tmp_path, {"safe": _info("safe", approval=False)})
    frontend = ScriptedFrontend("n")
    res = await collect_inline_approvals([_call("safe", {})], deps, frontend)
    assert res.approved_ids == set()
    assert res.denials == {}
    assert frontend.prompts == [], "a non-approval tool is never prompted"
