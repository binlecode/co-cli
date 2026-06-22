"""Append-only prompt composition — base is profile-agnostic, overlay only ADDS.

Guards the model-profile-1b overlay mechanism: ``build_rules_block()`` is no-arg
base-only and identical for every profile, ``build_profile_overlay(profile)`` returns
a profile's own ``overlays/<profile>.md`` block (or ``None``), and an overlay appears
only in its own profile's composition — never in another's, and never by removing base
content. This is the distinct guard for the ``build_base_instructions`` /
``build_rules_block`` / ``build_profile_overlay`` path; the orchestrator-spec
byte-identity guard lives in ``test_flow_model_profile.py``.
"""

from __future__ import annotations

import co_cli.context.assembly as assembly
from co_cli.config.core import Settings
from co_cli.config.llm import LlmSettings, ModelProfile
from co_cli.context.assembly import (
    build_base_instructions,
    build_profile_overlay,
    build_rules_block,
)


def _config(provider: str) -> Settings:
    """A Settings whose backend resolves to the profile under test."""
    if provider == "gemini":
        llm = LlmSettings(provider="gemini", model="gemini-3.1-pro-preview", api_key="x")
    else:
        llm = LlmSettings(provider="ollama")
    return Settings.model_validate({"personality": None}, context={"env": {}}).model_copy(
        update={"llm": llm}
    )


def test_base_instructions_are_profile_agnostic() -> None:
    """The assembled base is byte-identical across profiles — overlays carry all divergence.

    ``build_base_instructions`` builds the base-only prefix (seed + mindsets + rules);
    the per-profile overlay is appended later by the orchestrator, not here. So this
    equality holds regardless of which overlay files ship — the base is the shared,
    profile-agnostic intersection.
    """
    assert build_base_instructions(_config("ollama")) == build_base_instructions(_config("gemini"))


def test_weak_local_overlay_ships_relocated_reflexes() -> None:
    """The weak-local overlay carries the relocated weak-scaffold sections.

    Plan 03 relocated weak-specific reflexes (Execution, Error recovery, the intent
    taxonomy, ...) out of the profile-agnostic base into ``overlays/weak_local.md``,
    so WEAK_LOCAL's overlay is non-empty and the base no longer carries them. The
    frontier overlay does not ship yet, so FRONTIER's overlay is still None.
    """
    weak_overlay = build_profile_overlay(ModelProfile.WEAK_LOCAL)
    assert weak_overlay is not None
    assert "## Execution" in weak_overlay
    assert "## Error recovery" in weak_overlay
    assert "## Execution" not in build_rules_block()
    assert "## Error recovery" not in build_rules_block()
    assert build_profile_overlay(ModelProfile.FRONTIER) is None


def test_weak_local_base_carries_full_rules_block() -> None:
    """The weak-local base prefix contains the unfiltered rules block verbatim."""
    prompt = build_base_instructions(_config("ollama"))
    assert build_rules_block() in prompt


def test_overlay_appends_for_its_profile_only(tmp_path, monkeypatch) -> None:
    """A fixture overlay appears only in its own profile's composition, never the other.

    Proves the append-only seam: a ``## Frontier overlay`` section placed in
    ``overlays/frontier.md`` shows up for FRONTIER and is absent for WEAK_LOCAL, and
    the base is untouched in both — the overlay only ADDS.
    """
    monkeypatch.setattr(assembly, "_OVERLAYS_DIR", tmp_path)
    (tmp_path / "frontier.md").write_text("## Frontier overlay\n\nbe terse.", encoding="utf-8")

    frontier_overlay = build_profile_overlay(ModelProfile.FRONTIER)
    assert frontier_overlay is not None
    assert "## Frontier overlay" in frontier_overlay
    assert build_profile_overlay(ModelProfile.WEAK_LOCAL) is None

    base = build_rules_block()
    assert "## Frontier overlay" not in base
