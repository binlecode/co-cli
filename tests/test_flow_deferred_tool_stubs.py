"""Tests for the per-tool deferred-stub awareness prompt.

Guards the discovery signal for DEFERRED tools: every deferred tool is named, no
ALWAYS tool leaks in, one-liners stay single-line and length-capped, empty
descriptions fall back to a name-only stub, and the search_tools loader directive
is present. Built from a real bootstrap tool_index so a schema moving between
visibility buckets (or a new deferred tool being silently omitted) fails CI.
"""

import re

from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.deferred_prompt import (
    _ONE_LINER_MAX_CHARS,
    build_deferred_tool_awareness_prompt,
)


def _real_tool_index() -> dict[str, ToolInfo]:
    _, tool_index = build_native_toolset(SETTINGS)
    return tool_index


def _deferred_names(tool_index: dict[str, ToolInfo]) -> set[str]:
    return {
        name
        for name, info in tool_index.items()
        if info.visibility == VisibilityPolicyEnum.DEFERRED
    }


def _stub_names(prompt: str) -> list[str]:
    """Extract the tool name from each stub line (`` - `name` `` or `` - `name`: … ``).

    Parses the name *column* structurally rather than substring-scanning the whole
    prompt, so a one-liner that mentions another tool in backticks can't skew the
    result, and one physical line per deferred tool is enforced (a multi-line leak
    shows up as a count mismatch)."""
    return re.findall(r"^- `([^`]+)`", prompt, flags=re.MULTILINE)


def test_stub_names_exactly_match_deferred_set() -> None:
    """Stub entries are exactly the DEFERRED set — one physical line per tool.

    Set + count equality in one assertion: catches a silently-omitted deferred
    tool, an ALWAYS leak, and a multi-line description leak (which would split one
    tool across extra lines and break the count)."""
    tool_index = _real_tool_index()
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    stub_names = _stub_names(prompt)
    deferred = _deferred_names(tool_index)
    assert set(stub_names) == deferred
    assert len(stub_names) == len(deferred), "one stub line per deferred tool (multi-line leak?)"


def test_one_liner_rule_single_line_and_capped() -> None:
    """Each stub line is single-line; one-liners never exceed the char cap."""
    tool_index = _real_tool_index()
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    stub_lines = [ln for ln in prompt.splitlines() if ln.startswith("- `")]
    assert stub_lines, "expected at least one stub line"
    for line in stub_lines:
        if "`: " in line:
            one_liner = line.split("`: ", 1)[1]
            assert "\n" not in one_liner
            assert len(one_liner) <= _ONE_LINER_MAX_CHARS


def test_empty_description_falls_back_to_name_only() -> None:
    """A deferred tool with an empty description emits a name-only stub, no dangling colon."""
    tool_index = {
        "blank_tool": ToolInfo(
            name="blank_tool",
            description="",
            approval=False,
            source=ToolSourceEnum.MCP,
            visibility=VisibilityPolicyEnum.DEFERRED,
            integration="context7",
        ),
    }
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    assert "- `blank_tool`" in prompt
    assert "`blank_tool`:" not in prompt


def test_long_description_truncated_with_ellipsis() -> None:
    """A description longer than the cap is truncated and ends with an ellipsis."""
    long_desc = "x" * (_ONE_LINER_MAX_CHARS + 50)
    tool_index = {
        "verbose_tool": ToolInfo(
            name="verbose_tool",
            description=long_desc,
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.DEFERRED,
        ),
    }
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    one_liner = prompt.splitlines()[-1].split("`: ", 1)[1]
    assert len(one_liner) == _ONE_LINER_MAX_CHARS
    assert one_liner.endswith("…")


def test_first_non_empty_line_used() -> None:
    """A multi-line description collapses to its first non-empty, stripped line."""
    tool_index = {
        "multi_tool": ToolInfo(
            name="multi_tool",
            description="\n  \n  First real line.  \nSecond line.",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.DEFERRED,
        ),
    }
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    assert "- `multi_tool`: First real line." in prompt
    assert "Second line." not in prompt


def test_search_tools_directive_present() -> None:
    """The prompt must tell the model to load a tool via search_tools before calling it."""
    tool_index = _real_tool_index()
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    assert "search_tools" in prompt


def test_empty_set_returns_empty_string() -> None:
    """No deferred tools → empty string (per-turn slot contract)."""
    tool_index = {
        "always_tool": ToolInfo(
            name="always_tool",
            description="always on",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
        ),
    }
    assert build_deferred_tool_awareness_prompt(tool_index) == ""
    assert build_deferred_tool_awareness_prompt({}) == ""


_SUBHEADER_SUFFIX = " (load before use):"


def _deferred_tool(name: str, *, source: ToolSourceEnum, integration: str | None) -> ToolInfo:
    """Build a real DEFERRED ToolInfo for grouping tests (no mocks)."""
    return ToolInfo(
        name=name,
        description="does a thing",
        approval=False,
        source=source,
        visibility=VisibilityPolicyEnum.DEFERRED,
        integration=integration,
    )


def _section_by_stub(prompt: str) -> dict[str, str]:
    """Map each stub name to its section label ("" = general, no sub-header)."""
    section = ""
    mapping: dict[str, str] = {}
    for line in prompt.splitlines():
        if line.endswith(_SUBHEADER_SUFFIX):
            section = line[: -len(_SUBHEADER_SUFFIX)]
            continue
        match = re.match(r"^- `([^`]+)`", line)
        if match:
            mapping[match.group(1)] = section
    return mapping


def test_grouping_clusters_google_isolates_mcp_and_is_deterministic() -> None:
    """Deferred stubs group by integration family.

    Builds an index with the three distinct Google integrations, a None-integration
    native tool, a single-segment MCP tool, and a multi-segment-prefix MCP tool, then
    asserts (a) one Google Workspace sub-header, (b) all google_* stubs under it,
    (c) the None-integration tool in the general (no-sub-header) section, (d) the MCP
    tool under a title-cased fallback sub-header, (d2) a multi-segment MCP prefix kept
    whole — not split, not merged into a native family, and (e) deterministic output.
    """
    tool_index = {
        "google_calendar_list": _deferred_tool(
            "google_calendar_list", source=ToolSourceEnum.NATIVE, integration="google_calendar"
        ),
        "google_gmail_search": _deferred_tool(
            "google_gmail_search", source=ToolSourceEnum.NATIVE, integration="google_gmail"
        ),
        "google_drive_read": _deferred_tool(
            "google_drive_read", source=ToolSourceEnum.NATIVE, integration="google_drive"
        ),
        "task_start": _deferred_tool("task_start", source=ToolSourceEnum.NATIVE, integration=None),
        "context7_query_docs": _deferred_tool(
            "context7_query_docs", source=ToolSourceEnum.MCP, integration="context7"
        ),
        "data_api_fetch": _deferred_tool(
            "data_api_fetch", source=ToolSourceEnum.MCP, integration="data_api"
        ),
    }
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    mapping = _section_by_stub(prompt)

    # (a) exactly one Google Workspace sub-header
    assert prompt.count("Google Workspace" + _SUBHEADER_SUFFIX) == 1
    # (b) all three distinct google_* integrations cluster under it
    assert mapping["google_calendar_list"] == "Google Workspace"
    assert mapping["google_gmail_search"] == "Google Workspace"
    assert mapping["google_drive_read"] == "Google Workspace"
    # (c) None-integration native tool renders in the general (no-sub-header) section
    assert mapping["task_start"] == ""
    # (d) single-segment MCP tool under a non-empty title-cased fallback sub-header
    assert mapping["context7_query_docs"] == "Context7"
    # (d2) multi-segment MCP prefix kept whole — not split to "Data", not merged
    assert mapping["data_api_fetch"] == "Data Api"
    assert "Data" + _SUBHEADER_SUFFIX not in prompt
    # (e) deterministic — a second build is byte-identical
    assert build_deferred_tool_awareness_prompt(tool_index) == prompt
