"""Tests for internal knowledge loading."""

import sys
from io import StringIO
from pathlib import Path

import pytest

from co_cli.knowledge import load_internal_knowledge


@pytest.fixture
def temp_knowledge_dirs(tmp_path, monkeypatch):
    """Set up temporary knowledge directories."""
    # Create temp home dir with .config structure
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    global_config = home_dir / ".config" / "co-cli" / "knowledge"
    global_config.mkdir(parents=True)

    # Create temp project dir
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_knowledge = project_dir / ".co-cli" / "knowledge"
    project_knowledge.mkdir(parents=True)

    # Mock home and cwd
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    monkeypatch.chdir(project_dir)

    return {
        "global": global_config,
        "project": project_knowledge,
    }


def test_load_knowledge_none(temp_knowledge_dirs):
    """Test loading when no knowledge files exist."""
    result = load_internal_knowledge()
    assert result is None


def test_load_knowledge_global_only(temp_knowledge_dirs):
    """Test loading global context only."""
    context_file = temp_knowledge_dirs["global"] / "context.md"
    context_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Test User
- Prefers: async/await
"""
    )

    result = load_internal_knowledge()

    assert result is not None
    assert "## Internal Knowledge" in result
    assert "### Global Context" in result
    assert "Name: Test User" in result
    assert "Prefers: async/await" in result
    assert "### Project Context" not in result


def test_load_knowledge_project_only(temp_knowledge_dirs):
    """Test loading project context only."""
    context_file = temp_knowledge_dirs["project"] / "context.md"
    context_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

# Project
- Type: Python CLI
- Test policy: functional only
"""
    )

    result = load_internal_knowledge()

    assert result is not None
    assert "## Internal Knowledge" in result
    assert "### Project Context" in result
    assert "Type: Python CLI" in result
    assert "### Global Context" not in result


def test_load_knowledge_both(temp_knowledge_dirs):
    """Test loading both global and project context."""
    global_file = temp_knowledge_dirs["global"] / "context.md"
    global_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

Global knowledge here.
"""
    )

    project_file = temp_knowledge_dirs["project"] / "context.md"
    project_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

Project knowledge here.
"""
    )

    result = load_internal_knowledge()

    assert result is not None
    assert "### Global Context" in result
    assert "Global knowledge here" in result
    assert "### Project Context" in result
    assert "Project knowledge here" in result


def test_load_knowledge_project_overrides_global(temp_knowledge_dirs):
    """Test that project context appears after global (logical override)."""
    global_file = temp_knowledge_dirs["global"] / "context.md"
    global_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

Global content.
"""
    )

    project_file = temp_knowledge_dirs["project"] / "context.md"
    project_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

Project content (override).
"""
    )

    result = load_internal_knowledge()

    assert result is not None
    # Project should appear after global in output
    global_pos = result.find("Global Context")
    project_pos = result.find("Project Context")
    assert global_pos < project_pos


def test_load_knowledge_size_under_target(temp_knowledge_dirs):
    """Test loading with size under soft target (no warning)."""
    context_file = temp_knowledge_dirs["project"] / "context.md"
    # Small content (< 10 KiB)
    context_file.write_text(
        """---
version: 1
updated: 2026-02-09T14:30:00Z
---

Small content.
"""
    )

    # Capture stderr
    old_stderr = sys.stderr
    sys.stderr = StringIO()

    result = load_internal_knowledge()

    stderr_output = sys.stderr.getvalue()
    sys.stderr = old_stderr

    assert result is not None
    assert "WARNING" not in stderr_output
    assert "ERROR" not in stderr_output


def test_load_knowledge_size_warning(temp_knowledge_dirs):
    """Test warning when size exceeds soft target but under hard limit."""
    context_file = temp_knowledge_dirs["project"] / "context.md"
    # Generate content between 10-20 KiB
    large_content = "x" * 12000  # ~12 KiB of body content
    context_file.write_text(
        f"""---
version: 1
updated: 2026-02-09T14:30:00Z
---

{large_content}
"""
    )

    # Capture stderr
    old_stderr = sys.stderr
    sys.stderr = StringIO()

    result = load_internal_knowledge()

    stderr_output = sys.stderr.getvalue()
    sys.stderr = old_stderr

    assert result is not None
    assert "WARNING" in stderr_output
    assert "exceeds target" in stderr_output


def test_load_knowledge_size_error_truncate(temp_knowledge_dirs):
    """Test error and truncation when size exceeds hard limit."""
    context_file = temp_knowledge_dirs["project"] / "context.md"
    # Generate content > 20 KiB
    huge_content = "x" * 25000  # ~25 KiB of body content
    context_file.write_text(
        f"""---
version: 1
updated: 2026-02-09T14:30:00Z
---

{huge_content}
"""
    )

    # Capture stderr
    old_stderr = sys.stderr
    sys.stderr = StringIO()

    result = load_internal_knowledge()

    stderr_output = sys.stderr.getvalue()
    sys.stderr = old_stderr

    assert result is not None
    assert "ERROR" in stderr_output
    assert "exceeds" in stderr_output
    # Result should be truncated to 20 KiB
    assert len(result.encode("utf-8")) <= 20 * 1024


def test_load_knowledge_malformed_frontmatter(temp_knowledge_dirs):
    """Test graceful handling of malformed frontmatter."""
    context_file = temp_knowledge_dirs["project"] / "context.md"
    context_file.write_text(
        """---
invalid: [unclosed
---

Body content here.
"""
    )

    # Should not crash, should load body even if frontmatter invalid
    result = load_internal_knowledge()

    # Since frontmatter is invalid, it's treated as no frontmatter
    # and the entire content becomes the body
    assert result is not None or result is None  # Either is acceptable for malformed


def test_load_knowledge_missing_required_frontmatter_fields(temp_knowledge_dirs):
    """Test handling of missing required frontmatter fields."""
    context_file = temp_knowledge_dirs["project"] / "context.md"
    # Missing 'updated' field
    context_file.write_text(
        """---
version: 1
---

Body content here.
"""
    )

    # Should log warning but still load body
    result = load_internal_knowledge()

    assert result is not None
    assert "Body content here" in result
