"""Eval-side bootstrap — real CoDeps against the real ``~/.co-cli/`` workspace.

Builds production deps via ``create_deps()`` exactly as ``main.py`` does — no
``CO_HOME`` override, no temp dirs, no inline model/settings overrides. The
only adapter is :class:`EvalFrontend`, a ``TerminalFrontend`` subclass that
overrides interactive prompts to non-interactive deterministic returns so
``run_turn`` and slash dispatch can drive end-to-end without blocking.

Lifetime: ``create_deps`` registers MCP servers + cleanup hooks on an
``AsyncExitStack``. ``eval_deps()`` is therefore an async context manager —
exiting the ``async with`` cleanly tears down the stack.

Approval bypass: the plan called for an allow-all rule in
``session_approval_rules``, but ``is_auto_approved`` matches rules by exact
``(kind, value)`` (``co_cli/tools/approvals.py:166``) — a wildcard entry
won't auto-approve a dynamic subject. The bypass is implemented at the
frontend layer instead: ``EvalFrontend.prompt_approval`` returns ``"a"``
(always-approve-and-remember), driving the production approval path through
``record_approval_choice`` so the first subject hit is auto-approved on
every subsequent call this session. This is a protocol-compliant frontend
implementation — no mock, no patch — that uses values returned from real
production code (an ApprovalSubject and a choice from the real ``y/n/a``
set).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from pydantic_ai import Agent

from co_cli.agent.build import build_orchestrator
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.bootstrap.core import create_deps
from co_cli.deps import ApprovalSubject, CoDeps
from co_cli.display.core import QuestionPrompt, TerminalFrontend


class EvalFrontend(TerminalFrontend):
    """Non-interactive ``TerminalFrontend`` for eval-driven turns.

    Overrides only the blocking prompt surfaces — rendering surfaces
    (``on_status``, ``on_text_delta``, etc.) inherit from ``TerminalFrontend``
    unchanged so the production display pipeline still emits the same
    structured-log spans (``co_cli.observability.tracing``) and stdout
    output a real REPL would produce.
    """

    def prompt_approval(self, subject: ApprovalSubject) -> str:
        return "a" if subject.can_remember else "y"

    def prompt_question(self, prompt: QuestionPrompt) -> str:
        if prompt.options:
            return prompt.options[0]
        return ""

    def prompt_confirm(self, message: str) -> bool:
        return True


@asynccontextmanager
async def eval_deps(
    theme_override: str | None = None,
) -> AsyncIterator[tuple[CoDeps, Agent[CoDeps, Any], EvalFrontend]]:
    """Yield a fully-bootstrapped ``(deps, agent, frontend)`` for the eval lifetime.

    Mirrors ``main.py:_chat_loop``'s bootstrap exactly: ``create_deps`` on a
    managed ``AsyncExitStack``, then ``build_orchestrator(ORCHESTRATOR_SPEC, deps)``.
    The stack is closed when the ``async with`` exits, tearing down MCP
    servers and any other registered cleanups.
    """
    frontend = EvalFrontend()
    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack, theme_override=theme_override)
        agent = build_orchestrator(ORCHESTRATOR_SPEC, deps)
        yield deps, agent, frontend


async def make_eval_deps() -> tuple[CoDeps, Agent[CoDeps, Any], EvalFrontend, AsyncExitStack]:
    """Imperative variant — returns deps + an open ``AsyncExitStack`` the caller closes.

    Prefer :func:`eval_deps` (async context manager) for new code. This form
    is provided for the import-smoke check in the plan's ``done_when`` and
    for simple linear eval scripts that don't naturally nest under an
    ``async with``. Caller must ``await stack.aclose()`` in a ``finally``.
    """
    frontend = EvalFrontend()
    stack = AsyncExitStack()
    await stack.__aenter__()
    deps = await create_deps(frontend, stack, theme_override=None)
    agent = build_orchestrator(ORCHESTRATOR_SPEC, deps)
    return deps, agent, frontend, stack
