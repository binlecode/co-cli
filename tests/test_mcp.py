"""Functional tests for MCP client integration (stdio transport).

Tests cover config schema, agent wiring, and status display.
"""

import json

import pytest
from pydantic import ValidationError

from co_cli.config import MCPServerConfig, Settings, load_config
from co_cli.agent import get_agent
from co_cli.status import get_status


# -- Config schema tests -------------------------------------------------------


def test_valid_config():
    """MCPServerConfig loads with valid fields."""
    cfg = MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        timeout=30,
        env={"NODE_ENV": "production"},
    )
    assert cfg.command == "npx"
    assert cfg.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert cfg.timeout == 30
    assert cfg.env == {"NODE_ENV": "production"}


def test_invalid_timeout_too_high():
    """Timeout > 60 raises ValidationError."""
    with pytest.raises(ValidationError):
        MCPServerConfig(command="npx", timeout=120)


def test_invalid_timeout_too_low():
    """Timeout < 1 raises ValidationError."""
    with pytest.raises(ValidationError):
        MCPServerConfig(command="npx", timeout=0)


def test_default_approval():
    """Default approval is 'auto'."""
    cfg = MCPServerConfig(command="npx")
    assert cfg.approval == "auto"


def test_approval_never():
    """Approval can be set to 'never'."""
    cfg = MCPServerConfig(command="npx", approval="never")
    assert cfg.approval == "never"


def test_approval_invalid():
    """Invalid approval value raises ValidationError."""
    with pytest.raises(ValidationError):
        MCPServerConfig(command="npx", approval="always")


def test_custom_prefix():
    """Custom prefix field works."""
    cfg = MCPServerConfig(command="npx", prefix="mytools")
    assert cfg.prefix == "mytools"


def test_default_prefix_is_none():
    """Default prefix is None (server name used as prefix)."""
    cfg = MCPServerConfig(command="npx")
    assert cfg.prefix is None


def test_default_env_is_empty():
    """Default env is empty dict."""
    cfg = MCPServerConfig(command="npx")
    assert cfg.env == {}


def test_default_args_is_empty():
    """Default args is empty list."""
    cfg = MCPServerConfig(command="npx")
    assert cfg.args == []


# -- Settings integration tests ------------------------------------------------


def test_settings_defaults_have_mcp():
    """Settings ships with default MCP servers."""
    s = Settings()
    assert len(s.mcp_servers) > 0
    assert "github" in s.mcp_servers
    assert "thinking" in s.mcp_servers
    assert "context7" in s.mcp_servers


def test_settings_empty_mcp_override():
    """User can explicitly clear defaults with empty dict."""
    s = Settings(mcp_servers={})
    assert s.mcp_servers == {}


def test_settings_with_mcp():
    """Settings loads with mcp_servers configured."""
    s = Settings(mcp_servers={
        "filesystem": MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ),
    })
    assert "filesystem" in s.mcp_servers
    assert s.mcp_servers["filesystem"].command == "npx"


def test_settings_multiple_servers():
    """Settings supports multiple MCP servers."""
    s = Settings(mcp_servers={
        "fs": MCPServerConfig(command="npx", args=["server-fs"]),
        "db": MCPServerConfig(command="python", args=["-m", "db_server"]),
    })
    assert len(s.mcp_servers) == 2
    assert s.mcp_servers["fs"].command == "npx"
    assert s.mcp_servers["db"].command == "python"


def test_github_token_from_env(monkeypatch):
    """Default GitHub MCP server picks up GITHUB_TOKEN_BINLECODE env var."""
    monkeypatch.setenv("GITHUB_TOKEN_BINLECODE", "ghp_test123")
    from co_cli.config import _github_env
    env = _github_env()
    assert env == {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_test123"}


def test_github_token_absent(monkeypatch):
    """Default GitHub MCP server has empty env when GITHUB_TOKEN_BINLECODE not set."""
    monkeypatch.delenv("GITHUB_TOKEN_BINLECODE", raising=False)
    from co_cli.config import _github_env
    env = _github_env()
    assert env == {}


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


# -- Agent integration tests ---------------------------------------------------


def test_agent_no_mcp():
    """get_agent() works without mcp_servers passed explicitly."""
    agent, model_settings, tool_names = get_agent(mcp_servers={})
    assert len(tool_names) > 0
    # Should have no MCP toolsets — only the internal function toolset
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

    # Should have MCP toolset in addition to native function toolset
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

    # Find the MCPServerStdio in toolsets
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

    # The toolset should be wrapped (not raw MCPServerStdio)
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

    # The toolset should be raw MCPServerStdio (not wrapped)
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


# -- Status display tests -----------------------------------------------------


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

    # Reload settings with MCP config
    from co_cli.config import load_config
    new_settings = load_config()
    monkeypatch.setattr("co_cli.status.settings", new_settings)

    info = get_status()
    assert len(info.mcp_servers) == 2
    names = [name for name, _ in info.mcp_servers]
    assert "filesystem" in names
    assert "database" in names
    assert all(status == "configured" for _, status in info.mcp_servers)


def test_status_table_renders_mcp(tmp_path, monkeypatch):
    """Status table includes MCP row when servers configured."""
    from io import StringIO

    from rich.console import Console

    from co_cli.status import render_status_table, StatusInfo

    info = StatusInfo(
        version="0.1.0",
        git_branch="main",
        cwd="test",
        sandbox="Docker (full isolation)",
        llm_provider="Gemini (gemini-2.0-flash)",
        llm_status="configured",
        google="not found",
        google_detail="n/a",
        obsidian="not found",
        slack="not configured",
        web_search="not configured",
        mcp_servers=[("filesystem", "configured"), ("database", "configured")],
        tool_count=21,
        db_size="0 KB",
        project_config=None,
    )

    table = render_status_table(info)
    # Render to string using the project's themed console
    from co_cli.display import console as themed_console

    buf = StringIO()
    themed_console.file = buf
    themed_console.print(table)
    output = buf.getvalue()
    assert "MCP Servers" in output
    assert "2 configured" in output
    assert "filesystem" in output


# -- E2E functional tests (real MCP servers via npx) ---------------------------

import os
import asyncio


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
        # Discover tools
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "sequentialthinking" in tool_names

        # Make a real tool call
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
        # Discover tools
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "resolve-library-id" in tool_names
        assert "query-docs" in tool_names

        # Make a real tool call — resolve a library
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
        # Discover tools
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "search_repositories" in tool_names
        assert "get_file_contents" in tool_names
        assert "list_issues" in tool_names

        # Make a real tool call — search for a public repo
        result = await server.direct_call_tool(
            "search_repositories",
            {"query": "modelcontextprotocol/servers"},
        )
        assert result is not None


@pytest.mark.asyncio
async def test_e2e_all_defaults_via_agent():
    """Start all 3 default MCP servers through the agent lifecycle."""
    from co_cli.config import _DEFAULT_MCP_SERVERS, MCPServerConfig

    # Rebuild defaults with live GitHub token
    mcp_servers = {
        "github": MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_TOKEN_BINLECODE"]},
            approval="auto",
        ),
        "thinking": _DEFAULT_MCP_SERVERS["thinking"],
        "context7": _DEFAULT_MCP_SERVERS["context7"],
    }

    agent, _, tool_names = get_agent(mcp_servers=mcp_servers)

    async with agent:
        from co_cli.main import _discover_mcp_tools

        all_tool_names = await _discover_mcp_tools(agent, tool_names)

        # Native tools still present
        assert "run_shell_command" in all_tool_names

        # MCP tools discovered with correct prefixes
        assert "thinking_sequentialthinking" in all_tool_names
        assert "context7_resolve-library-id" in all_tool_names
        assert "github_search_repositories" in all_tool_names

        # Total tool count increased
        assert len(all_tool_names) > len(tool_names)
