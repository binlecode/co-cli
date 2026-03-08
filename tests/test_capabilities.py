"""Functional tests for check_capabilities tool."""
import asyncio

from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import get_agent
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli._shell_backend import ShellBackend
from co_cli.tools.capabilities import check_capabilities

_AGENT, _, _, _ = get_agent()


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
