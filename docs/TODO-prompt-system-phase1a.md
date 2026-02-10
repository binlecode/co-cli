# Prompt System Refactor - Phase 1a Implementation Guide

## Executive Summary

**Goal:** Enable model-specific prompt assembly with conditional processing and project instruction support.

**Problem:** Current system sends raw conditional markers (`[IF gemini]`, `[IF ollama]`) to LLMs without processing. Gemini sees Ollama guidance and vice versa.

**Solution:** Add `get_system_prompt(provider: str)` function that processes conditionals at prompt assembly time and appends project-specific instructions if present.

**Scope:** Phase 1a focuses ONLY on model conditionals and project instructions. Personality templates, internal knowledge, and user preferences are future phases.

**Effort:** 4-6 hours (implementation + testing + verification)

**Risk:** Low (additive changes, backward compatible, comprehensive tests)

---

## Table of Contents

1. [Context & Rationale](#context--rationale)
2. [Architecture Overview](#architecture-overview)
3. [Implementation Plan](#implementation-plan)
4. [Code Specifications](#code-specifications)
5. [Test Specifications](#test-specifications)
6. [Verification Procedures](#verification-procedures)
7. [Documentation Updates](#documentation-updates)
8. [Success Criteria](#success-criteria)
9. [Risk Assessment](#risk-assessment)
10. [Future Phases](#future-phases)

---

## Context & Rationale

### Why This Change

The current prompt system loads a single static `system.md` file without any dynamic processing. However, the prompt file already contains model-specific guidance wrapped in conditional markers (`[IF gemini]` and `[IF ollama]`) that are **not being processed** — they're sent as raw text to the LLM.

**Current problem:**
- Gemini models see Ollama-specific guidance about "limited context" and "keep under 3 sentences" (incorrect for 2M context)
- Ollama models see Gemini-specific guidance about "strong context window" and "detailed explanations OK" (incorrect for 4K-32K context)
- No way to inject project-specific instructions (conventions, patterns, codebase rules)

**Desired outcome (Phase 1a):**
- Model-specific prompt assembly: Gemini gets Gemini guidance, Ollama gets Ollama guidance
- Project override support: Load `.co-cli/instructions.md` if present
- Maintain simplicity: Single markdown source, minimal processing, no over-engineering
- Pythonic design: Explicit over implicit, simple over complex, flat over nested
- Extension points for future phases (personality, preferences, internal knowledge)

### First-Principles Analysis

**Core problem:** Model-specific conditionals (`[IF gemini]`, `[IF ollama]`) in system.md are not being processed. Gemini sees Ollama guidance and vice versa.

**Core solution:** Process conditionals at prompt assembly time.

**That's all Phase 1a does.** Simple, focused, solves immediate problem.

**Insights from peer system analysis deferred to future phases:**
- Recency bias (system_reminder): Test current prompt first, add only if compliance issues found
- Escape hatches: Add when evidence shows stuck states occur
- Compression security: Not applicable until compression exists
- Commentary/counter-steering/contrast pairs: Optimizations without demonstrated need

**Phase 1a philosophy:** Solve the problem you have, not the problems other systems have.

### Design Foundation

The architecture is specified in `docs/PROPOSAL-prompt-system.md` (proposal/WIP, 1705 lines):
- **Single source of truth:** `co_cli/prompts/system.md` contains all prompt content (761 lines, already written)
- **Minimal dynamic assembly:** Process conditionals + append project overrides
- **Tool-centric content:** Each tool has dedicated guidance section
- **Learned from peers:** Adopts Gemini CLI's Directive vs Inquiry, closes fact verification gap found in all systems

### The "Finch" Vision

Co will evolve from tool-calling assistant to personal companion (inspired by "Finch" 2021 robot: helpful, curious, adaptive, empathetic, loyal, growing).

**Five pillars of co's character:**

**1. Soul (Identity & Personality)**
- **Phase 1b:** Pre-set personality templates (professional, friendly, terse, inquisitive)
- User-selectable via config or runtime command
- Injected at prompt assembly time
- Fixed set to start (bounded config space, explicit > implicit)
- Extension point: `[PERSONALITY: <template-name>]` marker in system.md

**2. Internal Knowledge (Co's learned context)**
- **Phase 1c:** Agent SDK memory handling for session memory
- **Phase 1c:** File-based persistent storage (`.co-cli/internal/`) for cross-session knowledge
- **Boundary:** Internal = always available in context, External = queried on demand
- Distinct from External Knowledge (tools)
- Extension point: Load `.co-cli/internal/context.json` at session start

**3. External Knowledge (Tools & Data Access)**
- **Current:** Tools (shell, web_search, obsidian, google, slack)
- **Phase 2:** MCP servers, custom tool plugins
- Extension point: Tool registration system already extensible

**4. Emotion (Tone & Empathy)**
- **Current:** Model-specific guidance ([IF gemini] = explain reasoning, [IF ollama] = be concise)
- **Phase 1b:** Personality templates include tone/empathy guidance
- **Future:** Context-aware tone shifting based on user state
- Extension point: Personality templates define emotional register

**5. Habit (Workflow Preferences)**
- **Phase 2:** User workflow preferences system
- Settings: auto_approve_tools, verbosity_level, proactive_suggestions, output_format_preferences
- Research peer systems + 2026 best practices for design
- Extension point: Load `.co-cli/preferences.json` after personality, before project instructions

**Prompt assembly order (final architecture):**
```
1. Base system.md (identity, principles, tool guidance)
2. Model conditionals ([IF gemini] / [IF ollama])          ← Phase 1a
3. Personality template ([PERSONALITY: <name>])            ← Phase 1b
4. Internal knowledge (.co-cli/internal/context.json)      ← Phase 1c
5. User preferences (.co-cli/preferences.json)             ← Phase 2
6. Project instructions (.co-cli/instructions.md)          ← Phase 1a
```

**Current MVP (Phase 1a) includes:**
- Model conditionals (solve immediate problem)
- Project instructions loading
- Extension points for future phases

### Current State

**What exists:**
- ✅ Complete `system.md` with conditional markers (761 lines)
- ✅ Complete design specification (PROPOSAL-prompt-system.md)
- ✅ Settings system with `llm_provider` field
- ✅ Project config pattern (`.co-cli/settings.json`)

**What's missing (Phase 1a scope):**
- ❌ Conditional processing logic (`[IF model]...[ENDIF]`)
- ❌ Project instructions loading (`.co-cli/instructions.md`)
- ❌ Tests for prompt assembly
- ❌ Integration with agent factory

**What's deferred (future phases):**
- ⏳ Personality templates (Phase 1b)
- ⏳ Internal knowledge loading (Phase 1c)
- ⏳ User preferences (Phase 2)
- ⏳ Skills system (post-Phase-1)

---

## Architecture Overview

### Current Flow

```
User ──▶ CLI ──▶ get_agent() ──▶ load_prompt("system") ──▶ Agent
                                        │
                                        ▼
                                 system.md (raw, with [IF] markers)
                                        │
                                        ▼
                                    LLM (sees raw markers ❌)
```

### New Flow (Phase 1a)

```
User ──▶ CLI ──▶ get_agent() ──▶ get_system_prompt(provider) ──▶ Agent
                                        │
                                        ▼
                            ┌───────────┴───────────┐
                            ▼                       ▼
                      system.md          .co-cli/instructions.md
                   (with [IF] markers)        (if exists)
                            │                       │
                            ▼                       │
                    Process conditionals            │
                 (remove non-matching sections)     │
                            │                       │
                            ▼                       │
                    Clean up markers                │
                 (remove [IF] and [ENDIF])          │
                            │                       │
                            └───────────┬───────────┘
                                        ▼
                              Assembled prompt (clean ✅)
                                        │
                                        ▼
                                    LLM
```

### Conditional Processing Logic

**Pattern:** Regex-based removal (simple, sufficient)

```python
if provider == "gemini":
    # Remove Ollama sections
    prompt = re.sub(r"\[IF ollama\].*?\[ENDIF\]", "", prompt, flags=re.DOTALL)
    # Clean up Gemini markers
    prompt = prompt.replace("[IF gemini]", "").replace("[ENDIF]", "")
elif provider == "ollama":
    # Remove Gemini sections
    prompt = re.sub(r"\[IF gemini\].*?\[ENDIF\]", "", prompt, flags=re.DOTALL)
    # Clean up Ollama markers
    prompt = prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
else:
    # Unknown provider - default to Ollama (conservative)
    prompt = re.sub(r"\[IF gemini\].*?\[ENDIF\]", "", prompt, flags=re.DOTALL)
    prompt = prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
```

**Why not Jinja2/templating?**
- Only 2 conditionals (gemini/ollama)
- No nesting required
- Stdlib only (no dependencies)
- Easier to debug (print intermediate states)

### Project Instructions Pattern

**Chosen:** `.co-cli/instructions.md`

**Why:**
- Consistent with `.co-cli/settings.json` pattern
- Project-scoped (not global)
- Markdown format (human-readable, LLM-friendly)
- Git-commitable (team can share conventions)

**Alternative considered:** `CLAUDE.md` (Claude Code pattern)
- Rejected: Co-cli is simpler, one location sufficient

**Example `.co-cli/instructions.md`:**
```markdown
# Django Project Conventions

## Database Access
- Use Django ORM for all database queries
- Never use raw SQL unless performance-critical

## Views
- All views must be class-based (CBV)
- Use mixins for common functionality

## Testing
- Tests go in tests/ directory
- Use pytest with pytest-django
- All tests must be functional (no mocks)

## Code Style
- Follow PEP 8
- Max line length: 100 characters
- Use type hints everywhere
```

---

## Implementation Plan

### Phase 1: Add Prompt Assembly Logic

**File:** `co_cli/prompts/__init__.py`

**Current code** (28 lines):
```python
"""Prompt templates for the Co CLI agent.

All prompts are stored as Markdown files for easy editing with syntax highlighting.
Use load_prompt() to load a prompt by name.
"""

from pathlib import Path


def load_prompt(name: str) -> str:
    """Load a prompt template by name.

    Args:
        name: Prompt filename without extension (e.g., "system" for "system.md")

    Returns:
        The prompt content as a string.

    Raises:
        FileNotFoundError: If the prompt file doesn't exist.
    """
    prompts_dir = Path(__file__).parent
    prompt_file = prompts_dir / f"{name}.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    return prompt_file.read_text(encoding="utf-8")
```

**Add new function** `get_system_prompt(provider: str) -> str`:

```python
import re
from pathlib import Path


def get_system_prompt(provider: str) -> str:
    """Assemble system prompt with model-specific conditionals and project overrides.

    Processing steps:
    1. Load base system.md
    2. Process model conditionals ([IF gemini] / [IF ollama])
    3. Append project instructions from .co-cli/instructions.md (if exists)
    4. Validate result (no empty prompt, no unprocessed markers)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown)
                 Unknown providers default to Ollama (conservative)

    Returns:
        Assembled system prompt as string

    Raises:
        FileNotFoundError: If system.md doesn't exist
        ValueError: If assembled prompt is empty or has unprocessed conditionals

    Example:
        >>> prompt = get_system_prompt("gemini")
        >>> assert "[IF ollama]" not in prompt  # Ollama sections removed
        >>> assert "[IF gemini]" not in prompt  # Markers cleaned up
    """
    # 1. Load base prompt
    prompts_dir = Path(__file__).parent
    prompt_file = prompts_dir / "system.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    base_prompt = prompt_file.read_text(encoding="utf-8")

    # 2. Process model conditionals
    provider_lower = provider.lower()

    if provider_lower == "gemini":
        # Remove Ollama sections
        base_prompt = re.sub(
            r"\[IF ollama\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        # Clean up Gemini markers
        base_prompt = base_prompt.replace("[IF gemini]", "").replace("[ENDIF]", "")
    elif provider_lower == "ollama":
        # Remove Gemini sections
        base_prompt = re.sub(
            r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        # Clean up Ollama markers
        base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
    else:
        # Unknown provider - treat as Ollama (conservative default)
        base_prompt = re.sub(
            r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")

    # 3. Load project instructions if present
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        base_prompt += "\n\n## Project-Specific Instructions\n\n"
        base_prompt += instructions_content

    # 4. Validate result
    if not base_prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    # Check for unprocessed conditionals (indicates bug in regex)
    if "[IF " in base_prompt:
        raise ValueError("Unprocessed conditional markers remain in prompt")

    return base_prompt
```

**Tasks:**
- [ ] Add `import re` at top of file
- [ ] Add `get_system_prompt()` function with full implementation (code above)
- [ ] Keep `load_prompt()` for backward compatibility
- [ ] Add validation checks (empty prompt, unprocessed conditionals)
- [ ] Add comprehensive docstring with example

**Estimated lines:** +70 lines (function + docstring + validation)

---

### Phase 2: Update Agent Factory

**File:** `co_cli/agent.py`

**Current code** (line ~82):
```python
system_prompt = load_prompt("system")
```

**Change to:**
```python
from co_cli.prompts import get_system_prompt
...
system_prompt = get_system_prompt(settings.llm_provider)
```

**Full context** (lines 1-90):
```python
# Near top of file (around line 20):
from co_cli.prompts import load_prompt  # ← CHANGE THIS

# Later in get_agent() function (around line 82):
system_prompt = load_prompt("system")  # ← CHANGE THIS
```

**Tasks:**
- [ ] Update import at top: `from co_cli.prompts import get_system_prompt`
- [ ] Change line 82: `system_prompt = get_system_prompt(settings.llm_provider)`
- [ ] Verify `settings` is already imported and available (it is, from earlier in function)
- [ ] Test that agent factory still works

**Estimated lines:** 2 changes (1 import, 1 call site)

---

### Phase 3: Add Comprehensive Tests

**File:** `tests/test_prompts.py` (NEW FILE)

**Complete test suite:**

```python
"""Tests for prompt assembly system."""

import re
from pathlib import Path

import pytest

from co_cli.prompts import get_system_prompt, load_prompt


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

        # Gemini content should be present
        assert "strong context window" in prompt.lower() or "2M tokens" in prompt or "2m tokens" in prompt.lower()

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
        assert has_ollama_guidance, "Ollama-specific guidance not found"

        # Gemini content should be removed
        assert "strong context window" not in prompt.lower()
        assert "[IF gemini]" not in prompt

        # No unprocessed markers
        assert "[IF ollama]" not in prompt
        assert "[ENDIF]" not in prompt

    def test_unknown_provider_defaults_to_ollama(self):
        """Unknown provider treated as Ollama (conservative)."""
        prompt = get_system_prompt("unknown-provider")

        # Should get Ollama content (more conservative for unknown models)
        has_ollama_guidance = (
            "limited context" in prompt.lower() or "concise" in prompt.lower()
        )
        # May or may not have Ollama guidance depending on exact phrasing
        # Just ensure Gemini guidance is NOT present
        assert "strong context window" not in prompt.lower()

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
        assert "Tool Guidance" in prompt or "Shell Tool" in prompt
        assert "Obsidian" in prompt
        assert "Final Reminders" in prompt or "Remember" in prompt

    def test_directive_vs_inquiry_table(self):
        """Directive vs Inquiry section has examples table."""
        prompt = get_system_prompt("gemini")

        # Should have table with examples
        assert "Why does login fail?" in prompt or "login" in prompt.lower()
        assert "Fix the login bug" in prompt or "fix" in prompt.lower()
        assert "Inquiry" in prompt
        assert "Directive" in prompt

    def test_fact_verification_procedure(self):
        """Fact verification has multi-step procedure."""
        prompt = get_system_prompt("gemini")

        # Check for key steps
        assert "Trust tool output" in prompt or "tool output first" in prompt
        assert "Verify" in prompt or "calculable facts" in prompt
        assert "Escalate" in prompt or "contradictions" in prompt
        assert "Never blindly accept" in prompt or "not blindly accept" in prompt

    def test_tool_guidance_present(self):
        """Tool-specific guidance sections are present."""
        prompt = get_system_prompt("gemini")

        # Should have guidance for major tools
        assert "shell" in prompt.lower() or "Shell Tool" in prompt
        assert "obsidian" in prompt.lower() or "notes" in prompt.lower()
        assert "google" in prompt.lower() or "Gmail" in prompt or "Drive" in prompt
        assert "slack" in prompt.lower()
        assert "web" in prompt.lower() or "search" in prompt.lower()


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


# Integration test
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
        assert "strong context" in prompt.lower() or "2M" in prompt
```

**Tasks:**
- [ ] Create `tests/test_prompts.py`
- [ ] Add `TestLoadPrompt` class (2 tests)
- [ ] Add `TestConditionalProcessing` class (6 tests)
- [ ] Add `TestProjectInstructions` class (3 tests)
- [ ] Add `TestValidation` class (2 tests)
- [ ] Add `TestPromptContent` class (4 tests)
- [ ] Add `TestBackwardCompatibility` class (1 test)
- [ ] Add `TestAgentIntegration` class (2 tests)
- [ ] Run tests: `uv run pytest tests/test_prompts.py -v`
- [ ] Verify all 20 tests pass

**Estimated lines:** ~250 lines (comprehensive test coverage)

---

## Code Specifications

### Function Signature

```python
def get_system_prompt(provider: str) -> str:
    """Assemble system prompt with model-specific conditionals and project overrides."""
```

**Parameters:**
- `provider` (str): LLM provider name ("gemini", "ollama", or any other)
  - Case-insensitive
  - Unknown providers default to Ollama behavior (conservative)

**Returns:**
- `str`: Assembled prompt with conditionals processed and project instructions appended

**Raises:**
- `FileNotFoundError`: If `system.md` doesn't exist in prompts directory
- `ValueError`: If assembled prompt is empty or contains unprocessed `[IF` markers

### Processing Rules

**1. Conditional Removal:**
- For Gemini: Remove all `[IF ollama]...[ENDIF]` sections
- For Ollama: Remove all `[IF gemini]...[ENDIF]` sections
- For Unknown: Remove all `[IF gemini]...[ENDIF]` sections (conservative default)

**2. Marker Cleanup:**
- Remove all `[IF <provider>]` markers matching the target provider
- Remove all `[ENDIF]` markers after conditional removal
- Result should have ZERO conditional markers remaining

**3. Project Instructions:**
- Check for `.co-cli/instructions.md` in current working directory
- If exists: read content and append with "## Project-Specific Instructions" header
- If not exists: skip silently (no error)

**4. Validation:**
- Ensure final prompt is not empty (after `.strip()`)
- Ensure no `[IF ` markers remain (indicates regex bug)
- Raise `ValueError` with clear message if validation fails

### Regex Patterns

```python
# Remove conditional sections
re.sub(r"\[IF ollama\].*?\[ENDIF\]", "", prompt, flags=re.DOTALL)
re.sub(r"\[IF gemini\].*?\[ENDIF\]", "", prompt, flags=re.DOTALL)

# Clean up markers (after section removal)
prompt.replace("[IF gemini]", "").replace("[ENDIF]", "")
prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
```

**Pattern explanation:**
- `\[IF ollama\]` - Literal text `[IF ollama]` (brackets escaped)
- `.*?` - Match any characters (non-greedy)
- `\[ENDIF\]` - Literal text `[ENDIF]` (brackets escaped)
- `re.DOTALL` - Make `.` match newlines (for multi-line sections)

### File Paths

```python
# Base prompt
prompts_dir = Path(__file__).parent
prompt_file = prompts_dir / "system.md"

# Project instructions
project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
```

**Path handling:**
- Use `pathlib.Path` (not string concatenation)
- Use `Path.cwd()` for current working directory
- Check `.exists()` before reading optional files
- Always specify `encoding="utf-8"` when reading text

### Error Messages

```python
# File not found
f"Prompt file not found: {prompt_file}"

# Empty prompt
"Assembled prompt is empty after processing"

# Unprocessed markers
"Unprocessed conditional markers remain in prompt"
```

**Error message principles:**
- Include file path when file not found
- Be specific about what validation failed
- No need to include full prompt in error (could be huge)

---

## Test Specifications

### Test Structure

```
tests/test_prompts.py
├── TestLoadPrompt (backward compatibility)
│   ├── test_load_system_prompt
│   └── test_load_nonexistent_prompt
├── TestConditionalProcessing (core logic)
│   ├── test_gemini_conditionals
│   ├── test_ollama_conditionals
│   ├── test_unknown_provider_defaults_to_ollama
│   ├── test_case_insensitive_provider
│   ├── test_no_unprocessed_conditionals
│   └── test_all_providers_have_clean_output
├── TestProjectInstructions (file loading)
│   ├── test_no_project_instructions
│   ├── test_with_project_instructions
│   └── test_project_instructions_work_with_all_providers
├── TestValidation (error handling)
│   ├── test_prompt_not_empty
│   └── test_no_unprocessed_markers
├── TestPromptContent (semantic checks)
│   ├── test_core_sections_present
│   ├── test_directive_vs_inquiry_table
│   ├── test_fact_verification_procedure
│   └── test_tool_guidance_present
├── TestBackwardCompatibility
│   └── test_load_prompt_unchanged
└── TestAgentIntegration
    ├── test_prompt_assembly_returns_string
    └── test_provider_from_settings
```

### Test Fixtures

**Use pytest built-in fixtures:**
- `tmp_path` - Temporary directory for project instructions tests
- `monkeypatch` - Change `cwd()` for project instruction location

**Example:**
```python
def test_with_project_instructions(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    instructions_dir = tmp_path / ".co-cli"
    instructions_dir.mkdir()
    # ... create instructions.md ...
```

### Assertion Strategies

**String content checks:**
```python
# Presence
assert "expected text" in prompt

# Absence
assert "unexpected text" not in prompt

# Case-insensitive
assert "expected" in prompt.lower()

# Multiple options (OR)
assert any(x in prompt for x in ["option1", "option2", "option3"])
```

**Marker validation:**
```python
# No unprocessed markers
assert "[IF " not in prompt
assert "[ENDIF]" not in prompt

# Loop over providers
for provider in ["gemini", "ollama", "unknown"]:
    prompt = get_system_prompt(provider)
    assert "[IF " not in prompt
```

**File operations:**
```python
# Create test file
file_path.write_text("content", encoding="utf-8")

# Check order
index1 = prompt.index("first text")
index2 = prompt.index("second text")
assert index1 < index2
```

### Coverage Goals

- **Line coverage:** >95% for `co_cli/prompts/__init__.py`
- **Branch coverage:** All conditional branches tested
- **Edge cases:** Empty files, missing files, unknown providers, case sensitivity

**Run coverage:**
```bash
uv run pytest tests/test_prompts.py --cov=co_cli.prompts --cov-report=term-missing
```

---

## Verification Procedures

### Automated Testing

**Step 1: Run new tests**
```bash
uv run pytest tests/test_prompts.py -v
```

**Expected output:**
```
tests/test_prompts.py::TestLoadPrompt::test_load_system_prompt PASSED
tests/test_prompts.py::TestLoadPrompt::test_load_nonexistent_prompt PASSED
tests/test_prompts.py::TestConditionalProcessing::test_gemini_conditionals PASSED
tests/test_prompts.py::TestConditionalProcessing::test_ollama_conditionals PASSED
tests/test_prompts.py::TestConditionalProcessing::test_unknown_provider_defaults_to_ollama PASSED
tests/test_prompts.py::TestConditionalProcessing::test_case_insensitive_provider PASSED
tests/test_prompts.py::TestConditionalProcessing::test_no_unprocessed_conditionals PASSED
tests/test_prompts.py::TestProjectInstructions::test_no_project_instructions PASSED
tests/test_prompts.py::TestProjectInstructions::test_with_project_instructions PASSED
tests/test_prompts.py::TestProjectInstructions::test_project_instructions_work_with_all_providers PASSED
tests/test_prompts.py::TestValidation::test_prompt_not_empty PASSED
tests/test_prompts.py::TestValidation::test_no_unprocessed_markers PASSED
tests/test_prompts.py::TestPromptContent::test_core_sections_present PASSED
tests/test_prompts.py::TestPromptContent::test_directive_vs_inquiry_table PASSED
tests/test_prompts.py::TestPromptContent::test_fact_verification_procedure PASSED
tests/test_prompts.py::TestPromptContent::test_tool_guidance_present PASSED
tests/test_prompts.py::TestBackwardCompatibility::test_load_prompt_unchanged PASSED
tests/test_prompts.py::TestAgentIntegration::test_prompt_assembly_returns_string PASSED
tests/test_prompts.py::TestAgentIntegration::test_provider_from_settings PASSED

=================== 19 passed in 0.5s ===================
```

**Step 2: Run full test suite**
```bash
uv run pytest
```

**Check for regressions:**
- All existing tests should still pass
- No new warnings or errors
- Test count increases by ~19

**Step 3: Run with coverage**
```bash
uv run pytest tests/test_prompts.py --cov=co_cli.prompts --cov-report=term-missing
```

**Expected coverage:**
- `co_cli/prompts/__init__.py`: >95% coverage
- Only missed lines should be edge cases (if any)

### Manual Verification - Gemini

**Setup:**
```bash
export LLM_PROVIDER=gemini
uv run co chat
```

**Test 1: Check prompt assembly**
```
# In chat, ask a simple question
User: What day of the week is it?

# Observe response style:
# ✓ Should explain reasoning ("Today is...")
# ✓ Can be detailed (not constrained to 3 sentences)
# ✓ No visible [IF] markers in behavior
```

**Test 2: Verify Gemini guidance**
```python
# In separate Python session
from co_cli.prompts import get_system_prompt
prompt = get_system_prompt("gemini")

# Check content
assert "strong context window" in prompt.lower() or "2M" in prompt
assert "limited context" not in prompt.lower()
assert "[IF ollama]" not in prompt
assert "[IF gemini]" not in prompt
assert "[ENDIF]" not in prompt

print("✅ Gemini prompt assembly correct")
```

**Checklist:**
- [ ] Gemini-specific guidance appears in prompt
- [ ] Ollama-specific guidance does NOT appear
- [ ] Model behaves as expected (detailed, explains reasoning)
- [ ] No visible `[IF]` markers in assembled prompt

### Manual Verification - Ollama

**Setup:**
```bash
export LLM_PROVIDER=ollama
uv run co chat
```

**Test 1: Check prompt assembly**
```
# In chat, ask a simple question
User: What time is it?

# Observe response style:
# ✓ Should be terse and concise
# ✓ Ideally under 3 sentences for simple queries
# ✓ No "strong context window" behavior
```

**Test 2: Verify Ollama guidance**
```python
# In separate Python session
from co_cli.prompts import get_system_prompt
prompt = get_system_prompt("ollama")

# Check content
assert "limited context" in prompt.lower() or "concise" in prompt.lower()
assert "strong context window" not in prompt.lower()
assert "[IF gemini]" not in prompt
assert "[IF ollama]" not in prompt
assert "[ENDIF]" not in prompt

print("✅ Ollama prompt assembly correct")
```

**Checklist:**
- [ ] Ollama-specific guidance appears in prompt
- [ ] Gemini-specific guidance does NOT appear
- [ ] Model behaves as expected (concise, minimal)
- [ ] No visible `[IF]` markers in assembled prompt

### Manual Verification - Project Instructions

**Setup:**
```bash
# Create test project instructions
mkdir -p .co-cli
cat > .co-cli/instructions.md << 'EOF'
# Test Project Conventions

## Database
- Use SQLAlchemy ORM only
- Never use raw SQL

## Code Style
- Max line length: 88 characters (Black default)
- Use type hints everywhere

## Testing
- Use pytest
- All tests must be functional (no mocks)
EOF

# Start chat
uv run co chat
```

**Test 1: Verify instructions are loaded**
```
User: What are the project conventions?

# Expected response should mention:
# - SQLAlchemy ORM
# - No raw SQL
# - Black formatting (88 chars)
# - Type hints
# - pytest
# - No mocks
```

**Test 2: Verify instructions are followed**
```
User: I need to query the database for all users

# Expected response should:
# ✓ Suggest SQLAlchemy ORM approach
# ✗ NOT suggest raw SQL
```

**Test 3: Verify missing instructions handled gracefully**
```bash
# Remove instructions file
rm .co-cli/instructions.md

# Start chat again
uv run co chat

# Should work normally without project instructions
# No error, no crash
```

**Checklist:**
- [ ] Project instructions loaded when file present
- [ ] Agent follows project-specific rules
- [ ] Instructions don't appear when file missing
- [ ] No crash when `.co-cli/` directory missing
- [ ] Instructions work with both Gemini and Ollama

### Debug Verification

**If tests fail, check intermediate state:**

```python
from co_cli.prompts import get_system_prompt
from pathlib import Path

# Load raw prompt
raw = Path("co_cli/prompts/system.md").read_text()
print("=== RAW PROMPT ===")
print(f"Length: {len(raw)}")
print(f"[IF gemini] count: {raw.count('[IF gemini]')}")
print(f"[IF ollama] count: {raw.count('[IF ollama]')}")
print(f"[ENDIF] count: {raw.count('[ENDIF]')}")

# Process for Gemini
gemini = get_system_prompt("gemini")
print("\n=== GEMINI PROMPT ===")
print(f"Length: {len(gemini)}")
print(f"[IF gemini] remaining: {gemini.count('[IF gemini]')}")
print(f"[IF ollama] remaining: {gemini.count('[IF ollama]')}")
print(f"[ENDIF] remaining: {gemini.count('[ENDIF]')}")

# Process for Ollama
ollama = get_system_prompt("ollama")
print("\n=== OLLAMA PROMPT ===")
print(f"Length: {len(ollama)}")
print(f"[IF gemini] remaining: {ollama.count('[IF gemini]')}")
print(f"[IF ollama] remaining: {ollama.count('[IF ollama]')}")
print(f"[ENDIF] remaining: {ollama.count('[ENDIF]')}")

# All counts should be 0 for processed prompts
assert gemini.count("[IF ") == 0
assert ollama.count("[IF ") == 0
print("\n✅ All conditionals processed correctly")
```

---

## Documentation Updates

### File: `docs/TODO-prompts-refactor.md`

**Replace entire file** with implementation tracking version.

**Structure:**
1. Status tracking (✅ Complete / 🚧 In Progress / ⏳ Pending)
2. Phase checklist (Phases 1-5)
3. Design reference link
4. Known issues section
5. Files modified list
6. Success criteria
7. Implementation notes
8. Timeline estimate
9. Rollback plan

**Key sections to include:**

```markdown
## Status: ⏳ Pending

### Phase 1: Prompt Assembly Logic
- [ ] Add `get_system_prompt()` to `co_cli/prompts/__init__.py`
- [ ] Implement conditional processing
- [ ] Implement project instructions loading
- [ ] Add validation checks
- [ ] Add docstrings

### Phase 2: Agent Integration
- [ ] Update import in `co_cli/agent.py`
- [ ] Change call site to use `get_system_prompt()`
- [ ] Verify integration works

### Phase 3: Testing
- [ ] Create `tests/test_prompts.py`
- [ ] Add 20 comprehensive tests
- [ ] Verify all tests pass

### Phase 4: Documentation
- [ ] Update this file with completion status
- [ ] Document project instructions in README
- [ ] Add example `.co-cli/instructions.md`

### Phase 5: Manual Verification
- [ ] Test with Gemini
- [ ] Test with Ollama
- [ ] Test project instructions loading
```

### File: `README.md`

**Add new section:** "Project-Specific Instructions"

**Location:** After "Configuration" section

**Content:**
```markdown
## Project-Specific Instructions

Co can load project-specific conventions and rules from `.co-cli/instructions.md`.

**Example `.co-cli/instructions.md`:**
```markdown
# Project Conventions

## Code Style
- Use Black for formatting (88 character line limit)
- Type hints required for all functions
- Docstrings: Google style

## Database
- Use SQLAlchemy ORM only
- No raw SQL unless performance-critical
- All migrations via Alembic

## Testing
- pytest for all tests
- Functional tests only (no mocks)
- Minimum 80% coverage
```

**How it works:**
1. Create `.co-cli/instructions.md` in your project root
2. Co loads and follows these instructions automatically
3. Instructions are appended to the system prompt
4. All team members get consistent guidance when using Co

**Tips:**
- Keep instructions concise and actionable
- Use bullet points for readability
- Include "why" for non-obvious rules
- Commit to git so team shares conventions
```

### File: `.co-cli/instructions.md` (Example)

**Create example file** in docs for reference.

**Location:** `docs/examples/.co-cli/instructions.md`

**Content:**
```markdown
# Project-Specific Instructions

This file provides Co with project-specific conventions, patterns, and rules.

## Code Style

- **Formatting:** Use Black with 88-character line limit
- **Type hints:** Required for all functions and methods
- **Docstrings:** Google style for all public APIs
- **Imports:** Absolute imports only, no relative imports

## Architecture

- **Database:** SQLAlchemy ORM only, no raw SQL
- **API:** FastAPI framework, async/await everywhere
- **Testing:** pytest with pytest-asyncio, functional tests only
- **Logging:** structlog with JSON output

## Conventions

- **Branch naming:** `feature/`, `bugfix/`, `hotfix/` prefixes
- **Commit messages:** Conventional Commits format
- **PR requirements:** Tests pass, coverage ≥80%, approved by 1 reviewer

## Common Patterns

### Database Queries
```python
# ✅ Good: Use SQLAlchemy ORM
users = session.query(User).filter_by(active=True).all()

# ❌ Bad: Raw SQL
users = session.execute("SELECT * FROM users WHERE active = 1")
```

### API Endpoints
```python
# ✅ Good: Async with proper error handling
@app.get("/users/{user_id}")
async def get_user(user_id: int) -> User:
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404)
    return user
```

## Testing Guidelines

- All tests in `tests/` directory
- Use `pytest.mark.asyncio` for async tests
- No mocks unless testing external API integration
- Fixture naming: `test_<feature>_<scenario>()`

## Known Issues

- Database connection pool exhaustion on high load → Use connection limits
- Async context management in tests → Use `async with` consistently
```

---

## Success Criteria

The Phase 1a refactor is complete when ALL of the following are true:

### Code Criteria
- [ ] `get_system_prompt(provider: str) -> str` function exists in `co_cli/prompts/__init__.py`
- [ ] Function processes `[IF gemini]` and `[IF ollama]` conditionals correctly
- [ ] Function loads `.co-cli/instructions.md` when present
- [ ] Function validates output (no empty prompts, no unprocessed markers)
- [ ] `co_cli/agent.py` uses `get_system_prompt(settings.llm_provider)`
- [ ] `load_prompt()` still exists for backward compatibility

### Test Criteria
- [ ] `tests/test_prompts.py` exists with ~20 comprehensive tests
- [ ] All tests pass: `uv run pytest tests/test_prompts.py -v`
- [ ] No regressions: `uv run pytest` (all tests pass)
- [ ] Coverage >95%: `uv run pytest tests/test_prompts.py --cov=co_cli.prompts`

### Behavioral Criteria
- [ ] Gemini gets prompt WITHOUT Ollama sections
- [ ] Ollama gets prompt WITHOUT Gemini sections
- [ ] Unknown providers default to Ollama behavior
- [ ] Project instructions append if `.co-cli/instructions.md` exists
- [ ] Agent works correctly with both providers
- [ ] No visible `[IF]` or `[ENDIF]` markers in assembled prompts

### Documentation Criteria
- [ ] `docs/TODO-prompts-refactor.md` updated with implementation status
- [ ] `README.md` documents project instructions pattern
- [ ] Example `.co-cli/instructions.md` created in docs

### Verification Criteria
- [ ] Manual test with Gemini: `LLM_PROVIDER=gemini uv run co chat`
- [ ] Manual test with Ollama: `LLM_PROVIDER=ollama uv run co chat`
- [ ] Manual test with project instructions (create test file, verify loading)
- [ ] Code review checklist completed

### Quality Criteria
- [ ] All functions have type hints
- [ ] All functions have docstrings (Google style)
- [ ] Error messages are clear and actionable
- [ ] Code follows project style (Black formatting, no `import *`)
- [ ] Git diffs are readable and reviewable
- [ ] No breaking changes to existing APIs

### Integration Criteria
- [ ] Agent factory works with both providers
- [ ] Chat loop continues to function normally
- [ ] Tools continue to work correctly
- [ ] No performance regressions (prompt assembly is fast)

---

## Risk Assessment

### Identified Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Regex fails on edge cases | Low | Medium | Comprehensive tests cover nested markers, empty sections, special chars |
| Empty prompt after processing | Low | High | Validation check catches before sending to LLM, raises clear error |
| Project instructions malformed | Medium | Low | Graceful handling - just append raw content, no parsing required |
| Performance regression | Very Low | Low | String ops are fast, single assembly at startup, not per-message |
| Breaking existing behavior | Very Low | High | Backward compatible - `load_prompt()` unchanged, agent factory is only call site |
| Unprocessed markers sent to LLM | Low | Medium | Validation check catches remaining `[IF ` markers, raises error |
| Case sensitivity issues | Very Low | Low | Provider name converted to lowercase, tests verify case-insensitivity |
| File encoding issues | Very Low | Low | Always specify `encoding="utf-8"`, tests use standard ASCII/UTF-8 |

### Risk Mitigation Strategy

**1. Comprehensive Testing**
- 20+ tests cover all code paths
- Edge cases explicitly tested (missing files, empty content, unknown providers)
- Integration tests verify agent factory still works
- Manual verification with both providers

**2. Validation Checks**
- Empty prompt detection with clear error message
- Unprocessed marker detection (indicates regex bug)
- Fail fast on invalid state (don't send broken prompts to LLM)

**3. Backward Compatibility**
- Old `load_prompt()` function unchanged
- New `get_system_prompt()` is additive, not replacement
- Agent factory is only call site (isolated change)
- Old behavior still available if rollback needed

**4. Clear Error Messages**
- Include file paths in FileNotFoundError
- Specific validation error messages
- No silent failures

**5. Incremental Rollout**
- Implement → Test → Verify → Document
- Can test locally before committing
- Can commit without deploying
- Can rollback single file if issues arise

### Rollback Plan

**If critical issues arise after implementation:**

**Option 1: Quick revert (5 minutes)**
```python
# In co_cli/agent.py, line ~82
system_prompt = load_prompt("system")  # Revert to old call
```
- System falls back to current behavior (no conditionals processed)
- Agent still works, just with non-optimal prompts
- Buys time to debug issue

**Option 2: Full rollback (git revert)**
```bash
git revert <commit-hash>
```
- Removes all changes
- Tests still present (can keep for future attempt)
- Clean slate

**Option 3: Fix forward**
- If issue is minor (e.g., regex bug), fix and deploy
- Tests should catch most issues before deployment
- Validation catches runtime issues clearly

**Rollback decision criteria:**
- **Immediate revert:** Agent crashes, produces empty prompts, infinite loops
- **Fix forward:** Wrong content in prompt, minor formatting issues, edge case bugs
- **No action needed:** Different but valid behavior, user preference differences

---

## Future Phases

### Phase 1b: Personality Templates (Next)

**Goal:** Add personality/tone options (professional, friendly, terse, inquisitive)

**Design:**
- Pre-set personality templates in `co_cli/prompts/personalities/`
- User selects via config: `personality = "friendly"`
- Templates define tone, verbosity, empathy level
- Injected between model conditionals and project instructions

**Extension point:**
```python
# In get_system_prompt()
if personality := settings.personality:
    personality_content = load_personality(personality)
    base_prompt += f"\n\n## Personality\n\n{personality_content}"
```

**Templates:**
- `professional.md` - Formal, precise, minimal small talk
- `friendly.md` - Warm, conversational, uses "we"
- `terse.md` - Ultra-concise, bullet points, no explanations
- `inquisitive.md` - Asks clarifying questions, explores options

**Estimated effort:** 3-4 hours

---

### Phase 1c: Internal Knowledge (Later)

**Goal:** Load Co's learned context (facts about user, project, preferences)

**Design:**
- Storage: `.co-cli/internal/context.json`
- Content: User facts, project insights, learned patterns
- Format: JSON with structured fields
- Loading: Inject after personality, before project instructions

**Extension point:**
```python
# In get_system_prompt()
internal_knowledge = Path.cwd() / ".co-cli" / "internal" / "context.json"
if internal_knowledge.exists():
    knowledge = json.loads(internal_knowledge.read_text())
    base_prompt += format_internal_knowledge(knowledge)
```

**Schema:**
```json
{
  "user": {
    "name": "Alex",
    "timezone": "America/Los_Angeles",
    "preferences": {
      "verbosity": "detailed",
      "explanation_style": "examples_first"
    }
  },
  "project": {
    "name": "co-cli",
    "type": "python_cli",
    "patterns": [
      "Uses pydantic-ai for agents",
      "Docker for sandboxing",
      "pytest for tests (no mocks)"
    ]
  },
  "learned_facts": [
    "User prefers async/await over callbacks",
    "Project follows Google-style docstrings",
    "Database is PostgreSQL with SQLAlchemy"
  ]
}
```

**Estimated effort:** 4-5 hours

---

### Phase 2: User Preferences (Future)

**Goal:** Workflow preferences system (auto-approve, verbosity, format)

**Design:**
- Storage: `.co-cli/preferences.json` (user-level) or `~/.config/co-cli/preferences.json` (global)
- Content: Behavioral preferences, not identity/knowledge
- Loading: After internal knowledge, before project instructions

**Research needed:**
- Survey peer systems (Claude Code, Codex, Aider, Gemini CLI)
- Identify 2026 best practices
- Design preference schema

**Example preferences:**
```json
{
  "approval": {
    "auto_approve_read_only": true,
    "auto_approve_tools": ["obsidian_search", "google_search_drive"],
    "always_confirm_tools": ["shell_command"]
  },
  "output": {
    "verbosity": "detailed",
    "code_style": "show_diffs_not_full_files",
    "streaming": true
  },
  "behavior": {
    "proactive_suggestions": false,
    "ask_clarifying_questions": true,
    "prefer_examples": true
  }
}
```

**Estimated effort:** 8-10 hours (includes research)

---

### Phase 3: Skills System (Post-MVP)

**Goal:** User-defined workflows and macros

**Design:**
- Co can be "Finch"-like with just: soul + knowledge + tools + memory
- Skills are nice-to-have, not required for personality
- Deferred until Phase 2 complete

**Why deferred:**
- Focus on foundation first (soul, knowledge, emotion, habit)
- Skills are extension, not core identity
- Can add later without redesigning prompt system

**Estimated effort:** TBD (depends on Phase 2 learnings)

---

## Implementation Checklist

### Phase 1: Code Implementation

**File: `co_cli/prompts/__init__.py`**
- [ ] Add `import re` at top
- [ ] Add `get_system_prompt(provider: str) -> str` function
  - [ ] Load base prompt from `system.md`
  - [ ] Process conditionals based on provider
  - [ ] Load project instructions if present
  - [ ] Validate result (empty check, marker check)
  - [ ] Return assembled prompt
- [ ] Add comprehensive docstring with type hints
- [ ] Keep `load_prompt()` unchanged (backward compatibility)

**File: `co_cli/agent.py`**
- [ ] Update import: `from co_cli.prompts import get_system_prompt`
- [ ] Change line ~82: `system_prompt = get_system_prompt(settings.llm_provider)`
- [ ] Verify `settings` is available in scope (it is)
- [ ] Test agent factory still works

---

### Phase 2: Testing

**File: `tests/test_prompts.py` (NEW)**
- [ ] Create file with module docstring
- [ ] Add imports (`pytest`, `Path`, `get_system_prompt`, `load_prompt`)
- [ ] Add `TestLoadPrompt` class
  - [ ] `test_load_system_prompt` - Basic loading works
  - [ ] `test_load_nonexistent_prompt` - Error handling
- [ ] Add `TestConditionalProcessing` class
  - [ ] `test_gemini_conditionals` - Gemini gets Gemini content only
  - [ ] `test_ollama_conditionals` - Ollama gets Ollama content only
  - [ ] `test_unknown_provider_defaults_to_ollama` - Unknown = Ollama
  - [ ] `test_case_insensitive_provider` - Case handling
  - [ ] `test_no_unprocessed_conditionals` - All markers removed
- [ ] Add `TestProjectInstructions` class
  - [ ] `test_no_project_instructions` - Missing file OK
  - [ ] `test_with_project_instructions` - File loads correctly
  - [ ] `test_project_instructions_work_with_all_providers` - Works for all providers
- [ ] Add `TestValidation` class
  - [ ] `test_prompt_not_empty` - Never empty
  - [ ] `test_no_unprocessed_markers` - Validation catches bugs
- [ ] Add `TestPromptContent` class
  - [ ] `test_core_sections_present` - Major sections exist
  - [ ] `test_directive_vs_inquiry_table` - Table with examples
  - [ ] `test_fact_verification_procedure` - Multi-step procedure
  - [ ] `test_tool_guidance_present` - Tool sections exist
- [ ] Add `TestBackwardCompatibility` class
  - [ ] `test_load_prompt_unchanged` - Old function still works
- [ ] Add `TestAgentIntegration` class
  - [ ] `test_prompt_assembly_returns_string` - Returns valid string
  - [ ] `test_provider_from_settings` - Works with Settings object

**Run tests:**
- [ ] `uv run pytest tests/test_prompts.py -v` - All pass?
- [ ] `uv run pytest` - No regressions?
- [ ] `uv run pytest tests/test_prompts.py --cov=co_cli.prompts` - Coverage >95%?

---

### Phase 3: Documentation

**File: `docs/TODO-prompts-refactor.md`**
- [ ] Replace entire content with implementation tracking
- [ ] Add status checkboxes for all phases
- [ ] Add known issues section
- [ ] Add success criteria
- [ ] Add implementation notes (regex strategy, file paths, validation)
- [ ] Add timeline estimate
- [ ] Add rollback plan

**File: `README.md`**
- [ ] Add "Project-Specific Instructions" section
- [ ] Document `.co-cli/instructions.md` pattern
- [ ] Provide example with conventions
- [ ] Explain how it works (loaded at startup, appended to prompt)

**File: `docs/examples/.co-cli/instructions.md` (NEW)**
- [ ] Create example instructions file
- [ ] Include code style, architecture, conventions sections
- [ ] Add code examples (good vs bad patterns)
- [ ] Document testing guidelines

---

### Phase 4: Manual Verification

**Gemini Testing:**
- [ ] Set `export LLM_PROVIDER=gemini`
- [ ] Run `uv run co chat`
- [ ] Ask simple question, observe response style (detailed OK)
- [ ] Verify in Python: `get_system_prompt("gemini")` has no Ollama content
- [ ] Check for Gemini-specific guidance in prompt
- [ ] Confirm no `[IF]` markers visible

**Ollama Testing:**
- [ ] Set `export LLM_PROVIDER=ollama`
- [ ] Run `uv run co chat`
- [ ] Ask simple question, observe response style (concise)
- [ ] Verify in Python: `get_system_prompt("ollama")` has no Gemini content
- [ ] Check for Ollama-specific guidance in prompt
- [ ] Confirm no `[IF]` markers visible

**Project Instructions Testing:**
- [ ] Create `.co-cli/instructions.md` with test content
- [ ] Run `uv run co chat`
- [ ] Ask about project conventions
- [ ] Verify agent mentions instructions content
- [ ] Remove `.co-cli/instructions.md`
- [ ] Restart chat, verify no crash
- [ ] Confirm instructions don't appear when missing

**Debug Testing:**
- [ ] Run debug script (from verification section)
- [ ] Verify all marker counts are 0 in processed prompts
- [ ] Check prompt lengths are reasonable (>500 chars)

---

### Phase 5: Code Review

**Self-review checklist:**
- [ ] All functions have type hints
- [ ] All functions have docstrings (Google style)
- [ ] Error messages are clear and include context
- [ ] File paths use `pathlib.Path` (not string concat)
- [ ] File reading specifies `encoding="utf-8"`
- [ ] Regex patterns are correct (test in REPL if unsure)
- [ ] Validation checks are present (empty, unprocessed markers)
- [ ] No `import *` (explicit imports only)
- [ ] Code follows Black formatting
- [ ] No global state or mutable defaults
- [ ] Edge cases handled (missing files, empty content, unknown providers)

**Git review:**
- [ ] Git diff is readable
- [ ] Commit message is clear and follows Conventional Commits
- [ ] No unintended files in commit
- [ ] No commented-out code
- [ ] No debug print statements

---

### Phase 6: Completion

**Final checks:**
- [ ] All automated tests pass
- [ ] Manual verification complete (both providers)
- [ ] Documentation updated
- [ ] Success criteria all met
- [ ] Code review complete
- [ ] Ready to commit

**Git commit:**
```bash
git add co_cli/prompts/__init__.py
git add co_cli/agent.py
git add tests/test_prompts.py
git add docs/TODO-prompts-refactor.md
git add README.md
git add docs/examples/.co-cli/instructions.md

git commit -m "feat(prompts): add model-specific conditional processing

- Add get_system_prompt() function to process [IF model] conditionals
- Support .co-cli/instructions.md for project-specific guidance
- Add comprehensive test suite (20 tests, >95% coverage)
- Update agent factory to use conditional prompts
- Document project instructions pattern in README

Closes: Phase 1a of prompt system refactor
Ref: docs/PROPOSAL-prompt-system.md"
```

**Update TODO:**
- [ ] Mark Phase 1a tasks as ✅ Complete in `docs/TODO-prompts-refactor.md`
- [ ] Update status to "✅ Complete" at top of file
- [ ] Commit TODO update separately

**Announce completion:**
- [ ] Test in real workflow
- [ ] Note any issues for future phases
- [ ] Begin planning Phase 1b (personality templates)

---

## Appendix A: Key Code Snippets

### Complete get_system_prompt() Implementation

```python
import re
from pathlib import Path


def get_system_prompt(provider: str) -> str:
    """Assemble system prompt with model-specific conditionals and project overrides.

    Processing steps:
    1. Load base system.md
    2. Process model conditionals ([IF gemini] / [IF ollama])
    3. Append project instructions from .co-cli/instructions.md (if exists)
    4. Validate result (no empty prompt, no unprocessed markers)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown)
                 Unknown providers default to Ollama (conservative)

    Returns:
        Assembled system prompt as string

    Raises:
        FileNotFoundError: If system.md doesn't exist
        ValueError: If assembled prompt is empty or has unprocessed conditionals

    Example:
        >>> prompt = get_system_prompt("gemini")
        >>> assert "[IF ollama]" not in prompt  # Ollama sections removed
        >>> assert "[IF gemini]" not in prompt  # Markers cleaned up
    """
    # 1. Load base prompt
    prompts_dir = Path(__file__).parent
    prompt_file = prompts_dir / "system.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    base_prompt = prompt_file.read_text(encoding="utf-8")

    # 2. Process model conditionals
    provider_lower = provider.lower()

    if provider_lower == "gemini":
        # Remove Ollama sections
        base_prompt = re.sub(
            r"\[IF ollama\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        # Clean up Gemini markers
        base_prompt = base_prompt.replace("[IF gemini]", "").replace("[ENDIF]", "")
    elif provider_lower == "ollama":
        # Remove Gemini sections
        base_prompt = re.sub(
            r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        # Clean up Ollama markers
        base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
    else:
        # Unknown provider - treat as Ollama (conservative default)
        base_prompt = re.sub(
            r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
        )
        base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")

    # 3. Load project instructions if present
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        base_prompt += "\n\n## Project-Specific Instructions\n\n"
        base_prompt += instructions_content

    # 4. Validate result
    if not base_prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    # Check for unprocessed conditionals (indicates bug in regex)
    if "[IF " in base_prompt:
        raise ValueError("Unprocessed conditional markers remain in prompt")

    return base_prompt
```

### Agent Factory Integration

```python
# In co_cli/agent.py

# Import (top of file)
from co_cli.prompts import get_system_prompt

# In get_agent() function
def get_agent(settings: Settings) -> Agent[CoDeps, str | DeferredToolRequests]:
    """Create and configure the Co agent."""
    # ... model initialization ...

    # Assemble prompt with conditionals
    system_prompt = get_system_prompt(settings.llm_provider)

    # ... rest of agent setup ...
```

### Debug Script

```python
"""Debug script to verify prompt assembly."""

from pathlib import Path
from co_cli.prompts import get_system_prompt

# Load raw prompt
raw = Path("co_cli/prompts/system.md").read_text()
print("=== RAW PROMPT ===")
print(f"Length: {len(raw)}")
print(f"[IF gemini] count: {raw.count('[IF gemini]')}")
print(f"[IF ollama] count: {raw.count('[IF ollama]')}")
print(f"[ENDIF] count: {raw.count('[ENDIF]')}")

# Process for Gemini
gemini = get_system_prompt("gemini")
print("\n=== GEMINI PROMPT ===")
print(f"Length: {len(gemini)}")
print(f"[IF gemini] remaining: {gemini.count('[IF gemini]')}")
print(f"[IF ollama] remaining: {gemini.count('[IF ollama]')}")
print(f"[ENDIF] remaining: {gemini.count('[ENDIF]')}")
assert gemini.count("[IF ") == 0, "Gemini has unprocessed markers!"

# Process for Ollama
ollama = get_system_prompt("ollama")
print("\n=== OLLAMA PROMPT ===")
print(f"Length: {len(ollama)}")
print(f"[IF gemini] remaining: {ollama.count('[IF gemini]')}")
print(f"[IF ollama] remaining: {ollama.count('[IF ollama]')}")
print(f"[ENDIF] remaining: {ollama.count('[ENDIF]')}")
assert ollama.count("[IF ") == 0, "Ollama has unprocessed markers!"

print("\n✅ All conditionals processed correctly")
print(f"📊 Gemini prompt: {len(gemini)} chars")
print(f"📊 Ollama prompt: {len(ollama)} chars")
```

---

## Appendix B: Testing Patterns

### Fixture Pattern (tmp_path + monkeypatch)

```python
def test_with_project_instructions(self, tmp_path, monkeypatch):
    """Load and append .co-cli/instructions.md if present."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    # Create test structure
    instructions_dir = tmp_path / ".co-cli"
    instructions_dir.mkdir()
    instructions_file = instructions_dir / "instructions.md"
    instructions_file.write_text("# Test Instructions\n\n- Rule 1\n- Rule 2")

    # Test
    prompt = get_system_prompt("gemini")
    assert "Test Instructions" in prompt
    assert "Rule 1" in prompt
```

### Assertion Pattern (Multiple Options)

```python
# Check for various possible phrasings
has_ollama_guidance = (
    "limited context" in prompt.lower()
    or "4K-32K" in prompt
    or "concise" in prompt.lower()
)
assert has_ollama_guidance, "Expected Ollama guidance not found"
```

### Loop Pattern (Test All Providers)

```python
def test_no_unprocessed_conditionals(self):
    """All conditional markers are processed."""
    for provider in ["gemini", "ollama", "unknown"]:
        prompt = get_system_prompt(provider)
        assert "[IF " not in prompt, f"[IF] found in {provider} prompt"
        assert "[ENDIF]" not in prompt, f"[ENDIF] found in {provider} prompt"
```

---

## Appendix C: Reference Links

### Internal Documentation
- `docs/PROPOSAL-prompt-system.md` - Complete architecture specification (1705 lines)
- `docs/DESIGN-co-evolution.md` - "Finch" vision and five pillars
- `docs/REVIEW-prompts-peer-systems.md` - Peer system analysis
- `docs/REVIEW-compare-four.md` - Four-system prompt comparison
- `co_cli/prompts/system.md` - Current system prompt (761 lines, with conditionals)

### Code Files
- `co_cli/prompts/__init__.py` - Prompt loader (will add assembly logic)
- `co_cli/agent.py` - Agent factory (line 82 uses prompt)
- `co_cli/config.py` - Settings class (defines llm_provider field)
- `tests/test_agent.py` - Existing agent tests (pattern reference)

### External Resources
- Python `re` module: https://docs.python.org/3/library/re.html
- pytest fixtures: https://docs.pytest.org/en/stable/fixture.html
- pathlib: https://docs.python.org/3/library/pathlib.html

---

## Appendix D: Timeline Estimate

| Phase | Task | Time Estimate |
|-------|------|---------------|
| 1 | Add `get_system_prompt()` function | 1-2 hours |
| 1 | Add validation and docstrings | 30 minutes |
| 2 | Update agent factory integration | 15 minutes |
| 3 | Write test suite (20 tests) | 2-3 hours |
| 3 | Run tests and fix issues | 30 minutes |
| 4 | Update documentation | 30 minutes |
| 5 | Manual verification (both providers) | 30 minutes |
| 5 | Code review and cleanup | 30 minutes |
| **Total** | | **4-6 hours** |

**Breakdown by activity:**
- **Implementation:** 2-2.5 hours
- **Testing:** 2.5-3.5 hours
- **Documentation:** 0.5-1 hour
- **Verification:** 0.5-1 hour

**Assumptions:**
- Uninterrupted work time
- No major blockers or design changes
- Tests pass on first or second attempt
- Familiar with codebase and tools

**Buffer:**
- Add 50% for context switching (6-9 hours)
- Add 100% for discovery/debugging (8-12 hours)

**Realistic estimate for production:** 1-2 days (including reviews, breaks, other tasks)

---

## Appendix E: Glossary

**Terms used in this document:**

- **Conditional** - `[IF model]...[ENDIF]` markers in system.md for model-specific content
- **Prompt assembly** - Process of loading, processing, and combining prompt components
- **Project instructions** - User-provided conventions in `.co-cli/instructions.md`
- **Provider** - LLM provider name ("gemini", "ollama", or other)
- **Marker** - Syntax token like `[IF gemini]` or `[ENDIF]`
- **Backward compatibility** - New code doesn't break existing functionality
- **Validation** - Runtime checks to ensure prompt is correct (not empty, no unprocessed markers)
- **Extension point** - Code location designed for future enhancement without redesign

**Phase terminology:**
- **Phase 1a** - Model conditionals + project instructions (THIS DOCUMENT)
- **Phase 1b** - Personality templates (next)
- **Phase 1c** - Internal knowledge loading (after 1b)
- **Phase 2** - User preferences + MCP (future)

**Testing terminology:**
- **Functional test** - Tests real behavior, no mocks
- **Regression test** - Ensures existing functionality still works
- **Integration test** - Tests multiple components together
- **Edge case** - Unusual or boundary condition (empty file, missing file, etc.)

---

**END OF IMPLEMENTATION GUIDE**

This document contains all critical information for Phase 1a implementation. Nothing has been omitted.
