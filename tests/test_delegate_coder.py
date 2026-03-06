"""Functional tests for the delegate_coder, delegate_research, and delegate_analysis tool wiring."""

import os
import pytest
from dataclasses import dataclass, field

from pydantic import ValidationError
from pydantic_ai.usage import RunUsage

from co_cli.agents.analysis import AnalysisResult, make_analysis_agent
from co_cli.agents.coder import CoderResult, make_coder_agent
from co_cli.agents.research import ResearchResult, make_research_agent
from co_cli.tools.delegation import delegate_analysis, delegate_coder, delegate_research


@dataclass
class FakeDeps:
    model_roles: dict = field(default_factory=dict)
    llm_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    turn_usage: RunUsage | None = None


class FakeCtx:
    def __init__(self, model_roles: dict | None = None) -> None:
        self.deps = FakeDeps(model_roles=model_roles or {})


@pytest.mark.asyncio
async def test_delegate_coder_no_model() -> None:
    """Returns error dict when model_roles.coding is not set."""
    ctx = FakeCtx(model_roles={})
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
    agent = make_coder_agent("gemini-2.0-flash", "gemini", "")
    assert agent is not None


def test_make_subagent_deps_resets_session_state() -> None:
    """make_subagent_deps resets 7 mutable session fields; scalar config is preserved."""
    from co_cli.deps import CoDeps, make_subagent_deps
    from co_cli.shell_backend import ShellBackend

    dirty = CoDeps(
        shell=ShellBackend(),
        session_id="parent-session",
        brave_search_api_key="test-key",
        auto_approved_tools={"run_shell_command", "write_file"},
        active_skill_env={"MY_VAR": "value"},
        active_skill_allowed_tools={"web_search"},
        drive_page_tokens={"folder": ["tok1"]},
        session_todos=[{"task": "do something"}],
        skill_registry=[{"name": "my-skill"}],
        precomputed_compaction="some-cached-summary",
        memory_max_count=500,
    )

    isolated = make_subagent_deps(dirty)

    # Mutable session state must be reset to clean defaults
    assert isolated.auto_approved_tools == set()
    assert isolated.active_skill_env == {}
    assert isolated.active_skill_allowed_tools == set()
    assert isolated.drive_page_tokens == {}
    assert isolated.session_todos == []
    assert isolated.skill_registry == []
    assert isolated.precomputed_compaction is None

    # Mutable session state: turn_usage reset to None
    assert isolated.turn_usage is None

    # Scalar config must be inherited from parent
    assert isolated.brave_search_api_key == "test-key"
    assert isolated.memory_max_count == 500
    assert isolated.session_id == "parent-session"


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
    agent = make_research_agent("gemini-2.0-flash", "gemini", "")
    assert agent is not None


@pytest.mark.asyncio
async def test_delegate_research_no_model() -> None:
    """Returns error dict when model_roles.research is not set."""
    ctx = FakeCtx(model_roles={})
    result = await delegate_research(ctx, "latest Python news")
    assert result.get("error") is True
    assert "not configured" in result["display"]


@pytest.mark.asyncio
async def test_delegate_research_max_requests_guard() -> None:
    """max_requests < 1 raises ModelRetry."""
    from pydantic_ai import ModelRetry as _ModelRetry

    ctx = FakeCtx(model_roles={"coding": ["some-model"]})
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
    agent = make_analysis_agent("gemini-2.0-flash", "gemini", "")
    assert agent is not None


@pytest.mark.asyncio
async def test_delegate_analysis_no_model() -> None:
    """Returns error dict when model_roles.analysis is not set."""
    ctx = FakeCtx(model_roles={})
    result = await delegate_analysis(ctx, "compare these documents")
    assert result.get("error") is True
    assert "not configured" in result["display"]


def test_confidence_out_of_range_fails_validation() -> None:
    """Out-of-range confidence values are rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        ResearchResult(summary="ok", sources=[], confidence=1.5)
    with pytest.raises(ValidationError):
        CoderResult(summary="ok", diff_preview="", files_touched=[], confidence=-0.1)


@pytest.mark.skipif(not os.getenv("LLM_PROVIDER"), reason="requires LLM_PROVIDER env var")
@pytest.mark.asyncio
async def test_delegate_research_budget_no_overflow() -> None:
    """delegate_research never consumes more requests than max_requests."""
    from co_cli.config import get_settings
    from co_cli.deps import CoDeps
    from co_cli.shell_backend import ShellBackend

    _settings = get_settings()
    if not _settings.model_roles.get("research"):
        pytest.skip("model_roles.research not configured")

    class RealCtx:
        def __init__(self, deps: CoDeps) -> None:
            self.deps = deps

    deps = CoDeps(
        shell=ShellBackend(),
        llm_provider=_settings.llm_provider,
        model_roles=_settings.model_roles,
        brave_search_api_key=_settings.brave_search_api_key,
        ollama_host=_settings.ollama_host,
    )
    ctx = RealCtx(deps)
    await delegate_research(ctx, "What is pydantic-ai?", max_requests=1)
    assert ctx.deps.turn_usage is not None
    assert ctx.deps.turn_usage.requests <= 1
