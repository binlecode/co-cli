"""Guidance-content guard for the delegate prose (Phase 3.7 R1).

The delegate guidance rides the instruction floor whenever the ``delegate`` tool is
present. It must carry the converged delegation contract for a now-write-capable
sub-agent: the stale read-mostly D4 wording is gone, and the write-era dimensions
(D5 don't-redo, D6 research-vs-act + verify, D7 self-report side-effects, D10
evidence-not-authority) are present. No LLM — a literal substring check on the
assembled guidance.
"""

from __future__ import annotations

from co_cli.context.guidance import build_toolset_guidance
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum


def _guidance_with_delegate() -> str:
    catalog = {
        "delegate": ToolInfo(
            name="delegate",
            description="Delegate a multi-step subtask to a focused sub-agent.",
            is_approval_required=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
            is_concurrent_safe=False,
        )
    }
    return build_toolset_guidance(catalog)


def test_delegate_guidance_drops_stale_d4_wording() -> None:
    guidance = _guidance_with_delegate().lower()
    assert "read/search/gather" not in guidance
    assert "gather" not in guidance


def test_delegate_guidance_carries_write_era_dimensions() -> None:
    guidance = _guidance_with_delegate().lower()
    assert "don't redo" in guidance
    assert "verify" in guidance
    assert "self-report" in guidance
    assert "evidence" in guidance
