"""Build a per-tool awareness prompt for deferred tool discovery.

The SDK's ToolSearchToolset handles per-tool deferred visibility, but a deferred
tool's full schema is absent from the prompt until loaded via search_tools. This
module emits a per-tool stub (name + one-line purpose) for every DEFERRED tool so
the model knows the tool exists and what it does, and can load it via search_tools
before calling it. The list is derived from the live tool_index — complete by
construction, with no hardcoded allowlist to forget when a tool goes DEFERRED.

Stubs are grouped by integration family (e.g. all ``google_*`` tools under one
"Google Workspace" sub-header) so a small model treats them as one coherent
capability cluster instead of N loose interleaved lines. Grouping derives entirely
from the live index — a new ``google_*`` integration auto-joins the cluster.
"""

from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

# Max chars for a stub one-liner; longer descriptions are truncated with an ellipsis.
_ONE_LINER_MAX_CHARS = 100

# Friendly section headers for known integration families. Presentation-only: an
# unmapped family still groups and renders under a title-cased fallback, so this map
# is not a completeness-bearing allowlist (tool membership stays index-derived).
_FAMILY_LABELS = {"google": "Google Workspace"}


def _stub_one_liner(description: str) -> str:
    """Return the first non-empty line of description, stripped and length-capped.

    Truncates to _ONE_LINER_MAX_CHARS (ellipsis included) so a stub never re-imports
    the full multi-line schema cost the DEFERRED flip removed. Returns "" when the
    description has no non-empty line (caller emits a name-only stub).
    """
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if line:
            if len(line) > _ONE_LINER_MAX_CHARS:
                return line[: _ONE_LINER_MAX_CHARS - 1] + "…"
            return line
    return ""


def _stub_line(name: str, description: str) -> str:
    """Render one stub line: ``- `name`: one-liner`` (or ``- `name``` when empty)."""
    one_liner = _stub_one_liner(description)
    if one_liner:
        return f"- `{name}`: {one_liner}"
    return f"- `{name}`"


def _family_key(info: ToolInfo) -> str | None:
    """Return the integration-family key for a tool, or None for the general family.

    Native integrations key on the segment before the first ``_`` so all ``google_*``
    integrations collapse into one ``google`` family by construction. MCP integrations
    use the whole ``integration`` string — the user-configured server prefix, which may
    itself contain ``_`` — so two distinct servers are never merged and a
    ``google_*``-prefixed server is never absorbed into the native Google family. Tools
    with no integration (native primitives, prefixless MCP) fall into the general family.
    """
    integration = info.integration
    if integration is None:
        return None
    if info.source == ToolSourceEnum.MCP:
        return integration
    return integration.split("_", 1)[0]


def _family_label(family_key: str) -> str:
    """Resolve the friendly sub-header for an integration family.

    Known families use _FAMILY_LABELS; an unmapped family falls back to a title-cased
    rendering of its key (``context7`` → "Context7", ``data_api`` → "Data Api").
    """
    if family_key in _FAMILY_LABELS:
        return _FAMILY_LABELS[family_key]
    return family_key.replace("_", " ").title()


def build_deferred_tool_awareness_prompt(
    tool_index: dict[str, ToolInfo],
) -> str:
    """Return a per-tool stub prompt for every DEFERRED tool in tool_index.

    Each deferred tool emits one line: ``- `name`: one-liner`` (or ``- `name``` when
    the description is empty). Stubs are grouped by integration family: native
    primitives (no integration) render first under the top directive with no sub-header
    (preserving today's look); each integration family then renders under a sub-header
    line, e.g. ``Google Workspace (load before use):``. Within a family stubs are sorted
    by name, and families are ordered alphabetically by label — the whole output is
    deterministic so the per-turn slot does not churn.

    Config-gated and MCP tools only appear when their integration is registered in
    tool_index, so gating falls out naturally. Returns the empty string when no deferred
    tools exist — the per-turn instruction slot relies on that contract.
    """
    general: list[str] = []
    families: dict[str, list[str]] = {}
    for name in sorted(tool_index):
        info = tool_index[name]
        if info.visibility != VisibilityPolicyEnum.DEFERRED:
            continue
        line = _stub_line(name, info.description)
        family_key = _family_key(info)
        if family_key is None:
            general.append(line)
        else:
            families.setdefault(family_key, []).append(line)
    if not general and not families:
        return ""
    header = (
        "Additional tools are available but not loaded. "
        "Load a tool with search_tools before calling it:"
    )
    sections: list[str] = [header]
    sections.extend(general)
    for family_key in sorted(families, key=_family_label):
        sections.append(f"{_family_label(family_key)} (load before use):")
        sections.extend(families[family_key])
    return "\n".join(sections)
