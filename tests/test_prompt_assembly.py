"""Tests for system prompt assembly and personality composition.

Static prompt: instructions + rules + counter-steering (no personality).
Personality: soul + behaviors (composed separately, injected per turn).
"""

import time

from co_cli.prompts import assemble_prompt, _RULES_DIR
from co_cli.prompts.personalities._composer import (
    VALID_PERSONALITIES,
    load_soul,
    load_soul_seed,
    load_traits,
    compose_personality,
)


# --- Instructions ---


def test_static_prompt_has_no_generic_identity_claim():
    """Static prompt must not assert a generic identity — soul provides identity."""
    prompt, manifest = assemble_prompt("gemini")
    assert "You are a personal companion" not in prompt
    assert "You are Co" not in prompt


# --- Static prompt has no personality ---


def test_static_prompt_has_no_soul_section():
    """Static prompt never contains personality content."""
    prompt, manifest = assemble_prompt("gemini")
    assert "## Soul" not in prompt
    assert "Adopt this persona" not in prompt


# --- Rules ---


def test_prompt_includes_all_five_rules():
    """All 5 companion rules loaded with cross-cutting behavioral content."""
    prompt, manifest = assemble_prompt("gemini")
    rule_names = {"identity", "safety", "reasoning", "tool_protocol", "workflow"}
    loaded = set(manifest.parts_loaded)
    assert rule_names.issubset(loaded), (
        f"Missing rules: {rule_names - loaded}. Loaded: {manifest.parts_loaded}"
    )
    lower = prompt.lower()
    assert "tone" in lower
    assert "approval" in lower
    assert "verify" in lower or "trust tool" in lower


def test_rules_token_budget():
    """Combined rule text stays under ~1500 tokens (~6000 chars heuristic)."""
    total_chars = 0
    for rule_path in sorted(_RULES_DIR.glob("*.md")):
        total_chars += len(rule_path.read_text(encoding="utf-8").strip())
    assert total_chars < 6000, (
        f"Rules total {total_chars} chars (~{total_chars // 4} tokens), "
        f"expected < 6000 chars (~1500 tokens)"
    )


# --- Memory not in prompt ---


def test_prompt_has_no_memory():
    """Memory/knowledge is NOT in the assembled prompt."""
    prompt, manifest = assemble_prompt("gemini")
    assert "Background Reference" not in prompt


# --- Counter-steering ---


def test_counter_steering_for_quirk_model():
    """Model with known quirks gets counter-steering appended."""
    prompt, manifest = assemble_prompt("ollama", model_name="qwen3-coder-next")
    assert "## Model-Specific Guidance" in prompt
    assert "counter_steering" in manifest.parts_loaded


def test_counter_steering_absent_default():
    """Unknown model gets no counter-steering."""
    prompt, manifest = assemble_prompt("gemini", model_name="unknown-model-xyz")
    assert "## Model-Specific Guidance" not in prompt
    assert "counter_steering" not in manifest.parts_loaded


# --- Budget ---


def test_static_prompt_under_budget():
    """Static system prompt (instructions + rules + quirks) stays bounded."""
    prompt, manifest = assemble_prompt("gemini")
    assert manifest.total_chars < 6500, (
        f"Static prompt is {manifest.total_chars} chars, expected < 6500"
    )


def test_prompt_assembly_under_100ms():
    """Prompt assembly completes in under 100ms."""
    start = time.perf_counter()
    assemble_prompt("gemini")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, (
        f"Assembly took {elapsed_ms:.1f}ms, expected < 100ms"
    )


# --- Personality composition ---


def test_all_roles_have_soul():
    """Every role in traits/ has a loadable soul file."""
    for name in VALID_PERSONALITIES:
        soul = load_soul(name)
        assert len(soul) > 0, f"Empty soul for role: {name}"


def test_soul_seed_is_first_paragraph():
    """Soul seed is the opening identity declaration — stops before any ## section."""
    for name in VALID_PERSONALITIES:
        seed = load_soul_seed(name)
        assert len(seed) > 0, f"Empty seed for role: {name}"
        assert "##" not in seed, f"Seed contains ## heading for role: {name}"
        assert seed.startswith("You are"), f"Seed doesn't start with identity declaration: {name}"


def test_static_prompt_starts_with_soul_seed():
    """With a personality, static prompt opens with the soul seed — identity first."""
    seed = load_soul_seed("finch")
    prompt, manifest = assemble_prompt("gemini", soul_seed=seed)
    assert prompt.startswith(seed)
    assert "soul_seed" in manifest.parts_loaded


def test_all_roles_have_traits():
    """Every role has a parseable traits file with 4 traits."""
    for name in VALID_PERSONALITIES:
        traits = load_traits(name)
        assert len(traits) == 4, (
            f"Role {name} has {len(traits)} traits, expected 4: {traits}"
        )


def test_traits_have_behavior_files():
    """Every trait value referenced in traits files has a behavior file."""
    from pathlib import Path
    behaviors_dir = Path(__file__).parent.parent / "co_cli" / "prompts" / "personalities" / "behaviors"
    for name in VALID_PERSONALITIES:
        traits = load_traits(name)
        for trait_name, trait_value in traits.items():
            behavior_file = behaviors_dir / f"{trait_name}-{trait_value}.md"
            assert behavior_file.exists(), (
                f"Missing behavior file for {name}: {behavior_file.name}"
            )


def test_compose_personality_contains_soul_body():
    """Composed personality contains soul body (## Never) but not the seed — seed is in static prompt."""
    composed = compose_personality("finch")
    assert "## Soul" in composed
    assert "## Never" in composed
    # Seed paragraph must NOT be repeated in per-turn block
    assert "You are Finch" not in composed
    assert "teaches by doing" not in composed


def test_compose_personality_contains_behaviors():
    """Composed personality includes behavior file content."""
    composed = compose_personality("finch")
    # finch has communication: balanced → should include balanced communication content
    assert "Balanced Communication" in composed
    # finch has relationship: mentor → should include mentor content
    assert "Mentor Relationship" in composed


def test_no_adoption_mandate_in_personality():
    """Composed personality must contain no adoption mandate — soul IS the identity."""
    composed = compose_personality("jeff")
    assert "overrides your default" not in composed
    assert "Adopt this persona" not in composed


def test_compose_personality_differs_by_role():
    """Different roles produce different personality blocks."""
    finch = compose_personality("finch")
    jeff = compose_personality("jeff")
    # Seed text is not in per-turn block — differentiation comes from behaviors
    assert "Balanced Communication" in finch
    assert "Warm Communication" in jeff
    assert "Balanced Communication" not in jeff


def test_personality_under_budget():
    """Composed personality stays under 3500 chars (soul body + 5 behaviors)."""
    for name in VALID_PERSONALITIES:
        composed = compose_personality(name)
        assert len(composed) < 4500, (
            f"Personality '{name}' is {len(composed)} chars, expected < 4500"
        )


def test_total_prompt_under_budget():
    """Static prompt (with soul seed) + largest personality body stays under budget."""
    for name in VALID_PERSONALITIES:
        seed = load_soul_seed(name)
        prompt, manifest = assemble_prompt("gemini", soul_seed=seed)
        personality_body = len(compose_personality(name))
        total = manifest.total_chars + personality_body
        assert total < 11500, (
            f"Total for '{name}' is {total} chars "
            f"(static {manifest.total_chars} + personality {personality_body}), "
            f"expected < 11500"
        )


def test_valid_personalities_derived_from_traits():
    """VALID_PERSONALITIES is derived from traits/ folder, not hardcoded."""
    assert "finch" in VALID_PERSONALITIES
    assert "jeff" in VALID_PERSONALITIES
    assert len(VALID_PERSONALITIES) == 2


def test_static_prompt_has_no_core_traits_section():
    """Static identity rules must not define core traits — soul provides all identity."""
    from pathlib import Path
    identity = (
        Path(__file__).parent.parent / "co_cli/prompts/rules/01_identity.md"
    ).read_text()
    assert "## Core traits" not in identity
    assert "Helpful:" not in identity


def test_identity_rule_does_not_instruct_manual_recall():
    """Identity rule must not tell the model to call recall_memory manually."""
    from pathlib import Path
    identity = (
        Path(__file__).parent.parent / "co_cli/prompts/rules/01_identity.md"
    ).read_text()
    assert "recall memories relevant" not in identity


def test_instructions_preamble_is_empty():
    """The instructions.md preamble must be empty — soul provides all identity."""
    from pathlib import Path
    instructions = (
        Path(__file__).parent.parent / "co_cli/prompts/instructions.md"
    ).read_text().strip()
    assert instructions == ""
