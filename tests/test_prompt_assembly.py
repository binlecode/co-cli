"""Tests for system prompt assembly: instructions + soul seed + rules + counter-steering."""

import pytest

from co_cli.prompts import assemble_prompt
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


def test_prompt_contains_all_rules():
    """Assembled prompt includes content from all 6 rule files."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    # 01_identity.md
    assert "Local-first" in prompt
    assert "multi-turn conversation" in prompt
    assert "Adapt your tone" in prompt
    # 02_safety.md
    assert "approval" in prompt.lower()
    assert "safe shell commands" in prompt.lower()
    # 03_reasoning.md
    assert "Trust tool output over prior assumptions" in prompt
    # 04_tool_protocol.md
    assert "display" in prompt.lower()
    assert "Match explanation depth" in prompt
    # 05_context.md
    assert "recall memories" in prompt.lower()
    assert "save it to memory" in prompt
    # 06_workflow.md
    assert "Understand goal and constraints" in prompt
    assert "Complete the requested outcome" in prompt


def test_deleted_rules_absent():
    """Old rules that were removed are not in the prompt."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    # Old 02_intent — rigid binary
    assert "Default to inquiry" not in prompt
    assert "Inquiry:" not in prompt
    assert "Directive:" not in prompt
    # Old 07_response_style — conflicts with personality
    assert "high-signal" not in prompt
    assert "technically precise" not in prompt


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
    assert "context" in manifest.parts_loaded
    assert "workflow" in manifest.parts_loaded


def test_manifest_char_count():
    """Manifest total_chars matches actual prompt length."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert manifest.total_chars == len(prompt)


def test_prompt_under_budget():
    """System prompt remains bounded in size."""
    prompt, manifest = assemble_prompt("gemini", personality="finch")
    assert manifest.total_chars < 6000, (
        f"Prompt is {manifest.total_chars} chars, expected < 6000"
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
