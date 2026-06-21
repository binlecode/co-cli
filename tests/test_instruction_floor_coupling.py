"""Coupling guard — no deferred tool's call signature may ride the instruction floor.

The floor carries WHEN/WHY (behavioral triggers — legitimately uncompactable); the
loaded schema carries HOW (call signatures). A deferred tool's schema is *not* on the
floor (it loads via ``tool_view`` per ``04_tool_protocol.md``), so hard-coding its
``tool_name(args...)`` call syntax in rules/guidance prose both re-encodes the deferred
signature every turn and contradicts the deferred-load mechanic. This is the floor-audit
plan's F5 (``docs/exec-plans/.../instruction-floor-audit.md``).

This guard iterates the live ``deps.tool_catalog`` for DEFERRED-visibility tools and asserts
none of their names appears with a call-signature pattern (``\\bname\\s*\\(``) in the
assembled floor — the base rules block, the weak-local profile overlay (the worst-case
profile that ships extra reflex prose), and the toolset guidance. The
deferred set is derived live — no hardcoded allowlist — so any future defer is auto-covered.
"""

from __future__ import annotations

import re

import pytest

from co_cli.bootstrap.core import create_deps
from co_cli.config.llm import ModelProfile
from co_cli.context.assembly import build_profile_overlay, build_rules_block
from co_cli.context.guidance import build_toolset_guidance
from co_cli.deps import VisibilityPolicyEnum


@pytest.mark.asyncio
async def test_no_deferred_tool_signature_on_floor() -> None:
    """No deferred tool's call signature appears in the assembled instruction floor (F5)."""
    # stack=None: headless deps, no MCP connection — keeps the registry deterministic
    # across environments. on_status is keyword-only and required.
    deps = await create_deps(on_status=lambda _s: None, stack=None, theme_override=None)

    deferred_names = [
        name
        for name, info in deps.tool_catalog.items()
        if info.visibility == VisibilityPolicyEnum.DEFERRED
    ]

    floor = build_rules_block() + (build_profile_overlay(ModelProfile.WEAK_LOCAL) or "")
    floor += build_toolset_guidance(deps.tool_catalog)

    for name in deferred_names:
        match = re.search(rf"\b{re.escape(name)}\s*\(", floor)
        assert match is None, (
            f"deferred tool '{name}' has a call signature on the instruction floor "
            f"(matched {match.group(0)!r}) — floor-audit F5: deferred-tool signatures "
            "belong in the loaded schema (tool_view), not in rules/guidance prose. The "
            "floor carries WHEN/WHY (behavioral triggers); the loaded schema carries HOW "
            "(call signatures). Drop the literal signature, keep the behavioral trigger."
        )
