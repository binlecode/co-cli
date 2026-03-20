"""Functional tests for check_capabilities tool."""
import asyncio
from pathlib import Path

from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.capabilities import check_capabilities

_AGENT, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


def test_skill_grants_field() -> None:
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
        session=CoSessionState(skill_tool_grants={"run_shell_command"}),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    result = asyncio.run(check_capabilities(ctx))
    assert result["skill_grants"] == ["run_shell_command"]
    assert "Active skill grants" in result["display"]


def test_no_skill_grants_field_when_empty() -> None:
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    result = asyncio.run(check_capabilities(ctx))
    assert result["skill_grants"] == []
    assert "Active skill grants" not in result["display"]


def test_new_runtime_fields_present() -> None:
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    result = asyncio.run(check_capabilities(ctx))
    assert "tool_count" in result
    assert "mcp_mode" in result
    assert result["mcp_mode"] in ("mcp", "native-only")
    assert isinstance(result["tool_count"], int)


def test_capabilities_emits_doctor_progress_updates() -> None:
    statuses: list[str] = []
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )
    deps.runtime.status_callback = statuses.append
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    asyncio.run(check_capabilities(ctx))

    assert statuses[0] == "Doctor: starting runtime diagnostics..."
    assert "Doctor: checking provider and model availability..." in statuses
    assert "Doctor: checking configured integrations..." in statuses
    assert "Doctor: checking knowledge backend..." in statuses
    assert "Doctor: checking loaded skills..." in statuses


def test_capabilities_routes_progress_into_frontend_status_sink() -> None:
    events: list[tuple[str, str]] = []

    def _on_status(message: str) -> None:
        events.append(("status", message))

    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )
    deps.runtime.status_callback = _on_status
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    asyncio.run(check_capabilities(ctx))

    status_messages = [message for kind, message in events if kind == "status"]
    assert status_messages[0] == "Doctor: starting runtime diagnostics..."
    assert any(message == "Doctor: checking provider and model availability..." for message in status_messages)
    assert any(message == "Doctor: checking configured integrations..." for message in status_messages)
