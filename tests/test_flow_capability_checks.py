"""Consolidated E2E tests for test_flow_capability_checks."""

import asyncio
from types import MappingProxyType

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS, make_settings
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.capabilities import capabilities_check


def _make_deps(**settings_overrides) -> CoDeps:
    config = make_settings(**settings_overrides) if settings_overrides else SETTINGS
    _, tool_catalog = build_native_toolset()
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_capabilities_surfaces_deps_degradations() -> None:
    """Bootstrap-recorded degradations must surface in display and metadata."""
    deps = _make_deps()
    deps.degradations = MappingProxyType({"knowledge": "hybrid → fts5 (embedder unavailable)"})
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await capabilities_check(ctx)
    display = result.return_value
    assert "knowledge: hybrid → fts5 (embedder unavailable)" in display
    assert result.metadata["degradations"]["knowledge"] == "hybrid → fts5 (embedder unavailable)"
