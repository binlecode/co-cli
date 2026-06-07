"""Tests for the per-tool deferred-stub awareness prompt.

Guards the one functional contract co owns here: every DEFERRED tool appears in
the discovery prompt exactly once — none silently omitted, no ALWAYS tool leaking
in. A tool missing from this prompt is invisible to the model and can never be
loaded via tool_view, so this is the bug-finder for the deferral wiring.
Built from a real bootstrap tool_index so a schema moving between visibility
buckets fails CI. The live tool_view → load → call sequence is exercised by the
tool_view resolution tests and behavioral evals, not here.
"""

import re

from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import ToolInfo, VisibilityPolicyEnum
from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt


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
    tool (model can never discover it), an ALWAYS leak, and a multi-line
    description leak (which would split one tool across extra lines)."""
    tool_index = _real_tool_index()
    prompt = build_deferred_tool_awareness_prompt(tool_index)
    stub_names = _stub_names(prompt)
    deferred = _deferred_names(tool_index)
    assert set(stub_names) == deferred
    assert len(stub_names) == len(deferred), "one stub line per deferred tool (multi-line leak?)"
