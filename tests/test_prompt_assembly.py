"""Tests for system prompt assembly: instructions + soul seed + rules + counter-steering.

Critical functional coverage aligned with TODO-co-agentic-loop-and-prompting.md:
- §9: Conditional layered assembly (instructions → soul seed → rules → quirks)
- §10: Five companion rules, <1100 token budget, cross-cutting behavioral principles
- §11: Personality system (soul seed injection, preset swapping)
- §12: Model adaptation (counter-steering for quirky models)
- §22: < 100ms prompt assembly overhead
"""

import time

import pytest

from co_cli.prompts import assemble_prompt, _RULES_DIR
from co_cli.prompts.personalities._registry import PRESETS
from co_cli.prompts.personalities._composer import get_soul_seed


# --- Instructions ---


def test_prompt_starts_with_instructions():
    """Assembled prompt starts with instructions.md bootstrap content."""
    prompt, manifest = assemble_prompt("gemini")
    assert prompt.startswith("You are Co, a personal companion")


# --- Soul seed ---


def test_prompt_contains_soul_seed():
    """Soul seed is injected when personality is provided."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert "## Soul" in prompt
    assert "teach by doing" in prompt
    assert "soul_seed" in manifest.parts_loaded


def test_prompt_soul_seed_absent_without_personality():
    """No soul seed when personality is None."""
    prompt, manifest = assemble_prompt("gemini")
    assert "## Soul" not in prompt
    assert "soul_seed" not in manifest.parts_loaded


def test_soul_seed_framing_present():
    """Soul seed includes the override boundary framing."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert "never overrides safety or factual accuracy" in prompt


def test_soul_seed_swaps_with_personality():
    """Different personality presets inject different soul seeds."""
    prompt_finch, _ = assemble_prompt("gemini", personality="finch")
    prompt_jeff, _ = assemble_prompt("gemini", personality="jeff")
    assert "teach by doing" in prompt_finch
    assert "teach by doing" not in prompt_jeff
    assert "eager learner" in prompt_jeff
    assert "eager learner" not in prompt_finch


# --- Rules ---


def test_prompt_includes_all_five_rules():
    """All 5 companion rules loaded with cross-cutting behavioral content (§10).

    Tests behavioral invariants that persist across rule rewrites:
    identity (tone adaptation), safety (approval model), reasoning
    (verification principle), tool strategy, workflow.
    """
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    # Structural: all 5 rules present in manifest
    rule_names = {"identity", "safety", "reasoning", "tool_protocol", "workflow"}
    loaded = set(manifest.parts_loaded)
    assert rule_names.issubset(loaded), (
        f"Missing rules: {rule_names - loaded}. Loaded: {manifest.parts_loaded}"
    )
    # Behavioral invariants (stable across current rules and §10 redesign):
    lower = prompt.lower()
    # §10.1: identity always mentions tone adaptation
    assert "tone" in lower
    # §10.2: safety always mentions approval model
    assert "approval" in lower
    # §10.3: reasoning always mentions verification/trust of tool output
    assert "verify" in lower or "trust tool" in lower


def test_rule_count_is_five():
    """Exactly 5 rule files exist (§10: five companion rules)."""
    rule_files = sorted(_RULES_DIR.glob("*.md"))
    assert len(rule_files) == 5, (
        f"Expected 5 rules, found {len(rule_files)}: "
        f"{[f.name for f in rule_files]}"
    )


def test_rules_token_budget():
    """Combined rule text stays under ~1100 tokens (§10 token budget).

    Uses character count heuristic: ~4 chars/token for English text.
    At 1100 tokens × 4 chars = 4400 chars. We allow up to 5000 chars
    to absorb tokenizer variance across Gemini and Ollama models.
    """
    total_chars = 0
    for rule_path in sorted(_RULES_DIR.glob("*.md")):
        total_chars += len(rule_path.read_text(encoding="utf-8").strip())
    assert total_chars < 5000, (
        f"Rules total {total_chars} chars (~{total_chars // 4} tokens), "
        f"expected < 5000 chars (~1250 tokens)"
    )


# --- Memory not in prompt ---


def test_prompt_has_no_memory():
    """Memory/knowledge is NOT in the assembled prompt (tool-loaded only)."""
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


# --- Manifest ---


def test_manifest_parts_match():
    """Manifest parts_loaded reflects what was actually assembled."""
    _prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert "instructions" in manifest.parts_loaded
    assert "soul_seed" in manifest.parts_loaded
    assert "identity" in manifest.parts_loaded
    assert "safety" in manifest.parts_loaded
    assert "reasoning" in manifest.parts_loaded
    assert "tool_protocol" in manifest.parts_loaded
    assert "workflow" in manifest.parts_loaded


def test_manifest_char_count():
    """Manifest total_chars matches actual prompt length."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert manifest.total_chars == len(prompt)


def test_prompt_under_budget():
    """Full system prompt (instructions + soul + rules + quirks) stays bounded."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert manifest.total_chars < 6000, (
        f"Prompt is {manifest.total_chars} chars, expected < 6000"
    )


def test_prompt_assembly_under_100ms():
    """Prompt assembly completes in under 100ms (§22 performance criterion)."""
    start = time.perf_counter()
    assemble_prompt("gemini", personality="finch")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, (
        f"Assembly took {elapsed_ms:.1f}ms, expected < 100ms"
    )


# --- Personality registry ---


def test_all_presets_have_soul_seed():
    """Every personality preset has a loadable soul seed file."""
    for name in PRESETS:
        seed = get_soul_seed(name)
        assert len(seed) > 0, f"Empty soul seed for preset: {name}"


def test_get_soul_seed_returns_string():
    """get_soul_seed returns non-empty string for known presets."""
    seed = get_soul_seed("finch")
    assert isinstance(seed, str)
    assert "teach by doing" in seed


# --- Validation ---


def test_rule_filenames_must_be_numbered(tmp_path, monkeypatch):
    """Invalid rule filename format fails fast."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "identity.md").write_text("Identity rule\n", encoding="utf-8")
    monkeypatch.setattr("co_cli.prompts._RULES_DIR", rules_dir)

    with pytest.raises(ValueError, match="Invalid rule filename"):
        assemble_prompt("gemini")


def test_rule_order_must_be_contiguous(tmp_path, monkeypatch):
    """Missing numeric prefix in sequence fails fast."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "01_identity.md").write_text("Identity rule\n", encoding="utf-8")
    (rules_dir / "03_safety.md").write_text("Safety rule\n", encoding="utf-8")
    monkeypatch.setattr("co_cli.prompts._RULES_DIR", rules_dir)

    with pytest.raises(ValueError, match="contiguous"):
        assemble_prompt("gemini")
