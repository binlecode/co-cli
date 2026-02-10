"""Phase 1d prompt engineering tests.

Tests for 5 high-impact prompt techniques:
1. System Reminder - Critical rules repeated at end (recency bias)
2. Escape Hatches - "unless explicitly requested" to prevent stuck states
3. Contrast Examples - Show both good AND bad responses
4. Model Quirk Counter-Steering - Database-driven behavior remediation
5. Commentary in Examples - Teach principles, not just patterns
"""

from co_cli.prompts import get_system_prompt
from co_cli.prompts.model_quirks import (
    get_counter_steering,
    get_quirk_flags,
    list_models_with_quirks,
)


class TestSystemReminder:
    """Verify Critical Rules section present at end of prompt (recency bias exploit)."""

    def test_system_reminder_present(self):
        """Critical Rules section must appear after Final Reminders."""
        prompt = get_system_prompt("gemini")

        # 1. Verify "Critical Rules" section exists
        assert "## Critical Rules" in prompt, "Missing Critical Rules section"

        # 2. Verify it appears after "Final Reminders"
        critical_pos = prompt.find("## Critical Rules")
        final_pos = prompt.find("## Final Reminders")
        assert (
            critical_pos > final_pos
        ), "Critical Rules must appear after Final Reminders (recency bias)"

        # 3. Verify all 3 critical rules present
        assert "Default to Inquiry unless explicit action verb present" in prompt
        assert "Show tool output verbatim when display field present" in prompt
        assert "Trust tool output over user assertion for deterministic facts" in prompt

        # 4. Verify emphasis text
        assert (
            "These rules have highest priority" in prompt
        ), "Missing priority emphasis"
        assert (
            "Re-read them before every response" in prompt
        ), "Missing recency instruction"


class TestEscapeHatches:
    """Verify 'unless explicitly requested' added to prohibitions."""

    def test_escape_hatches_present(self):
        """Both escape hatches must be present in system.md."""
        prompt = get_system_prompt("gemini")

        # 1. Tool output reformatting escape hatch (Line 125)
        assert (
            "Never reformat, summarize, or drop URLs from tool output unless the user explicitly requests"
            in prompt
        ), "Missing escape hatch for tool output reformatting"

        # 2. Fact verification escape hatch (Lines 82-86)
        assert (
            "If user insists after verification: Acknowledge disagreement, proceed with user's preference"
            in prompt
        ), "Missing escape hatch for fact verification"

    def test_escape_hatch_prevents_stuck_state(self):
        """Escape hatches should allow proceeding when user explicitly requests."""
        prompt = get_system_prompt("gemini")

        # Original prohibition: "Never reformat"
        # With escape hatch: "Never reformat... unless user explicitly requests"
        # Verify both parts present
        assert "Never reformat" in prompt, "Original prohibition missing"
        assert (
            "unless the user explicitly requests" in prompt
        ), "Escape hatch missing"


class TestContrastExamples:
    """Verify 'Common mistakes' table and commentary present."""

    def test_contrast_examples_present(self):
        """Common mistakes table must show both wrong and correct classifications."""
        prompt = get_system_prompt("gemini")

        # 1. Verify section header
        assert (
            "**Common mistakes (what NOT to do):**" in prompt
        ), "Missing contrast examples section"

        # 2. Verify table has required columns
        assert "Wrong Classification" in prompt, "Missing 'Wrong Classification' column"
        assert "Why Wrong" in prompt, "Missing 'Why Wrong' column"
        assert "Correct Response" in prompt, "Missing 'Correct Response' column"

        # 3. Verify all 5 examples present
        contrast_examples = [
            "This function has a bug",
            "The API is slow",
            "Check if tests pass",
            "What if we added caching?",
            "The README could mention X",
        ]
        for example in contrast_examples:
            assert (
                example in prompt
            ), f"Missing contrast example: '{example}' in Common mistakes table"

    def test_commentary_present(self):
        """Verify 'Why these distinctions matter' commentary teaches principles."""
        prompt = get_system_prompt("gemini")

        # 1. Verify commentary header
        assert (
            "**Why these distinctions matter:**" in prompt
        ), "Missing commentary section"

        # 2. Verify all 5 principles explained
        principles = [
            "Observation ≠ Directive",
            "Hypotheticals ≠ Directives",
            "Questions ≠ Implementation Requests",
            "Action verbs are the primary signal",
            "Default to Inquiry when ambiguous",
        ]
        for principle in principles:
            assert principle in prompt, f"Missing principle: '{principle}'"

        # 3. Verify each principle has examples
        # Principle 1: Observation ≠ Directive
        assert (
            "Why does login fail?" in prompt and "Fix the login bug" in prompt
        ), "Principle 1 missing examples"

        # Principle 2: Hypotheticals ≠ Directives
        assert (
            "The API returns 500 errors" in prompt and "Update API to return 200" in prompt
        ), "Principle 2 missing examples"

        # Principle 3: Questions ≠ Implementation
        assert (
            "How does authentication work?" in prompt
            and "Add authentication to /api/users" in prompt
        ), "Principle 3 missing examples"

        # Principle 4: Action verbs
        assert (
            "fix" in prompt and "add" in prompt and "update" in prompt
        ), "Principle 4 missing verb examples"

        # Principle 5: Default to Inquiry
        assert (
            "False negative" in prompt and "False positive" in prompt
        ), "Principle 5 missing risk analysis"

    def test_contrast_placement(self):
        """Contrast examples must appear before 'When uncertain' line."""
        prompt = get_system_prompt("gemini")

        # Find positions
        contrast_pos = prompt.find("**Common mistakes (what NOT to do):**")
        uncertain_pos = prompt.find("**When uncertain:**")

        # Verify order
        assert (
            contrast_pos > 0
        ), "Contrast examples section not found (check markdown formatting)"
        assert uncertain_pos > 0, "'When uncertain' line not found"
        assert (
            contrast_pos < uncertain_pos
        ), "Contrast examples must appear before 'When uncertain' line"


class TestModelQuirkInjection:
    """Verify model-specific counter-steering injected correctly."""

    def test_model_quirk_injection(self):
        """Known models should get counter-steering text."""
        # Test known model (overeager - glm-4.7-flash)
        prompt_glm = get_system_prompt("ollama", None, "glm-4.7-flash")
        assert (
            "## Model-Specific Guidance" in prompt_glm
        ), "Missing model guidance section for known model"
        assert (
            "modify code" in prompt_glm.lower()
        ), "Missing overeager counter-steering"

        # Test known model (hesitant - llama3.1)
        prompt_llama31 = get_system_prompt("ollama", None, "llama3.1")
        assert (
            "## Model-Specific Guidance" in prompt_llama31
        ), "Missing model guidance section for llama3.1"
        assert (
            "confident and decisive" in prompt_llama31.lower()
        ), "Missing hesitant counter-steering"

        # Test unknown model (no injection)
        prompt_unknown = get_system_prompt("gemini", None, "unknown-model")
        assert (
            "## Model-Specific Guidance" not in prompt_unknown
        ), "Unknown model should not get model guidance"

    def test_model_quirks_database(self):
        """Verify all 2 expected models present in quirks database."""
        models = list_models_with_quirks()

        # Verify count
        assert len(models) == 2, f"Expected 2 models, got {len(models)}: {models}"

        # Verify all expected models present
        expected_models = [
            "ollama:llama3.1",
            "ollama:glm-4.7-flash",
        ]
        for model in expected_models:
            assert model in models, f"Missing expected model: {model}"

    def test_quirk_flags(self):
        """Verify quirk flag lookups work correctly."""
        # Hesitant model (llama3.1)
        flags_llama31 = get_quirk_flags("ollama", "llama3.1")
        assert flags_llama31["hesitant"] is True, "llama3.1 should be hesitant"
        assert flags_llama31["verbose"] is False, "llama3.1 should not be verbose"
        assert flags_llama31["lazy"] is False, "llama3.1 should not be lazy"
        assert flags_llama31["overeager"] is False, "llama3.1 should not be overeager"

        # Overeager model (glm-4.7-flash)
        flags_glm = get_quirk_flags("ollama", "glm-4.7-flash")
        assert flags_glm["overeager"] is True, "glm-4.7-flash should be overeager"
        assert flags_glm["verbose"] is False, "glm-4.7-flash should not be verbose"
        assert flags_glm["lazy"] is False, "glm-4.7-flash should not be lazy"
        assert flags_glm["hesitant"] is False, "glm-4.7-flash should not be hesitant"

        # Unknown model (all False)
        flags_unknown = get_quirk_flags("unknown", "unknown")
        assert all(
            not v for v in flags_unknown.values()
        ), "Unknown model should have all False flags"

    def test_counter_steering_content(self):
        """Verify counter-steering prompts contain expected keywords."""
        # Overeager models should mention key phrases
        cs_glm = get_counter_steering("ollama", "glm-4.7-flash")
        assert (
            "critical" in cs_glm.lower() or "modify code" in cs_glm.lower()
        ), "Overeager counter-steering missing key phrases"

        # Hesitant models should mention "confident"
        cs_llama31 = get_counter_steering("ollama", "llama3.1")
        assert (
            "confident" in cs_llama31.lower()
        ), "Hesitant counter-steering missing 'confident'"

        # Unknown models should return empty string
        cs_unknown = get_counter_steering("ollama", "unknown-model")
        assert cs_unknown == "", "Unknown model should return empty counter-steering"

    def test_backward_compatibility(self):
        """Verify prompt assembly works without model_name parameter."""
        # Original 2-param call should still work
        prompt_old = get_system_prompt("gemini", "friendly")
        assert len(prompt_old) > 0, "2-param call should still work"
        assert (
            "Model-Specific Guidance" not in prompt_old
        ), "2-param call should not inject model guidance"

        # 3-param call with None should also not inject
        prompt_none = get_system_prompt("gemini", "friendly", None)
        assert len(prompt_none) > 0, "3-param call with None should work"
        assert (
            "Model-Specific Guidance" not in prompt_none
        ), "model_name=None should not inject guidance"


class TestIntegration:
    """Integration tests verifying all 5 techniques work together."""

    def test_all_techniques_present(self):
        """Verify all 5 Phase 1d techniques present in final prompt."""
        prompt = get_system_prompt("ollama", None, "glm-4.7-flash")

        # 1. System Reminder
        assert (
            "## Critical Rules" in prompt
        ), "System Reminder technique missing (Critical Rules)"

        # 2. Escape Hatches
        assert (
            "unless the user explicitly requests" in prompt
        ), "Escape Hatch technique missing"

        # 3. Contrast Examples
        assert (
            "**Common mistakes (what NOT to do):**" in prompt
        ), "Contrast Examples technique missing"

        # 4. Model Quirk Counter-Steering
        assert (
            "## Model-Specific Guidance" in prompt
        ), "Model Quirk Counter-Steering technique missing"

        # 5. Commentary in Examples
        assert (
            "**Why these distinctions matter:**" in prompt
        ), "Commentary technique missing"

    def test_section_ordering(self):
        """Verify sections appear in correct order."""
        prompt = get_system_prompt("ollama", "friendly", "llama3.1")

        # Find section positions
        directive_pos = prompt.find("## Directive vs Inquiry")
        contrast_pos = prompt.find("**Common mistakes (what NOT to do):**")
        final_pos = prompt.find("## Final Reminders")
        critical_pos = prompt.find("## Critical Rules")
        personality_pos = prompt.find("## Personality")
        model_pos = prompt.find("## Model-Specific Guidance")

        # Verify ordering
        assert (
            directive_pos < contrast_pos
        ), "Contrast examples should follow Directive section"
        assert (
            final_pos < critical_pos
        ), "Critical Rules should follow Final Reminders"
        assert (
            personality_pos < model_pos
        ), "Model guidance should follow Personality"

    def test_no_regressions(self):
        """Verify Phase 1d changes don't break existing prompt structure."""
        prompt = get_system_prompt("gemini")

        # Core sections still present
        assert "## Directive vs Inquiry" in prompt, "Core section missing"
        assert "## Fact Verification" in prompt, "Core section missing"
        assert "## Tool Output Handling" in prompt, "Core section missing"
        assert "## Final Reminders" in prompt, "Core section missing"

        # Conditional processing still works
        assert "[IF ollama]" not in prompt, "Unprocessed Ollama conditional"
        assert "[IF gemini]" not in prompt, "Unprocessed Gemini conditional"
        assert "[ENDIF]" not in prompt, "Unprocessed ENDIF marker"
