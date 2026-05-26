"""Tests for semantic_marker on-demand suffix policy.

Pins which tool families get the recovery hint and which do not:
- Content-fetch reads (file_read, web_fetch, obsidian_read) → `read on demand` / `fetch on demand`
- Populated searches (file_search, file_find, web_search) → `re-query on demand`
- Empty-result branches → no suffix (terminal info)
- Side-effect tools (shell_exec) → no suffix (re-run is wrong)
- Generic fallback → no suffix (unknown semantics)
"""

from co_cli.context._tool_result_markers import semantic_marker


def test_file_read_marker_has_read_on_demand_suffix() -> None:
    marker = semantic_marker("file_read", {"path": "/a.py"}, "x" * 100)
    assert marker.endswith(" — read on demand")
    assert marker.startswith("[file_read] /a.py")


def test_web_fetch_marker_has_fetch_on_demand_suffix() -> None:
    marker = semantic_marker("web_fetch", {"url": "https://example.com"}, "x" * 100)
    assert marker.endswith(" — fetch on demand")


def test_obsidian_read_marker_has_read_on_demand_suffix() -> None:
    marker = semantic_marker("obsidian_read", {"filename": "note.md"}, "x" * 100)
    assert marker.endswith(" — read on demand")


def test_file_search_populated_has_re_query_suffix() -> None:
    marker = semantic_marker("file_search", {"pattern": "auth", "path": "."}, "hit1\nhit2\n")
    assert marker.endswith(" — re-query on demand")


def test_file_search_no_matches_has_no_suffix() -> None:
    marker = semantic_marker("file_search", {"pattern": "auth", "path": "."}, "(no matches)")
    assert "on demand" not in marker
    assert marker.endswith("no matches")


def test_file_find_populated_has_re_query_suffix() -> None:
    marker = semantic_marker("file_find", {"pattern": "*.py", "path": "."}, "a.py\nb.py\n")
    assert marker.endswith(" — re-query on demand")


def test_file_find_no_entries_has_no_suffix() -> None:
    marker = semantic_marker("file_find", {"pattern": "*.py", "path": "."}, "(empty)")
    assert "on demand" not in marker


def test_web_search_populated_has_re_query_suffix() -> None:
    marker = semantic_marker("web_search", {"query": "rust"}, "result body " * 20)
    assert marker.endswith(" — re-query on demand")


def test_web_search_no_results_has_no_suffix() -> None:
    marker = semantic_marker("web_search", {"query": "rust"}, "No results found")
    assert "on demand" not in marker


def test_shell_exec_marker_has_no_on_demand_suffix() -> None:
    marker = semantic_marker("shell_exec", {"cmd": "ls"}, "exit 0:\nfoo\nbar\n")
    assert "on demand" not in marker


def test_generic_fallback_marker_has_no_on_demand_suffix() -> None:
    marker = semantic_marker("custom_tool", {"key": "val"}, "x" * 100)
    assert "on demand" not in marker
    assert marker.startswith("[custom_tool]")
