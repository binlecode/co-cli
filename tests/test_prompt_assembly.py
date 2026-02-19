"""Tests for system prompt assembly and personality composition.

Static prompt: instructions + rules + counter-steering (no personality).
Personality: soul + behaviors + mandate (composed separately, injected per turn).
"""

import time

from co_cli.prompts import assemble_prompt, _RULES_DIR
from co_cli.prompts.personalities._composer import (
    VALID_PERSONALITIES,
    load_soul,
    load_traits,
    compose_personality,
)


# --- Instructions ---


def test_prompt_starts_with_instructions():
    """Assembled prompt starts with instructions.md bootstrap content."""
    prompt, manifest = assemble_prompt("gemini")
    assert prompt.startswith("You are Co, a personal companion")


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
    """Combined rule text stays under ~1100 tokens (~5000 chars heuristic)."""
    total_chars = 0
    for rule_path in sorted(_RULES_DIR.glob("*.md")):
        total_chars += len(rule_path.read_text(encoding="utf-8").strip())
    assert total_chars < 5000, (
        f"Rules total {total_chars} chars (~{total_chars // 4} tokens), "
        f"expected < 5000 chars (~1250 tokens)"
    )


# --- Memory not in prompt ---


def test_prompt_has_no_memory():
    """Memory/knowledge is NOT in the assembled prompt."""
    prompt, manifest = assemble_prompt("gemini")
    assert "Background Reference" not in prompt


# --- Counter-steering ---


def test_counter_steering_for_quirk_model():
    """Model with known quirks gets counter-steering appended."""
    prompt, manifest = assemble_prompt("ollama", model_name="glm-4.7-flash")
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
    assert manifest.total_chars < 5500, (
        f"Static prompt is {manifest.total_chars} chars, expected < 5500"
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


def test_all_roles_have_traits():
    """Every role has a parseable traits file with 5 traits."""
    for name in VALID_PERSONALITIES:
        traits = load_traits(name)
        assert len(traits) == 5, (
            f"Role {name} has {len(traits)} traits, expected 5: {traits}"
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


def test_compose_personality_contains_soul():
    """Composed personality contains the role identity basis."""
    composed = compose_personality("finch")
    assert "## Soul" in composed
    assert "You are Co" in composed
    assert "teaches by doing" in composed


def test_compose_personality_contains_behaviors():
    """Composed personality includes behavior file content."""
    composed = compose_personality("finch")
    # finch has communication: balanced → should include balanced communication content
    assert "Balanced Communication" in composed
    # finch has relationship: mentor → should include mentor content
    assert "Mentor Relationship" in composed


def test_compose_personality_contains_mandate():
    """Composed personality includes the adoption mandate."""
    composed = compose_personality("finch")
    assert "Adopt this persona fully" in composed
    assert "Match expression depth to context" in composed
    assert "never overrides safety or factual accuracy" in composed


def test_compose_personality_differs_by_role():
    """Different roles produce different personality blocks."""
    finch = compose_personality("finch")
    terse = compose_personality("terse")
    assert "teaches by doing" in finch
    assert "teaches by doing" not in terse
    assert "Balanced Communication" in finch
    assert "Terse Communication" in terse


def test_personality_under_budget():
    """Composed personality stays under 3500 chars (soul + 5 behaviors + mandate)."""
    for name in VALID_PERSONALITIES:
        composed = compose_personality(name)
        assert len(composed) < 3500, (
            f"Personality '{name}' is {len(composed)} chars, expected < 3500"
        )


def test_total_prompt_under_budget():
    """Static prompt + largest personality stays under 8500 char budget."""
    prompt, manifest = assemble_prompt("gemini")
    largest_personality = max(
        len(compose_personality(name)) for name in VALID_PERSONALITIES
    )
    total = manifest.total_chars + largest_personality
    assert total < 8500, (
        f"Total is {total} chars (static {manifest.total_chars} + "
        f"personality {largest_personality}), expected < 8500"
    )


def test_valid_personalities_derived_from_traits():
    """VALID_PERSONALITIES is derived from traits/ folder, not hardcoded."""
    assert "finch" in VALID_PERSONALITIES
    assert "terse" in VALID_PERSONALITIES
    assert len(VALID_PERSONALITIES) == 4


# --- Reasoning depth ---


def test_compose_personality_quick_depth_overrides_thoroughness():
    """quick depth swaps thoroughness from role default to minimal."""
    composed = compose_personality("finch", "quick")
    assert "Minimal Thoroughness" in composed
    assert "Comprehensive Thoroughness" not in composed


def test_compose_personality_quick_depth_overrides_curiosity():
    """quick depth swaps curiosity from proactive to reactive."""
    composed = compose_personality("finch", "quick")
    assert "Reactive Curiosity" in composed
    assert "Proactive Curiosity" not in composed


def test_compose_personality_deep_depth_overrides_thoroughness():
    """deep depth promotes thoroughness to comprehensive for standard-thoroughness roles."""
    # jeff has thoroughness: standard — deep overrides to comprehensive
    composed = compose_personality("jeff", "deep")
    assert "Comprehensive Thoroughness" in composed


def test_compose_personality_deep_depth_noop_for_comprehensive_role():
    """deep depth is a no-op for roles already at comprehensive thoroughness."""
    # finch has thoroughness: comprehensive — deep changes nothing
    assert compose_personality("finch", "normal") == compose_personality("finch", "deep")


def test_compose_personality_normal_depth_uses_role_defaults():
    """normal depth applies no overrides — role trait values are unchanged."""
    composed = compose_personality("finch", "normal")
    assert "Comprehensive Thoroughness" in composed
    assert "Proactive Curiosity" in composed


def test_compose_personality_default_depth_is_normal():
    """compose_personality with no depth argument equals normal depth."""
    assert compose_personality("finch") == compose_personality("finch", "normal")
