"""Functional tests for the delegate_coder, delegate_research, and delegate_analysis tool wiring."""

import pytest
from pathlib import Path

from pydantic import ValidationError
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli._model_factory import ResolvedModel
from co_cli.tools._delegation_agents import AnalysisResult, make_analysis_agent, CoderResult, make_coder_agent, ResearchResult, make_research_agent, ThinkingResult, make_thinking_agent
from co_cli.config import ModelEntry, settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.delegation import delegate_analysis, delegate_coder, delegate_research, delegate_think

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


def _make_ctx() -> RunContext:
    """Return a real RunContext with no model_registry — triggers unavailable guard."""
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=None),
        config=CoConfig(),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_delegate_coder_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await delegate_coder(ctx, "analyze foo")


def test_coder_result_model() -> None:
    """CoderResult is a valid Pydantic model with expected fields."""
    r = CoderResult(
        summary="test summary",
        diff_preview="",
        files_touched=["foo.py"],
        confidence=0.8,
    )
    assert r.summary == "test summary"
    assert r.confidence == 0.8


def test_make_coder_agent_registers_file_tools() -> None:
    """make_coder_agent should register 3 read-only file tools without raising."""
    agent = make_coder_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None


def test_make_subagent_deps_resets_session_state() -> None:
    """make_subagent_deps() resets session/runtime groups; shares services/config."""
    from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState, CoRuntimeState, make_subagent_deps
    from co_cli.tools._shell_backend import ShellBackend

    dirty = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            brave_search_api_key="test-key",
            memory_max_count=500,
        ),
        session=CoSessionState(
            session_id="parent-session",
            session_tool_approvals={"run_shell_command", "write_file"},
            active_skill_env={"MY_VAR": "value"},
            skill_tool_grants={"web_search"},
            drive_page_tokens={"folder": ["tok1"]},
            session_todos=[{"task": "do something"}],
            skill_registry=[{"name": "my-skill"}],
        ),
        runtime=CoRuntimeState(
            precomputed_compaction="some-cached-summary",
        ),
    )

    isolated = make_subagent_deps(dirty)

    # Session resets to clean defaults
    assert isolated.session.session_tool_approvals == set()
    assert isolated.session.active_skill_env == {}
    assert isolated.session.skill_tool_grants == set()
    assert isolated.session.drive_page_tokens == {}
    assert isolated.session.session_todos == []
    assert isolated.session.skill_registry == []

    # Runtime resets to clean defaults
    assert isolated.runtime.precomputed_compaction is None
    assert isolated.runtime.turn_usage is None

    # Config inherited from parent
    assert isolated.config.brave_search_api_key == "test-key"
    assert isolated.config.memory_max_count == 500
    # sub-agents get fresh CoSessionState — session_id is not inherited (correct isolation behavior)
    assert isolated.session.session_id == ""

    # Services shared (same object)
    assert isolated.services is dirty.services


def test_research_result_model() -> None:
    """ResearchResult is a valid Pydantic model with expected fields."""
    r = ResearchResult(
        summary="Python 3.12 ships with the new GIL opt-out feature.",
        sources=["https://docs.python.org/3.12/"],
        confidence=0.9,
    )
    assert r.summary
    assert len(r.sources) == 1
    assert r.confidence == 0.9


def test_make_research_agent_registers_web_tools() -> None:
    """make_research_agent registers web_search and web_fetch without raising."""
    agent = make_research_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None


@pytest.mark.asyncio
async def test_delegate_research_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await delegate_research(ctx, "latest Python news")


@pytest.mark.asyncio
async def test_delegate_research_max_requests_guard() -> None:
    """max_requests < 1 raises ModelRetry (checked before registry lookup)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="max_requests must be at least 1"):
        await delegate_research(ctx, "any query", max_requests=0)


def test_analysis_result_model() -> None:
    """AnalysisResult is a valid Pydantic model with expected fields."""
    r = AnalysisResult(
        conclusion="Python 3.12 is more performant than 3.11.",
        evidence=["Benchmark shows 15% speedup.", "PEP 703 reduces GIL contention."],
        reasoning="Two independent benchmarks converge on the same improvement range.",
    )
    assert r.conclusion
    assert len(r.evidence) == 2
    assert r.reasoning


def test_make_analysis_agent_returns_agent() -> None:
    """make_analysis_agent returns a non-None agent without raising."""
    agent = make_analysis_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None


@pytest.mark.asyncio
async def test_delegate_analysis_no_model() -> None:
    """Raises ModelRetry when model_registry is None (no registry configured)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await delegate_analysis(ctx, "compare these documents")


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


def test_make_thinking_agent_no_tools() -> None:
    """make_thinking_agent returns a non-None agent with no registered function tools.

    Note: agent._function_toolset.tools is a pydantic-ai private attribute (dict of
    user-registered function tools). Accessing it here is intentional — it is the
    only way to assert the no-tools invariant without a live LLM call. If pydantic-ai
    renames this attribute, update this test.
    """
    agent = make_thinking_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
    assert agent is not None
    assert len(agent._function_toolset.tools) == 0


@pytest.mark.asyncio
async def test_delegate_think_no_model() -> None:
    """Raises ModelRetry matching 'unavailable' when model_registry is None."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="unavailable"):
        await delegate_think(ctx, "solve this problem")


@pytest.mark.asyncio
async def test_delegate_think_max_requests_guard() -> None:
    """max_requests=0 raises ModelRetry matching 'max_requests' (checked before registry lookup)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="max_requests"):
        await delegate_think(ctx, "any problem", max_requests=0)


@pytest.mark.asyncio
async def test_delegate_coder_max_requests_guard() -> None:
    """max_requests=0 raises ModelRetry matching 'max_requests' (parity with delegate_research/analysis)."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = _make_ctx()
    with pytest.raises(_ModelRetry, match="max_requests"):
        await delegate_coder(ctx, "analyze foo", max_requests=0)
