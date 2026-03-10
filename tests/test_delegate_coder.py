"""Functional tests for the delegate_coder, delegate_research, and delegate_analysis tool wiring."""

import pytest
from dataclasses import dataclass, field

from pydantic import ValidationError
from pydantic_ai.usage import RunUsage

from co_cli.agents._factory import ResolvedModel
from co_cli.agents.analysis import AnalysisResult, make_analysis_agent
from co_cli.agents.coder import CoderResult, make_coder_agent
from co_cli.agents.research import ResearchResult, make_research_agent
from co_cli.config import ModelEntry
from co_cli.tools.delegation import delegate_analysis, delegate_coder, delegate_research


@dataclass
class FakeConfig:
    role_models: dict = field(default_factory=dict)
    llm_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"


@dataclass
class FakeRuntime:
    turn_usage: RunUsage | None = None


@dataclass
class FakeServices:
    model_registry: object | None = None


@dataclass
class FakeDeps:
    config: FakeConfig = field(default_factory=FakeConfig)
    runtime: FakeRuntime = field(default_factory=FakeRuntime)
    services: FakeServices = field(default_factory=FakeServices)


class FakeCtx:
    def __init__(self, role_models: dict | None = None) -> None:
        self.deps = FakeDeps(config=FakeConfig(role_models=role_models or {}))


@pytest.mark.asyncio
async def test_delegate_coder_no_model() -> None:
    """Returns error dict when role_models.coding is not set."""
    ctx = FakeCtx(role_models={})
    result = await delegate_coder(ctx, "analyze foo")
    assert result.get("error") is True
    assert "not configured" in result["display"]


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
    from co_cli._shell_backend import ShellBackend

    dirty = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            session_id="parent-session",
            brave_search_api_key="test-key",
            memory_max_count=500,
        ),
        session=CoSessionState(
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
    assert isolated.config.session_id == "parent-session"

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
    """Returns error dict when role_models.research is not set."""
    ctx = FakeCtx(role_models={})
    result = await delegate_research(ctx, "latest Python news")
    assert result.get("error") is True
    assert "not configured" in result["display"]


@pytest.mark.asyncio
async def test_delegate_research_max_requests_guard() -> None:
    """max_requests < 1 raises ModelRetry."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = FakeCtx(role_models={"coding": [ModelEntry(model="some-model")]})
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
    """Returns error dict when role_models.analysis is not set."""
    ctx = FakeCtx(role_models={})
    result = await delegate_analysis(ctx, "compare these documents")
    assert result.get("error") is True
    assert "not configured" in result["display"]


def test_confidence_out_of_range_fails_validation() -> None:
    """Out-of-range confidence values are rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        ResearchResult(summary="ok", sources=[], confidence=1.5)
    with pytest.raises(ValidationError):
        CoderResult(summary="ok", diff_preview="", files_touched=[], confidence=-0.1)


