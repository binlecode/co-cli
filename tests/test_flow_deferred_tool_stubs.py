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


def _always_names(tool_index: dict[str, ToolInfo]) -> set[str]:
    return {
        name for name, info in tool_index.items() if info.visibility == VisibilityPolicyEnum.ALWAYS
    }


def _stub_names(prompt: str) -> list[str]:
    """Extract the tool name from each stub line (`` - `name` `` or `` - `name`: … ``).

    Parses the name *column* structurally rather than substring-scanning the whole
    prompt, so a one-liner that mentions another tool in backticks can't skew the
    result, and one physical line per deferred tool is enforced (a multi-line leak
    shows up as a count mismatch)."""
    return re.findall(r"^- `([^`]+)`", prompt, flags=re.MULTILINE)


def test_completeness_every_deferred_tool_named() -> None:
    """Every DEFERRED tool in the live index must appear by name in the prompt."""
    tool_index = _real_tool_index()
    deferred = _deferred_names(tool_index)
    assert deferred, "expected at least one DEFERRED tool in a real bootstrap"
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    for name in deferred:
        assert f"`{name}`" in prompt, f"deferred tool {name!r} missing from prompt"


def test_exclusion_no_always_tool_named() -> None:
    """No ALWAYS tool may appear as a stub entry in the deferred-stub prompt.

    Checks the stub *name column* (not substring-in-blob), so an ALWAYS tool
    referenced inside a deferred tool's one-liner does not cause a false failure.
    """
    tool_index = _real_tool_index()
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    stub_names = set(_stub_names(prompt))
    leaked = stub_names & _always_names(tool_index)
    assert not leaked, f"ALWAYS tools leaked as stubs: {sorted(leaked)}"


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
