"""Tests for compute_segment_filter() — per-segment tool exposure policy."""

from pathlib import Path

from co_cli.agent import ALWAYS_ON_TOOL_NAMES, CORE_TOOL_NAMES, build_agent
from co_cli.config import settings
from co_cli.context._orchestrate import compute_segment_filter
from co_cli.deps import CoDeps, CoCapabilityState, CoConfig, CoServices
from co_cli.tools._shell_backend import ShellBackend

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_AGENT_RESULT = build_agent(config=_CONFIG)


def _make_deps() -> CoDeps:
    """Build real CoDeps with tool_catalog populated from build_agent()."""
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=_CONFIG,
        capabilities=CoCapabilityState(
            tool_names=_AGENT_RESULT.tool_names,
            tool_approvals=_AGENT_RESULT.tool_approvals,
            tool_catalog=_AGENT_RESULT.tool_catalog,
        ),
    )
    return deps


def test_main_turn_filter_is_core_intersected_with_native() -> None:
    """Main-turn filter returns CORE_TOOL_NAMES intersected with registered native tools."""
    deps = _make_deps()
    result = compute_segment_filter(deps)
    native_names = {
        name
        for name, tc in deps.capabilities.tool_catalog.items()
        if tc.source == "native"
    }
    expected = CORE_TOOL_NAMES & native_names
    assert result == expected, (
        f"Filter mismatch.\nExpected: {sorted(expected)}\nGot: {sorted(result)}"
    )


def test_main_turn_filter_with_granted_tools() -> None:
    """Main-turn filter includes granted_tools union with CORE_TOOL_NAMES."""
    deps = _make_deps()
    deps.session.granted_tools.add("write_file")
    result = compute_segment_filter(deps)
    assert "write_file" in result, "granted write_file must appear in filter"
    # Core tools are still present
    for name in ALWAYS_ON_TOOL_NAMES:
        assert name in result, f"ALWAYS_ON tool {name!r} missing after grant"


def test_main_turn_filter_excludes_discoverable_tools_by_default() -> None:
    """Main-turn filter does not include save_memory when granted_tools is empty."""
    deps = _make_deps()
    result = compute_segment_filter(deps)
    assert "save_memory" not in result, "save_memory must not be in main-turn core surface"
    assert "write_file" not in result, "write_file must not be in main-turn core surface"


def test_main_turn_filter_contains_always_on_tools() -> None:
    """Main-turn filter includes every ALWAYS_ON_TOOL_NAMES member."""
    deps = _make_deps()
    result = compute_segment_filter(deps)
    for name in ALWAYS_ON_TOOL_NAMES:
        assert name in result, f"ALWAYS_ON tool {name!r} missing from main-turn filter"


def test_main_turn_filter_excludes_mcp_tool_names() -> None:
    """Main-turn filter contains no tool names whose catalog entry is source='mcp'.

    In this test context there is no live MCP server, so mcp_names is always empty
    and the assertion verifies the native-only contract against a native-only catalog.
    """
    deps = _make_deps()
    result = compute_segment_filter(deps)
    mcp_names = {
        name
        for name, tc in deps.capabilities.tool_catalog.items()
        if tc.source == "mcp"
    }
    overlap = result & mcp_names
    assert not overlap, f"MCP tools leaked into native filter: {overlap}"


def test_approval_resume_filter_is_exact_union() -> None:
    """Approval-resume filter returns exactly deferred_tool_names | ALWAYS_ON_TOOL_NAMES."""
    deps = _make_deps()
    deferred = {"run_shell_command"}
    result = compute_segment_filter(deps, deferred_tool_names=deferred)
    assert result == deferred | ALWAYS_ON_TOOL_NAMES, (
        f"Expected {deferred | ALWAYS_ON_TOOL_NAMES!r}, got {result!r}"
    )


def test_approval_resume_filter_no_extra_native_tools() -> None:
    """Approval-resume filter does not include unrequested native tools."""
    deps = _make_deps()
    deferred = {"run_shell_command"}
    result = compute_segment_filter(deps, deferred_tool_names=deferred)
    # Any native tool that is not in deferred and not in ALWAYS_ON must be absent
    native_names = {
        name
        for name, tc in deps.capabilities.tool_catalog.items()
        if tc.source == "native"
    }
    unrequested = native_names - deferred - ALWAYS_ON_TOOL_NAMES
    leaked = result & unrequested
    assert not leaked, f"Unexpected native tools in resume filter: {leaked}"
