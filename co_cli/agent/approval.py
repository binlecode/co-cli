"""Inline approval collection for the owned loop — pre-fan-out, sequential.

The owned loop has no suspend/resume: it prompts inline, **before** the step's tool
fan-out, and feeds the decisions into ``dispatch_tools`` as denials (denied calls) +
approved ids (the in-body raisers' gate).

Two approval triggers reach the collector, unified here, plus clarify (which asks rather
than approves):

- **Catalog ``is_approval_required=True``** — the tool is marked sensitive in the catalog.
- **Shell's dynamic policy gate** — ``shell_exec`` is *not* catalog-marked; it decides at
  runtime via ``evaluate_shell_command``. The collector re-evaluates that pure-function
  policy here to decide whether to prompt; ``shell_policy`` is side-effect-free so
  evaluating it in the collector and again in the body is free.
- **``clarify``** — asks its questions inline, stashes the answers in
  ``deps.runtime.clarify_answers``, and marks the call approved so the clarify body reads
  the stash and returns the answers.

A denial becomes a ``ToolReturnPart`` fed back to the model and the turn **continues** —
approved siblings still execute. Headless (``frontend is None``) auto-denies standard
approvals (``"n"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pydantic_ai.messages import ToolCallPart, ToolReturnPart

from co_cli.deps import CoDeps
from co_cli.display.core import QuestionPrompt
from co_cli.tools.approvals import (
    decode_tool_args,
    is_auto_approved,
    remember_tool_approval,
    resolve_approval_subject,
)
from co_cli.tools.shell_policy import ShellDecisionEnum, evaluate_shell_command

if TYPE_CHECKING:
    from co_cli.display.core import Frontend


_DENIAL_CONTENT = "User denied this action"
"""The graph's ``ToolDenied`` content (``approvals.py:203``). Matched verbatim so the model
sees the same denial text on both paths."""


@dataclass
class ApprovalResolution:
    """The collector's decisions for one step's tool calls.

    ``denials`` maps a denied call's ``tool_call_id`` to its denial ``ToolReturnPart``;
    ``approved_ids`` is the set of call ids that should run with ``tool_call_approved=True``
    (the in-body raisers' gate). Calls that need no approval appear in neither — they
    execute normally.
    """

    denials: dict[str, ToolReturnPart] = field(default_factory=dict)
    approved_ids: set[str] = field(default_factory=set)


def _denial_part(call: ToolCallPart) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name=call.tool_name,
        content=_DENIAL_CONTENT,
        tool_call_id=call.tool_call_id,
        outcome="denied",
    )


def _shell_needs_approval(call: ToolCallPart, deps: CoDeps) -> bool:
    """Whether a ``shell_exec`` call hits the dynamic REQUIRE_APPROVAL policy gate.

    Shell is the only dynamic-approval tool, so the collector carries this one branch
    (mirroring the shell branch in ``resolve_approval_subject``). If a second
    dynamic-approval tool ever appears, generalize to a per-tool ``approval_probe_fn``
    rather than adding a second string-match here.

    Args are decoded first (they may arrive as a raw JSON string); malformed args →
    ``{}`` → ``cmd=""``, which ``evaluate_shell_command`` classifies as REQUIRE_APPROVAL
    — a harmless prompt-then-empty-cmd that matches the graph on the same input.
    """
    cmd = decode_tool_args(call.args).get("cmd", "")
    policy = evaluate_shell_command(cmd, deps.config.shell.safe_commands)
    return policy.decision == ShellDecisionEnum.REQUIRE_APPROVAL


async def _handle_clarify(
    call: ToolCallPart,
    deps: CoDeps,
    frontend: Frontend | None,
    resolution: ApprovalResolution,
) -> None:
    """Prompt a clarify call's questions inline, stash the answers, mark it approved.

    Build a ``QuestionPrompt`` per question, ask it, stash the answers list in
    ``deps.runtime.clarify_answers`` keyed by ``tool_call_id``, and add the call to
    ``approved_ids`` so the clarify body reads the stash and returns the answers.

    Headless asymmetry: unlike standard approvals (which auto-deny when ``frontend is
    None``), clarify auto-approves with empty answers. So the headless branch stays on the
    approve-with-empty path, not the deny path.
    """
    questions = decode_tool_args(call.args).get("questions", [])
    answers: list[str] = []
    for q in questions:
        raw_opts = q.get("options") if isinstance(q, dict) else None
        labels = [o["label"] if isinstance(o, dict) else o for o in raw_opts] if raw_opts else None
        q_text = (
            q.get("question") or q.get("label") or q.get("text") or q.get("message", "")
            if isinstance(q, dict)
            else str(q)
        )
        prompt = QuestionPrompt(
            question=q_text,
            options=labels,
            multiple=q.get("multiple", False) if isinstance(q, dict) else False,
        )
        answer = (await frontend.prompt_question(prompt)) if frontend is not None else ""
        answers.append(answer)
    deps.runtime.clarify_answers[call.tool_call_id] = answers
    resolution.approved_ids.add(call.tool_call_id)


async def collect_inline_approvals(
    tool_calls: list[ToolCallPart],
    deps: CoDeps,
    frontend: Frontend | None,
    origin_label: str | None = None,
) -> ApprovalResolution:
    """Collect approval decisions for one step's calls, sequentially, before fan-out.

    STRICTLY SEQUENTIAL over ``tool_calls`` in original order — the prompts must never be
    ``asyncio.gather``-ed: the frontend has a single instance-level prompt future
    (``display/core.py``), so concurrent prompts would clobber it. A future "parallelize
    approvals" optimization is therefore a correctness bug, not a speedup.

    For each call: ``clarify`` asks its questions inline and stashes the answers (see
    ``_handle_clarify``); a catalog ``is_approval_required`` tool OR a ``shell_exec`` that
    hits the dynamic REQUIRE_APPROVAL gate needs approval. Auto-approved subjects are added
    to ``approved_ids`` with no prompt; otherwise ``prompt_approval`` (``frontend is None``
    → ``"n"``) decides — ``y``/``a`` approve (``a`` also remembers a rememberable subject),
    ``n`` denies. Calls needing no approval are left untouched (they execute normally).

    ``origin_label`` (set by the delegated-subtask driver) prefixes the prompt's
    ``display`` with ``[<label>] `` so a child's gated call is identifiable as
    delegated-origin even though the child's reasoning stays silent (D-3). It is purely
    cosmetic — auto-approval and remember-choice match on ``kind``/``value``, never
    ``display`` — and defaults ``None`` to keep the orchestrator prompt byte-identical.
    """
    resolution = ApprovalResolution()
    for call in tool_calls:
        if call.tool_name == "clarify":
            await _handle_clarify(call, deps, frontend, resolution)
            continue

        info = deps.tool_catalog.get(call.tool_name)
        needs_approval = (info is not None and info.is_approval_required) or (
            call.tool_name == "shell_exec" and _shell_needs_approval(call, deps)
        )
        if not needs_approval:
            continue

        subject = resolve_approval_subject(call.tool_name, decode_tool_args(call.args), info)
        if is_auto_approved(subject, deps):
            resolution.approved_ids.add(call.tool_call_id)
            continue

        prompt_subject = (
            replace(subject, display=f"[{origin_label}] {subject.display}")
            if origin_label
            else subject
        )
        choice = (await frontend.prompt_approval(prompt_subject)) if frontend is not None else "n"
        if choice in ("y", "a"):
            resolution.approved_ids.add(call.tool_call_id)
            if choice == "a" and subject.can_remember:
                remember_tool_approval(subject, deps)
        else:
            resolution.denials[call.tool_call_id] = _denial_part(call)

    return resolution
