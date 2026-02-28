"""Tests for system prompt assembly and personality composition.

Static prompt: instructions + rules + counter-steering (no personality).
Personality: expanded soul seed (identity + Core + Never) in static prompt.
Task-specific guidance: loaded on demand via load_task_strategy tool.
"""

import time
from pathlib import Path

from co_cli.prompts import assemble_prompt, _RULES_DIR
from co_cli.prompts.personalities._composer import (
    REQUIRED_STRATEGY_TASK_TYPES,
    VALID_PERSONALITIES,
    load_soul_seed,
    validate_personality_files,
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


# --- Personality: expanded seed ---


def test_all_roles_have_soul():
    """Every role has a loadable seed file."""
    for name in VALID_PERSONALITIES:
        seed = load_soul_seed(name)
        assert len(seed) > 0, f"Empty seed for role: {name}"


def test_soul_seed_starts_with_identity():
    """Soul seed opens with identity declaration and contains Core and Never sections."""
    for name in VALID_PERSONALITIES:
        seed = load_soul_seed(name)
        assert len(seed) > 0, f"Empty seed for role: {name}"
        assert seed.startswith("You are"), f"Seed doesn't start with identity declaration: {name}"
        assert "Core:" in seed, f"Seed missing Core section for role: {name}"
        assert "Never:" in seed, f"Seed missing Never section for role: {name}"


def test_static_prompt_starts_with_soul_seed():
    """With a personality, static prompt opens with the soul seed — identity first."""
    seed = load_soul_seed("finch")
    prompt, manifest = assemble_prompt("gemini", soul_seed=seed)
    assert prompt.startswith(seed)
    assert "soul_seed" in manifest.parts_loaded


def test_valid_personalities_derived_from_souls():
    """VALID_PERSONALITIES is derived from souls/ folder, not hardcoded."""
    assert "finch" in VALID_PERSONALITIES
    assert "jeff" in VALID_PERSONALITIES
    assert len(VALID_PERSONALITIES) >= 2


def test_static_prompt_has_no_core_traits_section():
    """Static identity rules must not define core traits — soul provides all identity."""
    identity = (
        Path(__file__).parent.parent / "co_cli/prompts/rules/01_identity.md"
    ).read_text()
    assert "## Core traits" not in identity
    assert "Helpful:" not in identity


def test_identity_rule_does_not_instruct_manual_recall():
    """Identity rule must not tell the model to call recall_memory manually."""
    identity = (
        Path(__file__).parent.parent / "co_cli/prompts/rules/01_identity.md"
    ).read_text()
    assert "recall memories relevant" not in identity


def test_instructions_preamble_is_empty():
    """The instructions.md preamble must be empty — soul provides all identity."""
    instructions = (
        Path(__file__).parent.parent / "co_cli/prompts/instructions.md"
    ).read_text().strip()
    assert instructions == ""


def test_total_prompt_under_budget():
    """Static prompt with soul seed stays under budget."""
    for name in VALID_PERSONALITIES:
        seed = load_soul_seed(name)
        prompt, manifest = assemble_prompt("gemini", soul_seed=seed)
        assert manifest.total_chars < 8000, (
            f"Static prompt for '{name}' is {manifest.total_chars} chars, expected < 8000"
        )


# --- Strategy files ---


def test_all_roles_have_strategy_files():
    """Every role has all 6 strategy files."""
    strategies_base = (
        Path(__file__).parent.parent / "co_cli" / "prompts" / "personalities" / "strategies"
    )
    task_types = ["technical", "exploration", "debugging", "teaching", "emotional", "memory"]
    for name in VALID_PERSONALITIES:
        for task_type in task_types:
            f = strategies_base / name / f"{task_type}.md"
            assert f.exists(), f"Missing strategy: {name}/{task_type}.md"


def test_strategy_files_have_content():
    """Every strategy file has non-empty content."""
    strategies_base = (
        Path(__file__).parent.parent / "co_cli" / "prompts" / "personalities" / "strategies"
    )
    for strategy_file in strategies_base.rglob("*.md"):
        content = strategy_file.read_text(encoding="utf-8").strip()
        assert len(content) > 0, f"Empty strategy file: {strategy_file.name}"


def test_validate_personality_files_no_warnings_for_complete_role():
    """Built-in role with complete files returns no warnings."""
    assert validate_personality_files("finch") == []


def test_validate_personality_files_warns_on_missing_strategy(tmp_path, monkeypatch):
    """Missing strategy files return startup-safe warnings."""
    personalities_dir = tmp_path
    (personalities_dir / "souls" / "finch").mkdir(parents=True)
    (personalities_dir / "souls" / "finch" / "seed.md").write_text(
        "You are Finch.\n",
        encoding="utf-8",
    )
    (personalities_dir / "strategies" / "finch").mkdir(parents=True)
    for task_type in REQUIRED_STRATEGY_TASK_TYPES:
        if task_type == "memory":
            continue
        (personalities_dir / "strategies" / "finch" / f"{task_type}.md").write_text(
            f"{task_type} strategy",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "co_cli.prompts.personalities._composer._PERSONALITIES_DIR",
        personalities_dir,
    )

    warnings = validate_personality_files("finch")
    assert warnings == [
        "Personality 'finch' missing strategy file: strategies/finch/memory.md"
    ]
