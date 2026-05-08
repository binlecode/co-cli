"""Tests for discover_delegation_tools — profile-based delegation tool discovery."""

from tests._settings import make_settings

from co_cli.agent.core import discover_delegation_tools
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.web.fetch import web_fetch
from co_cli.tools.web.search import web_search


def test_web_research_profile_returns_web_tools():
    """web_research profile must include web_search and web_fetch regardless of optional config."""
    config = make_settings(obsidian_vault_path=None, google_credentials_path=None)
    tools = discover_delegation_tools("web_research", config)
    assert web_search in tools
    assert web_fetch in tools


def test_web_research_profile_excludes_knowledge_tools():
    """web_research profile must not include knowledge tools."""
    config = make_settings(obsidian_vault_path=None, google_credentials_path=None)
    tools = discover_delegation_tools("web_research", config)
    assert memory_search not in tools


def test_knowledge_analyze_base_tools_no_optional_config():
    """knowledge_analyze profile returns memory_search when no optional integrations configured."""
    config = make_settings(obsidian_vault_path=None, google_credentials_path=None)
    tools = discover_delegation_tools("knowledge_analyze", config)
    assert memory_search in tools


def test_knowledge_analyze_excludes_web_tools():
    """knowledge_analyze profile must not include web tools."""
    config = make_settings(obsidian_vault_path=None, google_credentials_path=None)
    tools = discover_delegation_tools("knowledge_analyze", config)
    assert web_search not in tools
    assert web_fetch not in tools


def test_knowledge_analyze_includes_obsidian_when_configured(tmp_path):
    """Obsidian tools appear in knowledge_analyze when obsidian_vault_path is set."""
    from co_cli.tools.obsidian.tools import obsidian_list, obsidian_search

    vault = tmp_path / "vault"
    vault.mkdir()
    config = make_settings(obsidian_vault_path=str(vault), google_credentials_path=None)
    tools = discover_delegation_tools("knowledge_analyze", config)
    assert obsidian_search in tools
    assert obsidian_list in tools


def test_knowledge_analyze_excludes_obsidian_when_not_configured():
    """Obsidian tools are absent from knowledge_analyze when obsidian_vault_path is unset."""
    from co_cli.tools.obsidian.tools import obsidian_list, obsidian_search

    config = make_settings(obsidian_vault_path=None, google_credentials_path=None)
    tools = discover_delegation_tools("knowledge_analyze", config)
    assert obsidian_search not in tools
    assert obsidian_list not in tools


def test_unknown_profile_returns_empty():
    """An unrecognized profile name returns an empty list."""
    config = make_settings()
    tools = discover_delegation_tools("nonexistent_profile", config)
    assert tools == []


def test_registry_is_populated_without_explicit_tool_imports(tmp_path):
    """discover_delegation_tools must trigger full registry population.

    Regression guard: the test file does NOT import google_drive_search anywhere,
    yet it must appear in the knowledge_analyze profile when google_credentials_path
    is set. discover_delegation_tools is responsible for ensuring all tool modules
    are loaded so the registry is complete regardless of caller import history.
    """
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    config = make_settings(google_credentials_path=str(creds))
    tools = discover_delegation_tools("knowledge_analyze", config)
    tool_names = [fn.__name__ for fn in tools]
    assert "google_drive_search" in tool_names
