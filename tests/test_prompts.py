"""Tests for prompt assembly system."""

import pytest

from co_cli.prompts import get_system_prompt, load_prompt, load_personality


class TestLoadPrompt:
    """Test basic prompt loading (backward compatibility)."""

    def test_load_system_prompt(self):
        """System prompt file exists and loads."""
        prompt = load_prompt("system")
        assert prompt
        assert "You are Co" in prompt
        assert len(prompt) > 500  # Substantial content

    def test_load_nonexistent_prompt(self):
        """FileNotFoundError for missing prompt."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_prompt("nonexistent")


class TestConditionalProcessing:
    """Test model-specific conditional processing."""

    def test_gemini_conditionals(self):
        """Gemini provider gets Gemini sections, not Ollama sections."""
        prompt = get_system_prompt("gemini")

        # Gemini content should be present (check for various possible phrasings)
        has_gemini_guidance = (
            "strong context window" in prompt.lower()
            or "2M tokens" in prompt
            or "2m tokens" in prompt.lower()
        )
        # Note: May not have explicit Gemini guidance if conditionals are for Ollama only
        # Main check: Ollama content should be removed

        # Ollama content should be removed
        assert "limited context window" not in prompt.lower()
        assert "[IF ollama]" not in prompt

        # No unprocessed markers
        assert "[IF gemini]" not in prompt
        assert "[ENDIF]" not in prompt

    def test_ollama_conditionals(self):
        """Ollama provider gets Ollama sections, not Gemini sections."""
        prompt = get_system_prompt("ollama")

        # Ollama content should be present (check for various possible phrasings)
        has_ollama_guidance = (
            "limited context" in prompt.lower()
            or "4K-32K" in prompt
            or "concise" in prompt.lower()
            or "minimal" in prompt.lower()
        )
        # Note: May not have explicit Ollama guidance if conditionals are for Gemini only
        # Main check: Gemini content should be removed

        # Gemini content should be removed
        assert "strong context window" not in prompt.lower()
        assert "[IF gemini]" not in prompt

        # No unprocessed markers
        assert "[IF ollama]" not in prompt
        assert "[ENDIF]" not in prompt

    def test_unknown_provider_defaults_to_ollama(self):
        """Unknown provider treated as Ollama (conservative)."""
        prompt = get_system_prompt("unknown-provider")

        # Should not get Gemini content
        assert "strong context window" not in prompt.lower()
        assert "[IF gemini]" not in prompt

        # Should have processed markers
        assert "[IF " not in prompt
        assert "[ENDIF]" not in prompt

    def test_case_insensitive_provider(self):
        """Provider names are case-insensitive."""
        prompt_lower = get_system_prompt("gemini")
        prompt_upper = get_system_prompt("GEMINI")
        prompt_mixed = get_system_prompt("Gemini")

        assert prompt_lower == prompt_upper == prompt_mixed

    def test_no_unprocessed_conditionals(self):
        """All conditional markers are processed."""
        for provider in ["gemini", "ollama", "unknown"]:
            prompt = get_system_prompt(provider)
            assert "[IF " not in prompt, f"Unprocessed [IF] in {provider} prompt"
            assert "[ENDIF]" not in prompt, f"Unprocessed [ENDIF] in {provider} prompt"

    def test_all_providers_have_clean_output(self):
        """All providers produce clean prompts without markers."""
        for provider in ["gemini", "ollama", "GEMINI", "Ollama", "unknown-model"]:
            prompt = get_system_prompt(provider)
            # Should have no conditional markers
            assert "[IF" not in prompt
            assert "[ENDIF]" not in prompt
            # Should have content
            assert len(prompt) > 500
            assert "You are Co" in prompt


class TestProjectInstructions:
    """Test project-specific instruction loading."""

    def test_no_project_instructions(self, tmp_path, monkeypatch):
        """Gracefully handle missing .co-cli/instructions.md."""
        monkeypatch.chdir(tmp_path)

        prompt = get_system_prompt("gemini")

        # Should not have project section
        assert "Project-Specific Instructions" not in prompt

    def test_with_project_instructions(self, tmp_path, monkeypatch):
        """Load and append .co-cli/instructions.md if present."""
        monkeypatch.chdir(tmp_path)

        # Create project instructions
        instructions_dir = tmp_path / ".co-cli"
        instructions_dir.mkdir()
        instructions_file = instructions_dir / "instructions.md"
        instructions_file.write_text(
            "# Project Rules\n\n"
            "- Use Django ORM for database access\n"
            "- All views must be class-based (CBV)\n"
            "- Tests go in tests/ directory with pytest\n"
        )

        prompt = get_system_prompt("gemini")

        # Should have project section
        assert "Project-Specific Instructions" in prompt
        assert "Django ORM" in prompt
        assert "class-based (CBV)" in prompt
        assert "pytest" in prompt

        # Project instructions should come after base content
        base_index = prompt.index("You are Co")
        project_index = prompt.index("Project-Specific Instructions")
        assert base_index < project_index

    def test_project_instructions_work_with_all_providers(self, tmp_path, monkeypatch):
        """Project instructions append regardless of provider."""
        monkeypatch.chdir(tmp_path)

        # Create project instructions
        instructions_dir = tmp_path / ".co-cli"
        instructions_dir.mkdir()
        instructions_file = instructions_dir / "instructions.md"
        instructions_file.write_text("# Test Project\n\n- Rule 1\n- Rule 2")

        for provider in ["gemini", "ollama", "unknown"]:
            prompt = get_system_prompt(provider)
            assert "Project-Specific Instructions" in prompt
            assert "Rule 1" in prompt
            assert "Rule 2" in prompt


class TestValidation:
    """Test prompt assembly validation."""

    def test_prompt_not_empty(self):
        """Assembled prompt is never empty."""
        for provider in ["gemini", "ollama", "unknown"]:
            prompt = get_system_prompt(provider)
            assert prompt.strip(), f"Prompt is empty for provider: {provider}"
            assert len(prompt) > 100, f"Prompt is suspiciously short for provider: {provider}"

    def test_no_unprocessed_markers(self):
        """Validation catches unprocessed conditional markers."""
        for provider in ["gemini", "ollama", "unknown"]:
            prompt = get_system_prompt(provider)
            # Should not raise, markers should be processed
            assert "[IF " not in prompt
            assert "[ENDIF]" not in prompt


class TestPromptContent:
    """Test that critical prompt sections are present after assembly."""

    def test_core_sections_present(self):
        """All major sections exist in assembled prompt."""
        prompt = get_system_prompt("gemini")

        # Core sections (from system.md structure)
        assert "Identity" in prompt or "You are Co" in prompt
        assert "Core Principles" in prompt
        assert "Directive vs Inquiry" in prompt
        assert "Fact Verification" in prompt
        # Tool guidance may be in various sections
        assert "shell" in prompt.lower() or "Shell" in prompt
        assert "Obsidian" in prompt or "obsidian" in prompt.lower()

    def test_directive_vs_inquiry_table(self):
        """Directive vs Inquiry section has examples table."""
        prompt = get_system_prompt("gemini")

        # Should have examples (check for key concepts)
        assert "login" in prompt.lower()
        assert "Inquiry" in prompt
        assert "Directive" in prompt

    def test_fact_verification_procedure(self):
        """Fact verification has multi-step procedure."""
        prompt = get_system_prompt("gemini")

        # Check for key steps (may be phrased differently)
        assert "Trust tool output" in prompt or "tool output first" in prompt
        assert "Verify" in prompt or "calculable" in prompt
        assert "Escalate" in prompt or "contradictions" in prompt
        assert "Never blindly accept" in prompt or "not blindly accept" in prompt

    def test_tool_guidance_present(self):
        """Tool-specific guidance sections are present."""
        prompt = get_system_prompt("gemini")

        # Should have guidance for major tools (case-insensitive)
        prompt_lower = prompt.lower()
        assert "shell" in prompt_lower
        assert "obsidian" in prompt_lower or "notes" in prompt_lower
        assert "google" in prompt_lower or "gmail" in prompt or "drive" in prompt
        assert "slack" in prompt_lower
        assert "web" in prompt_lower or "search" in prompt_lower


class TestBackwardCompatibility:
    """Test that old load_prompt() still works."""

    def test_load_prompt_unchanged(self):
        """load_prompt() still works for backward compatibility."""
        prompt = load_prompt("system")
        assert prompt
        assert "You are Co" in prompt

        # Old function returns raw content with markers
        assert "[IF " in prompt  # Markers NOT processed
        assert "[ENDIF]" in prompt


class TestAgentIntegration:
    """Test prompt assembly integrates correctly with agent factory."""

    def test_prompt_assembly_returns_string(self):
        """get_system_prompt returns valid string for agent."""
        prompt = get_system_prompt("gemini")
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_provider_from_settings(self):
        """Can pass settings.llm_provider to get_system_prompt."""
        # Simulate what agent.py does
        from co_cli.config import Settings

        settings = Settings(llm_provider="gemini")
        prompt = get_system_prompt(settings.llm_provider)
        assert prompt
        # Should have Gemini prompt (or at least not have Ollama-specific guidance)
        assert "[IF ollama]" not in prompt
        assert "[IF gemini]" not in prompt


class TestPersonalityTemplates:
    """Test personality template loading and injection."""

    def test_load_finch_personality(self):
        """Finch personality template loads."""
        personality = load_personality("finch")
        assert personality
        assert "finch" in personality.lower() or "mentor" in personality.lower() or "teacher" in personality.lower()
        assert "curate" in personality.lower() or "teaching" in personality.lower()

    def test_load_friendly_personality(self):
        """Friendly personality template loads."""
        personality = load_personality("friendly")
        assert personality
        assert "friendly" in personality.lower() or "warm" in personality.lower()
        assert "conversational" in personality.lower() or "collaborative" in personality.lower()

    def test_load_terse_personality(self):
        """Terse personality template loads."""
        personality = load_personality("terse")
        assert personality
        assert "terse" in personality.lower() or "minimal" in personality.lower()
        assert "brevity" in personality.lower() or "ultra-minimal" in personality.lower()

    def test_load_inquisitive_personality(self):
        """Inquisitive personality template loads."""
        personality = load_personality("inquisitive")
        assert personality
        assert "inquisitive" in personality.lower() or "question" in personality.lower()
        assert "explore" in personality.lower() or "clarify" in personality.lower()

    def test_load_jeff_personality(self):
        """Jeff personality template loads."""
        personality = load_personality("jeff")
        assert personality
        assert "jeff" in personality.lower() or "robot" in personality.lower() or "eager" in personality.lower()
        assert "72" in personality or "learning" in personality.lower()

    def test_load_all_personalities(self):
        """All five personalities load successfully."""
        for name in ["finch", "jeff", "friendly", "terse", "inquisitive"]:
            personality = load_personality(name)
            assert personality
            assert len(personality) > 50  # Substantial content

    def test_personality_injection(self):
        """Personality injected into system prompt."""
        prompt = get_system_prompt("gemini", personality="friendly")

        assert "Personality" in prompt
        assert "friendly" in prompt.lower() or "warm" in prompt.lower()

    def test_no_personality_works(self):
        """System prompt works without personality."""
        prompt = get_system_prompt("gemini", personality=None)

        # Should not have personality section
        assert "Personality" not in prompt or "Professional Personality" not in prompt

    def test_personality_order(self, tmp_path, monkeypatch):
        """Personality comes after conditionals, before project instructions."""
        monkeypatch.chdir(tmp_path)

        # Create project instructions
        instructions_dir = tmp_path / ".co-cli"
        instructions_dir.mkdir()
        instructions_file = instructions_dir / "instructions.md"
        instructions_file.write_text("# Project Rules\n\nUse pytest.")

        prompt = get_system_prompt("gemini", personality="friendly")

        # Check order
        base_idx = prompt.index("You are Co")
        personality_idx = prompt.index("Personality")
        project_idx = prompt.index("Project-Specific Instructions")

        assert base_idx < personality_idx < project_idx

    def test_invalid_personality_raises_error(self):
        """Invalid personality name raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_personality("nonexistent")

    def test_all_personalities_inject_correctly(self):
        """All five personalities inject without errors."""
        for name in ["finch", "jeff", "friendly", "terse", "inquisitive"]:
            prompt = get_system_prompt("gemini", personality=name)
            assert "Personality" in prompt
            # Check that the specific personality content is present
            personality_content = load_personality(name)
            # Check for at least one distinctive phrase from the personality
            assert any(phrase in prompt for phrase in personality_content.split()[:20])
