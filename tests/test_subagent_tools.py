"""Functional tests for the run_coding_subagent, run_research_subagent, and run_analysis_subagent tool wiring."""

import pytest
from copy import copy
from pathlib import Path

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.tools._subagent_agents import CoderResult, ResearchResult, ThinkingResult
from co_cli.config import settings, WebPolicy
from co_cli.deps import CoDeps, CoServices, CoConfig, CoCapabilityState, CoSessionState, CoRuntimeState, make_subagent_deps
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.subagent import run_analysis_subagent, run_coding_subagent, run_research_subagent, run_reasoning_subagent, _merge_turn_usage

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd())).agent


def _make_ctx() -> RunContext:
    """Return a real RunContext with no model_registry — triggers unavailable guard."""
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=None),
        config=CoConfig(),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_run_coding_subagent_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_coding_subagent(ctx, "analyze foo")


def test_make_subagent_deps_resets_session_state() -> None:
    """make_subagent_deps() shares tools by reference, inherits session fields, resets isolated fields."""
    from co_cli.commands._skill_types import SkillConfig
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    skill = SkillConfig(name="my-skill", body="do it")
    cap_state = CoCapabilityState(
        tool_names=["shell", "memory"],
        tool_approvals={"shell": True, "memory": False},
        skill_commands={"my-skill": skill},
        skill_registry=[{"name": "my-skill"}],
    )
    base = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            brave_search_api_key="test-key",
            memory_max_count=500,
        ),
        capabilities=cap_state,
        session=CoSessionState(
            session_id="parent-session",
            google_creds_resolved=True,
            session_approval_rules=[SessionApprovalRule(ApprovalKindEnum.SHELL, "git")],
            drive_page_tokens={"folder": ["tok1"]},
            session_todos=[{"task": "do something"}],
        ),
        runtime=CoRuntimeState(
            precomputed_compaction=None,
        ),
    )

    isolated = make_subagent_deps(base)

    # capabilities shared by reference (same capability registry)
    assert isolated.capabilities is base.capabilities

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
    assert isolated.runtime.precomputed_compaction is None
    assert isolated.runtime.turn_usage is None

    # Config inherited from parent
    assert isolated.config.brave_search_api_key == "test-key"
    assert isolated.config.memory_max_count == 500

    # Services shared (same object)
    assert isolated.services is base.services


@pytest.mark.asyncio
async def test_run_research_subagent_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_research_subagent(ctx, "latest Python news")


@pytest.mark.asyncio
async def test_run_analysis_subagent_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_analysis_subagent(ctx, "compare these documents")


def test_confidence_out_of_range_fails_validation() -> None:
    """Out-of-range confidence values are rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        ResearchResult(summary="ok", sources=[], confidence=1.5)
    with pytest.raises(ValidationError):
        CoderResult(summary="ok", diff_preview="", files_touched=[], confidence=-0.1)


def test_thinking_result_model() -> None:
    """ThinkingResult is a valid Pydantic model with expected field values."""
    r = ThinkingResult(
        plan="Decompose the problem into three phases.",
        steps=["Phase 1: gather context", "Phase 2: analyze", "Phase 3: synthesize"],
        conclusion="The recommended approach is X.",
    )
    assert r.plan == "Decompose the problem into three phases."
    assert len(r.steps) == 3
    assert r.conclusion == "The recommended approach is X."

@pytest.mark.asyncio
async def test_run_reasoning_subagent_no_model() -> None:
    """Raises ModelRetry matching 'unavailable' when model_registry is None."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_reasoning_subagent(ctx, "solve this problem")


@pytest.mark.asyncio
async def test_research_web_policy_gate_raises_model_retry() -> None:
    """web_policy gate fires before model_registry check — ModelRetry raised for non-allow policies."""
    from pydantic_ai import ModelRetry as _ModelRetry

    # search="ask" fires the gate even with fetch="allow"
    deps_search_ask = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=None),
        config=CoConfig(web_policy=WebPolicy(search="ask", fetch="allow")),
    )
    ctx_search_ask = RunContext(deps=deps_search_ask, model=_AGENT.model, usage=RunUsage())
    with pytest.raises(_ModelRetry, match="web_policy"):
        await run_research_subagent(ctx_search_ask, "latest Python news")

    # fetch="ask" fires the gate even with search="allow"
    deps_fetch_ask = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=None),
        config=CoConfig(web_policy=WebPolicy(search="allow", fetch="ask")),
    )
    ctx_fetch_ask = RunContext(deps=deps_fetch_ask, model=_AGENT.model, usage=RunUsage())
    with pytest.raises(_ModelRetry, match="web_policy"):
        await run_research_subagent(ctx_fetch_ask, "latest Python news")


def test_merge_turn_usage_alias_then_accumulate() -> None:
    """_merge_turn_usage aliases on first call (None) and accumulates on second call."""
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
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

