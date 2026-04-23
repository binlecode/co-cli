"""Functional tests for capabilities_check tool."""

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS

from co_cli.agent._core import build_agent, build_tool_registry
from co_cli.bootstrap.check import check_runtime
from co_cli.config._core import MCPServerSettings, settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._core import TerminalFrontend
from co_cli.tools.capabilities import capabilities_check
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_deps(**settings_overrides) -> CoDeps:
    """Build runtime deps for doctor-style checks without test doubles."""
    config = make_settings(**settings_overrides)
    tool_registry = build_tool_registry(config)
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# ---------------------------------------------------------------------------
# Doctor tool workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_runtime_fields_present() -> None:
    deps = _make_deps()
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    assert result.metadata["tool_count"] == len(deps.tool_index)
    assert result.metadata["native_tool_count"] + result.metadata["mcp_tool_count"] == len(
        deps.tool_index
    )
    assert result.metadata["mcp_mode"] in ("mcp", "native-only")


@pytest.mark.asyncio
async def test_capabilities_emits_doctor_progress_updates() -> None:
    statuses: list[str] = []
    deps = _make_deps()
    deps.runtime.tool_progress_callback = statuses.append
    ctx = _make_ctx(deps)

    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        await capabilities_check(ctx)

    assert statuses[0] == "Doctor: starting runtime diagnostics..."
    assert "Doctor: checking provider and model availability..." in statuses
    assert "Doctor: checking configured integrations..." in statuses
    assert "Doctor: checking knowledge backend..." in statuses
    assert "Doctor: checking loaded skills..." in statuses


@pytest.mark.asyncio
async def test_capabilities_progress_routes_to_frontend_via_curried_lambda() -> None:
    """Progress callback wired as curried lambda routes to the real terminal frontend.

    Validates the join between the tool's tool_progress_callback usage and the frontend
    protocol using the same curried lambda pattern _execute_stream_segment() applies at
    FunctionToolCallEvent time. This uses the real TerminalFrontend instead of a
    recording fake and asserts only on the frontend's public inspection API.
    """
    frontend = TerminalFrontend()
    tool_id = "cap1"
    deps = _make_deps(mcp_servers={})
    deps.runtime.tool_progress_callback = lambda msg, _tid=tool_id: frontend.on_tool_progress(
        _tid, msg
    )
    ctx = _make_ctx(deps)

    try:
        async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
            result = await capabilities_check(ctx)
        assert frontend.active_surface() == "tool"
        assert frontend.active_tool_messages(), "Expected tool progress to be rendered"
        assert frontend.active_tool_messages()[0] == "Doctor: checking loaded skills..."
        assert result.return_value, (
            "return_value missing or empty in capabilities_check ToolReturn"
        )
    finally:
        frontend.cleanup()


# ---------------------------------------------------------------------------
# Runtime health probing dependencies
# ---------------------------------------------------------------------------


def test_check_runtime_mcp_probe_name_matches_config_key() -> None:
    """Runtime checks must preserve the config key for each MCP probe result."""
    result = check_runtime(
        _make_deps(
            mcp_servers={"mysvr": MCPServerSettings(command="ls")},
        )
    )

    assert len(result.mcp_probes) == 1
    assert result.mcp_probes[0][0] == "mysvr"


def test_check_runtime_binary_probe_passes_when_command_on_path() -> None:
    """Healthy MCP commands on PATH should not emit a degraded finding."""
    result = check_runtime(
        _make_deps(
            mcp_servers={"mysvr": MCPServerSettings(command="ls")},
        )
    )

    assert not any(finding["component"] == "mcp:mysvr" for finding in result.findings)


# ---------------------------------------------------------------------------
# Self-check contract — grouped display, degradations, MCP wording, enum counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_display_contains_self_check_sections() -> None:
    """The model-visible display must expose the grouped self-check contract.

    Without these sections the model cannot answer "what can you do right now?"
    from the tool result alone and has to fall back to metadata or guesswork.
    """
    deps = _make_deps()
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    display = result.return_value
    assert "Available now:" in display
    assert "Discoverable on demand:" in display
    assert "Approval-gated:" in display
    assert "Unavailable or limited:" in display
    assert "Active fallbacks:" in display


@pytest.mark.asyncio
async def test_capabilities_surfaces_deps_degradations() -> None:
    """Bootstrap-recorded degradations must surface in display and metadata.

    Regression: old code hardcoded a single 'mcp: native-only' fallback string
    and ignored deps.degradations, so knowledge/MCP fallbacks never reached
    the model's self-check result.
    """
    deps = _make_deps()
    deps.degradations["knowledge"] = "sqlite-fts → grep (embedder unavailable)"
    deps.degradations["mcp.notes"] = "binary missing"
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    display = result.return_value
    assert "knowledge: sqlite-fts → grep (embedder unavailable)" in display
    assert "mcp.notes: tool discovery failed — binary missing" in display
    assert result.metadata["degradations"] == {
        "knowledge": "sqlite-fts → grep (embedder unavailable)",
        "mcp.notes": "binary missing",
    }
    fallbacks = result.metadata["fallbacks"]
    assert "knowledge: sqlite-fts → grep (embedder unavailable)" in fallbacks
    assert "mcp.notes: tool discovery failed — binary missing" in fallbacks


@pytest.mark.asyncio
async def test_capabilities_mcp_wording_is_evidence_based_not_connected() -> None:
    """MCP block must use evidence-based wording (command found / url configured / probe failed).

    A PATH probe only proves the command exists — calling that state 'connected'
    overstates reality and misleads the model into false confidence.
    """
    deps = _make_deps(mcp_servers={"mysvr": MCPServerSettings(command="ls")})
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    display = result.return_value
    assert "connected" not in display.lower()
    assert "command found" in display


@pytest.mark.asyncio
async def test_capabilities_source_counts_match_real_registry() -> None:
    """Tool counts must reflect the real registry attached to the running deps."""
    deps = _make_deps(mcp_servers={"mysvr": MCPServerSettings(command="ls")})
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    source_counts = result.metadata["source_counts"]
    assert result.metadata["native_tool_count"] == source_counts.get("native", 0)
    assert result.metadata["mcp_tool_count"] == source_counts.get("mcp", 0)
    assert result.metadata["tool_count"] == sum(source_counts.values())


def test_check_runtime_reasoning_ready_false_when_provider_probe_fails() -> None:
    """reasoning_ready must follow provider probe health, not just llm.model truthiness.

    Regression: old code was `bool(deps.config.llm.model)` and returned True
    whenever a model name was configured, even when the provider was unreachable
    or the API key was missing.
    """
    base = make_settings()
    unhealthy_llm = base.llm.model_copy(update={"provider": "gemini", "api_key": None})
    broken = base.model_copy(update={"llm": unhealthy_llm})
    deps = CoDeps(shell=ShellBackend(), config=broken, session=CoSessionState())
    result = check_runtime(deps)
    assert deps.config.llm.model, "precondition: llm.model string is set"
    assert result.capabilities["provider"]["ok"] is False
    assert result.capabilities["reasoning_ready"] is False
