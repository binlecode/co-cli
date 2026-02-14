"""Functional tests for MCP client integration (stdio transport).

Tests cover agent wiring, config loading, and E2E server lifecycle.
"""

import json
import os

import pytest

from co_cli.config import MCPServerConfig, Settings, load_config
from co_cli.agent import get_agent
from co_cli.status import get_status


# -- Settings integration tests ------------------------------------------------


def test_settings_defaults_have_mcp():
    """Settings ships with default MCP servers."""
    s = Settings()
    assert len(s.mcp_servers) > 0
    assert "github" in s.mcp_servers
    assert "thinking" in s.mcp_servers
    assert "context7" in s.mcp_servers


# -- Agent wiring tests -------------------------------------------------------


def test_github_token_resolved_lazily(monkeypatch):
    """GitHub token is resolved at agent creation, not at config import time."""
    monkeypatch.setenv("GITHUB_TOKEN_BINLECODE", "ghp_test123")
    agent, _, _ = get_agent(mcp_servers={
        "github": MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
        ),
    })
    mcp = agent.toolsets[1].wrapped
    assert mcp.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_test123"


def test_github_token_absent_no_env(monkeypatch):
    """GitHub server gets no token env when GITHUB_TOKEN_BINLECODE is unset."""
    monkeypatch.delenv("GITHUB_TOKEN_BINLECODE", raising=False)
    agent, _, _ = get_agent(mcp_servers={
        "github": MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
        ),
    })
    mcp = agent.toolsets[1].wrapped
    assert mcp.env is None or "GITHUB_PERSONAL_ACCESS_TOKEN" not in (mcp.env or {})


def test_env_var_override(tmp_path, monkeypatch):
    """CO_CLI_MCP_SERVERS env var parses JSON correctly."""
    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CO_CLI_MCP_SERVERS", json.dumps({
        "test": {"command": "echo", "args": ["hello"]},
    }))

    s = load_config()
    assert "test" in s.mcp_servers
    assert s.mcp_servers["test"].command == "echo"
    assert s.mcp_servers["test"].args == ["hello"]


def test_mcp_from_settings_file(tmp_path, monkeypatch):
    """MCP servers load from settings.json."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "mcp_servers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "approval": "never",
            }
        }
    }))
    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", settings_file)
    monkeypatch.chdir(tmp_path)

    s = load_config()
    assert "filesystem" in s.mcp_servers
    assert s.mcp_servers["filesystem"].approval == "never"


def test_agent_no_mcp():
    """get_agent() works without mcp_servers passed explicitly."""
    agent, model_settings, tool_names = get_agent(mcp_servers={})
    assert len(tool_names) > 0
    mcp_count = sum(
        1 for t in agent.toolsets
        if type(t).__name__ != "_AgentFunctionToolset"
    )
    assert mcp_count == 0


def test_agent_with_mcp():
    """get_agent() creates agent with MCP toolsets."""
    mcp = {
        "filesystem": MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ),
    }
    agent, _, tool_names = get_agent(mcp_servers=mcp)

    mcp_count = sum(
        1 for t in agent.toolsets
        if type(t).__name__ != "_AgentFunctionToolset"
    )
    assert mcp_count == 1


def test_tool_prefixing():
    """Tools from server named 'fs' get prefix 'fs' via tool_prefix."""
    from pydantic_ai.mcp import MCPServerStdio

    mcp = {
        "fs": MCPServerConfig(command="echo"),
    }
    agent, _, _ = get_agent(mcp_servers=mcp)

    for toolset in agent.toolsets:
        inner = getattr(toolset, "wrapped", toolset)
        if isinstance(inner, MCPServerStdio):
            assert inner.tool_prefix == "fs"
            break
    else:
        pytest.fail("No MCPServerStdio found in agent toolsets")


def test_custom_prefix_used():
    """Custom prefix overrides server name."""
    from pydantic_ai.mcp import MCPServerStdio

    mcp = {
        "fs": MCPServerConfig(command="echo", prefix="myprefix"),
    }
    agent, _, _ = get_agent(mcp_servers=mcp)

    for toolset in agent.toolsets:
        inner = getattr(toolset, "wrapped", toolset)
        if isinstance(inner, MCPServerStdio):
            assert inner.tool_prefix == "myprefix"
            break
    else:
        pytest.fail("No MCPServerStdio found in agent toolsets")


def test_approval_auto_wraps():
    """Server with approval='auto' wraps with ApprovalRequiredToolset."""
    mcp = {
        "test": MCPServerConfig(command="echo", approval="auto"),
    }
    agent, _, _ = get_agent(mcp_servers=mcp)

    for toolset in agent.toolsets:
        if type(toolset).__name__ == "_AgentFunctionToolset":
            continue
        assert type(toolset).__name__ == "ApprovalRequiredToolset"
        break
    else:
        pytest.fail("No MCP toolset found")


def test_approval_never_no_wrap():
    """Server with approval='never' is not wrapped with approval."""
    from pydantic_ai.mcp import MCPServerStdio

    mcp = {
        "test": MCPServerConfig(command="echo", approval="never"),
    }
    agent, _, _ = get_agent(mcp_servers=mcp)

    for toolset in agent.toolsets:
        if type(toolset).__name__ == "_AgentFunctionToolset":
            continue
        assert isinstance(toolset, MCPServerStdio)
        break
    else:
        pytest.fail("No MCP toolset found")


def test_multiple_mcp_servers():
    """Multiple MCP servers create multiple toolsets."""
    mcp = {
        "fs": MCPServerConfig(command="echo"),
        "db": MCPServerConfig(command="echo", approval="never"),
    }
    agent, _, _ = get_agent(mcp_servers=mcp)

    mcp_count = sum(
        1 for t in agent.toolsets
        if type(t).__name__ != "_AgentFunctionToolset"
    )
    assert mcp_count == 2


# -- Status tests -------------------------------------------------------------


def test_status_default_mcp():
    """Status shows default MCP servers."""
    info = get_status()
    assert len(info.mcp_servers) == 3
    names = [name for name, _ in info.mcp_servers]
    assert "github" in names


def test_status_with_mcp(tmp_path, monkeypatch):
    """Status shows configured MCP servers."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "mcp_servers": {
            "filesystem": {"command": "npx"},
            "database": {"command": "python"},
        }
    }))
    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", settings_file)
    monkeypatch.chdir(tmp_path)

    from co_cli.config import load_config as _load_config
    new_settings = _load_config()
    monkeypatch.setattr("co_cli.status.settings", new_settings)

    info = get_status()
    assert len(info.mcp_servers) == 2
    names = [name for name, _ in info.mcp_servers]
    assert "filesystem" in names
    assert "database" in names
    assert any(status == "ready" for _, status in info.mcp_servers)


# -- E2E functional tests (real MCP servers via npx) ---------------------------


@pytest.mark.asyncio
async def test_e2e_thinking_server():
    """Start sequential-thinking MCP server, discover tools, make a tool call."""
    from pydantic_ai.mcp import MCPServerStdio

    server = MCPServerStdio(
        "npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        tool_prefix="thinking",
        timeout=30,
    )
    async with server:
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "sequentialthinking" in tool_names

        result = await server.direct_call_tool(
            "sequentialthinking",
            {
                "thought": "What is 2+2?",
                "nextThoughtNeeded": False,
                "thoughtNumber": 1,
                "totalThoughts": 1,
            },
        )
        assert result is not None


@pytest.mark.asyncio
async def test_e2e_context7_server():
    """Start context7 MCP server, discover tools, make a tool call."""
    from pydantic_ai.mcp import MCPServerStdio

    server = MCPServerStdio(
        "npx",
        args=["-y", "@upstash/context7-mcp@latest"],
        tool_prefix="context7",
        timeout=30,
    )
    async with server:
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "resolve-library-id" in tool_names
        assert "query-docs" in tool_names

        result = await server.direct_call_tool(
            "resolve-library-id",
            {
                "libraryName": "pydantic",
                "query": "data validation",
            },
        )
        assert result is not None


@pytest.mark.asyncio
async def test_e2e_github_server():
    """Start GitHub MCP server, discover tools, make a tool call."""
    from pydantic_ai.mcp import MCPServerStdio

    token = os.environ["GITHUB_TOKEN_BINLECODE"]
    server = MCPServerStdio(
        "npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": token},
        tool_prefix="github",
        timeout=30,
    )
    async with server:
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "search_repositories" in tool_names
        assert "get_file_contents" in tool_names
        assert "list_issues" in tool_names

        result = await server.direct_call_tool(
            "search_repositories",
            {"query": "modelcontextprotocol/servers"},
        )
        assert result is not None


@pytest.mark.asyncio
async def test_e2e_all_defaults_via_agent():
    """Start all 3 default MCP servers through the agent lifecycle."""
    from co_cli.config import _DEFAULT_MCP_SERVERS

    agent, _, tool_names = get_agent(mcp_servers=_DEFAULT_MCP_SERVERS.copy())

    async with agent:
        from co_cli.main import _discover_mcp_tools

        all_tool_names = await _discover_mcp_tools(agent, tool_names)

        assert "run_shell_command" in all_tool_names
        assert "thinking_sequentialthinking" in all_tool_names
        assert "context7_resolve-library-id" in all_tool_names
        assert "github_search_repositories" in all_tool_names
        assert len(all_tool_names) > len(tool_names)
