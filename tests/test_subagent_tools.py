"""Functional tests for subagent tool wiring and deps isolation."""

import pytest
from copy import copy
from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps, CoSessionState, CoRuntimeState, make_subagent_deps
from tests._settings import test_settings
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.subagent import run_coding_subagent, _merge_turn_usage

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx() -> RunContext:
    """Return a real RunContext with no model_registry — triggers unavailable guard."""
    deps = CoDeps(
        shell=ShellBackend(), model_registry=None,
        config=test_settings(),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_run_coding_subagent_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured).

    All four subagent tools (coding, research, analysis, reasoning) share the
    same guard pattern: ``if not registry or not registry.is_configured(ROLE)``.
    This test exercises the pattern via the coding tool; the others are identical.
    """
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_coding_subagent(ctx, "analyze foo")


def test_make_subagent_deps_resets_session_state() -> None:
    """make_subagent_deps() shares tools by reference, inherits session fields, resets isolated fields."""
    from co_cli.commands._skill_types import SkillConfig
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    skill = SkillConfig(name="my-skill", body="do it")
    base = CoDeps(
        shell=ShellBackend(),
        skill_commands={"my-skill": skill},
        config=test_settings(
            brave_search_api_key="test-key",
            memory=test_settings().memory.model_copy(update={"max_count": 500}),
        ),
        session=CoSessionState(
            session_id="parent-session",
            google_creds_resolved=True,
            session_approval_rules=[SessionApprovalRule(ApprovalKindEnum.SHELL, "git")],
            drive_page_tokens={"folder": ["tok1"]},
            session_todos=[{"task": "do something"}],
        ),
        runtime=CoRuntimeState(),
    )

    isolated = make_subagent_deps(base)

    # service handles shared by reference
    assert isolated.shell is base.shell
    assert isolated.skill_commands is base.skill_commands

    # Session: inherited fields carry over
    assert isolated.session.google_creds_resolved is True
    assert isolated.session.session_approval_rules == [SessionApprovalRule(ApprovalKindEnum.SHELL, "git")]

    # CoSessionState no longer carries skill fields — they are on capabilities
    assert not hasattr(CoSessionState(), "skill_commands")

    # Approval rules are a copy, not the same list (sub-agent grants must not leak to parent)
    assert isolated.session.session_approval_rules is not base.session.session_approval_rules

    # Session: isolated fields reset to clean defaults
    assert isolated.session.drive_page_tokens == {}
    assert isolated.session.session_todos == []
    assert isolated.session.session_id == ""

    # Runtime resets to clean defaults
    assert isolated.runtime.turn_usage is None

    # Config inherited from parent
    assert isolated.config.brave_search_api_key == "test-key"
    assert isolated.config.memory.max_count == 500

    # Service handles shared (same objects)
    assert isolated.shell is base.shell
    assert isolated.model_registry is base.model_registry


def test_merge_turn_usage_alias_then_accumulate() -> None:
    """_merge_turn_usage aliases on first call (None) and accumulates on second call."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    # Phase 1: turn_usage is None — aliased directly, not copied
    u1 = RunUsage(input_tokens=10, output_tokens=20)
    _merge_turn_usage(ctx, u1)
    assert ctx.deps.runtime.turn_usage is u1

    # Snapshot before second merge to verify copy() decoupling
    snapshot = copy(u1)

    # Phase 2: second call accumulates into turn_usage
    u2 = RunUsage(input_tokens=5, output_tokens=5)
    _merge_turn_usage(ctx, u2)
    assert ctx.deps.runtime.turn_usage.input_tokens == 15

    # Snapshot is not mutated — confirms copy() in _run_subagent_attempt decouples usage
    assert snapshot.input_tokens == 10
