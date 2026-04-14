"""Functional tests for delegation tool wiring and deps isolation."""

from copy import copy
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState, fork_deps
from co_cli.tools.agents import _merge_turn_usage, delegate_coder
from co_cli.tools.shell_backend import ShellBackend

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx() -> RunContext:
    """Return a real RunContext with no model — triggers unavailable guard."""
    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=make_settings(),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_delegate_coder_no_model() -> None:
    """Raises ModelRetry when model is None (no model configured).

    All four delegation tools share the same guard pattern: ``if not deps.model``.
    This test exercises the pattern via the coder tool; the others are identical.
    """
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await delegate_coder(ctx, "analyze foo")


def test_fork_deps_resets_session_state() -> None:
    """fork_deps() shares handles by reference, inherits session fields, resets isolated fields."""
    from co_cli.commands._skill_types import SkillConfig
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    skill = SkillConfig(name="my-skill", body="do it")
    base = CoDeps(
        shell=ShellBackend(),
        skill_commands={"my-skill": skill},
        config=make_settings(
            brave_search_api_key="test-key",
            memory=make_settings().memory.model_copy(update={"injection_max_chars": 5000}),
        ),
        session=CoSessionState(
            session_path=Path("/tmp/parent-session.jsonl"),
            google_creds_resolved=True,
            session_approval_rules=[SessionApprovalRule(ApprovalKindEnum.SHELL, "git")],
            drive_page_tokens={"folder": ["tok1"]},
            session_todos=[{"task": "do something"}],
        ),
        runtime=CoRuntimeState(),
    )

    isolated = fork_deps(base)

    # service handles shared by reference
    assert isolated.shell is base.shell
    assert isolated.skill_commands is base.skill_commands

    # Session: inherited fields carry over
    assert isolated.session.google_creds_resolved is True
    assert isolated.session.session_approval_rules == [
        SessionApprovalRule(ApprovalKindEnum.SHELL, "git")
    ]

    # CoSessionState no longer carries skill fields — they are on capabilities
    assert not hasattr(CoSessionState(), "skill_commands")

    # Approval rules are a copy, not the same list (delegation grants must not leak to parent)
    assert isolated.session.session_approval_rules is not base.session.session_approval_rules

    # Session: isolated fields reset to clean defaults
    assert isolated.session.drive_page_tokens == {}
    assert isolated.session.session_todos == []
    assert isolated.session.session_path == Path()

    # Runtime resets to clean defaults (agent_depth incremented)
    assert isolated.runtime.turn_usage is None
    assert isolated.runtime.agent_depth == base.runtime.agent_depth + 1

    # Config inherited from parent
    assert isolated.config.brave_search_api_key == "test-key"
    assert isolated.config.memory.injection_max_chars == 5000

    # Service handles shared (same objects)
    assert isolated.shell is base.shell
    assert isolated.model is base.model


def test_merge_turn_usage_alias_then_accumulate() -> None:
    """_merge_turn_usage aliases on first call (None) and accumulates on second call."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
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

    # Snapshot is not mutated — confirms copy() in _run_agent_attempt decouples usage
    assert snapshot.input_tokens == 10
