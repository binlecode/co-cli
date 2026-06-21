"""Overlay-aware ablation guard for the rule-compliance harness.

Proves the harness composes and ablates the append-only overlay correctly: an
overlay-resident fixture section is present in its profile's composed prompt
(``_full_block``), absent from the other profile's, and cleanly removable by
``_rules_block_drop_section`` — so post-relocation ablation measurement (Plans
02/03, where reflexes move base→overlay) is attributable to the moved section.
Deterministic (no LLM): exercises the harness's section-span machinery only.
"""

from __future__ import annotations

import evals.eval_rule_compliance as harness

import co_cli.context.assembly as assembly
from co_cli.config.llm import ModelProfile


def test_overlay_resident_section_ablates_on_its_profile_arm(tmp_path, monkeypatch) -> None:
    """Ablating an overlay-resident section removes it from that profile arm's prompt.

    A ``## Frontier reflex`` section placed in ``overlays/frontier.md`` appears in the
    FRONTIER composed block, is gone after ablation while the base survives, and never
    appears in the WEAK_LOCAL block at all.
    """
    monkeypatch.setattr(assembly, "_OVERLAYS_DIR", tmp_path)
    (tmp_path / "frontier.md").write_text(
        "## Frontier reflex\n\nlean on native reasoning here.\n", encoding="utf-8"
    )

    frontier_block = harness._full_block(ModelProfile.FRONTIER)
    assert "## Frontier reflex" in frontier_block
    assert "## Frontier reflex" not in harness._full_block(ModelProfile.WEAK_LOCAL)

    overlay_sections = [
        s for s in harness._all_sections(ModelProfile.FRONTIER) if s.home == "overlay"
    ]
    assert [s.title for s in overlay_sections] == ["Frontier reflex"]
    target = overlay_sections[0]

    ablated = harness._rules_block_drop_section(target, ModelProfile.FRONTIER)
    assert "## Frontier reflex" not in ablated
    assert ablated == frontier_block.replace(target.span_text, "")
    assert "\n\n\n" not in ablated
    assert harness.build_rules_block() in ablated
