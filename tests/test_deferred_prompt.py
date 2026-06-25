"""Behavioral tests for the deferred-tool awareness prompt builder.

The stub generator and the per-turn visibility filter read one shared reveal set
(``runtime.revealed_tools``), so a deferred tool that has been revealed via tool_view
must not also be stubbed with a "load it via tool_view" line. These tests assert the
observable prompt text:

- builder boundary: a revealed DEFERRED tool is omitted while an unrevealed one remains;
  all-revealed input yields the empty-string contract.
- wired runtime path: ``deferred_tool_awareness_prompt(deps)`` threads
  ``deps.runtime.revealed_tools`` into the builder.
"""

from tests._settings import SETTINGS_NO_MCP

from co_cli.agent._instructions import deferred_tool_awareness_prompt
from co_cli.deps import (
    CoDeps,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt
from co_cli.tools.shell_backend import ShellBackend


def _deferred(name: str, description: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=description,
        is_approval_required=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.DEFERRED,
        is_concurrent_safe=False,
    )


_CATALOG = {
    "skill_create": _deferred("skill_create", "Author a new skill."),
    "skill_delete": _deferred("skill_delete", "Remove a skill."),
}


def test_revealed_tool_omitted_unrevealed_kept() -> None:
    """A revealed DEFERRED tool drops its stub; an unrevealed sibling keeps its stub."""
    prompt = build_deferred_tool_awareness_prompt(_CATALOG, {"skill_create"})
    assert "skill_create" not in prompt
    assert "skill_delete" in prompt


def test_all_revealed_yields_empty_string() -> None:
    """When every DEFERRED tool is revealed the builder returns the empty-string contract."""
    prompt = build_deferred_tool_awareness_prompt(_CATALOG, {"skill_create", "skill_delete"})
    assert prompt == ""


def test_none_revealed_stubs_all() -> None:
    """With nothing revealed, every DEFERRED tool is stubbed (baseline)."""
    prompt = build_deferred_tool_awareness_prompt(_CATALOG, set())
    assert "skill_create" in prompt
    assert "skill_delete" in prompt


def test_wired_path_threads_runtime_reveal_set(tmp_path) -> None:
    """deferred_tool_awareness_prompt(ctx) honors runtime.revealed_tools.

    Proves the _instructions.py threading: a revealed name in runtime drops its stub
    while the unrevealed sibling remains.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        tool_catalog=dict(_CATALOG),
        session=CoSessionState(),
        tool_results_dir=tmp_path / "tool-results",
    )
    deps.runtime.revealed_tools.add("skill_create")
    prompt = deferred_tool_awareness_prompt(deps)
    assert "skill_create" not in prompt
    assert "skill_delete" in prompt
