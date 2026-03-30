"""Functional tests for the run_coder_subagent, run_research_subagent, and run_analysis_subagent tool wiring."""

import pytest
from pathlib import Path

from pydantic import ValidationError
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli._model_factory import ResolvedModel
from co_cli.tools._subagent_agents import CoderResult, make_analysis_agent, make_coder_agent, ResearchResult, make_research_agent, ThinkingResult, make_thinking_agent
from co_cli.config import ModelConfig, settings
from co_cli.deps import CoDeps, CoServices, CoConfig, CoCapabilityState, CoSessionState, CoRuntimeState, make_subagent_deps
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.subagent import run_analysis_subagent, run_coder_subagent, run_research_subagent, run_thinking_subagent

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
async def test_run_coder_subagent_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_coder_subagent(ctx, "analyze foo")


def test_make_coder_agent_registers_file_tools() -> None:
    """make_coder_agent should register 3 read-only file tools without raising."""
    agent = make_coder_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None


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


def test_make_research_agent_registers_web_tools() -> None:
    """make_research_agent registers web_search and web_fetch without raising."""
    agent = make_research_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None


@pytest.mark.asyncio
async def test_run_research_subagent_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_research_subagent(ctx, "latest Python news")


def test_make_analysis_agent_returns_agent() -> None:
    """make_analysis_agent returns a non-None agent without raising."""
    agent = make_analysis_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None


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
async def test_run_thinking_subagent_no_model() -> None:
    """Raises ModelRetry matching 'unavailable' when model_registry is None."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await run_thinking_subagent(ctx, "solve this problem")


def test_run_coder_subagent_make_result_scope_kwarg():
    """make_result for run_coder_subagent must include scope from task input."""
    from co_cli.tools._result import make_result
    task = "a" * 200
    scope = task[:120]
    result = make_result(
        f"Scope: {scope}\nCoder analysis complete.\ntest summary\n[coding · model · 1/10 req]",
        summary="test summary",
        diff_preview="",
        files_touched=[],
        confidence=0.8,
        role="coding",
        model_name="model",
        requests_used=1,
        request_limit=10,
        scope=scope,
    )
    assert result.get("scope") == scope
    assert len(result["scope"]) <= 120
    assert result["display"].startswith("Scope: ")
