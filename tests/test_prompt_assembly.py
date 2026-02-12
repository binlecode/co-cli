"""Tests for rules-only prompt assembly."""

import pytest

from co_cli.prompts import assemble_prompt


def test_prompt_starts_with_instructions():
    """Assembled prompt starts with instructions.md bootstrap content."""
    prompt, manifest = assemble_prompt("gemini")
    assert prompt.startswith("You are Co, a personal assistant")


def test_prompt_contains_all_rules():
    """Assembled prompt includes content from all 7 rule files."""
    prompt, manifest = assemble_prompt("gemini")
    # 01_identity.md
    assert "Local-first" in prompt
    assert "multi-turn conversation" in prompt
    # 02_intent.md
    assert "Default to inquiry" in prompt
    # 03_safety.md
    assert "approval" in prompt.lower()
    assert "safe shell commands" in prompt.lower()
    # 04_reasoning.md
    assert "Trust tool output over prior assumptions" in prompt
    # 05_tool_use.md
    assert "load_aspect" in prompt
    assert "load_personality" in prompt
    assert "display" in prompt.lower()
    # 06_response_style.md
    assert "high-signal" in prompt.lower()
    # 07_workflow.md
    assert "execution loop" in prompt.lower()


def test_prompt_has_no_personality():
    """Personality is NOT in the assembled prompt (tool-loaded only)."""
    prompt, manifest = assemble_prompt("gemini")
    assert "## Personality" not in prompt
    assert "Finch Weinberg" not in prompt
    assert "eager robot" not in prompt


def test_prompt_has_no_memory():
    """Memory/knowledge is NOT in the assembled prompt (tool-loaded only)."""
    prompt, manifest = assemble_prompt("gemini")
    assert "Background Reference" not in prompt


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


def test_manifest_parts_match():
    """Manifest parts_loaded reflects what was actually assembled."""
    _prompt, manifest = assemble_prompt("gemini")
    assert "instructions" in manifest.parts_loaded
    # Numbered filenames are normalized to logical rule IDs.
    assert "identity" in manifest.parts_loaded
    assert "intent" in manifest.parts_loaded
    assert "safety" in manifest.parts_loaded
    assert "reasoning" in manifest.parts_loaded
    assert "tool_use" in manifest.parts_loaded
    assert "response_style" in manifest.parts_loaded
    assert "workflow" in manifest.parts_loaded


def test_manifest_char_count():
    """Manifest total_chars matches actual prompt length."""
    prompt, manifest = assemble_prompt("gemini")
    assert manifest.total_chars == len(prompt)


def test_prompt_under_budget():
    """Rules-only prompt remains bounded in size."""
    prompt, manifest = assemble_prompt("gemini")
    assert manifest.total_chars < 6000, (
        f"Prompt is {manifest.total_chars} chars, expected < 6000"
    )


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
