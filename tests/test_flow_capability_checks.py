"""Consolidated E2E tests for test_flow_capability_checks."""

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.config.core import settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.capabilities import capabilities_check
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_deps(**settings_overrides) -> CoDeps:
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


@pytest.mark.asyncio
async def test_capabilities_display_contains_self_check_sections() -> None:
    """The model-visible display must expose the grouped self-check contract."""
    deps = _make_deps()
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    display = result.return_value
    assert "Available now:" in display
    assert "Discoverable on demand:" in display
    assert "Approval-gated:" in display


@pytest.mark.asyncio
async def test_capabilities_surfaces_deps_degradations() -> None:
    """Bootstrap-recorded degradations must surface in display and metadata."""
    deps = _make_deps()
    deps.degradations["knowledge"] = "sqlite-fts → grep (embedder unavailable)"
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    display = result.return_value
    assert "knowledge: sqlite-fts → grep (embedder unavailable)" in display
    assert (
        result.metadata["degradations"]["knowledge"] == "sqlite-fts → grep (embedder unavailable)"
    )
