"""Functional tests for Obsidian tools."""

from dataclasses import dataclass

from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.sandbox import Sandbox
from co_cli.deps import CoDeps


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def test_search_notes(tmp_path):
    """Test multi-keyword search with word boundaries."""
    # Setup: create notes with different content
    (tmp_path / "meeting.md").write_text("# Meeting Notes\nDiscussed project timeline with team.")
    (tmp_path / "ideas.md").write_text("# Ideas\nBrainstorm session about new features.")
    (tmp_path / "todo.md").write_text("# Todo\n- Review project proposal\n- Send timeline")
    (tmp_path / "projector.md").write_text("# Equipment\nThe projector needs repair.")

    ctx = Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        obsidian_vault_path=tmp_path,
        session_id="test",
    ))

    # Single keyword - word boundary (should NOT match "projector")
    results = search_notes(ctx, "project")
    files = [r["file"] for r in results]
    assert "meeting.md" in files
    assert "todo.md" in files
    assert "projector.md" not in files  # Word boundary check

    # Multi-keyword AND logic
    results = search_notes(ctx, "project team")
    files = [r["file"] for r in results]
    assert "meeting.md" in files  # Has both "project" and "team"
    assert "todo.md" not in files  # Has "project" but not "team"

    # Limit parameter
    results = search_notes(ctx, "project", limit=1)
    assert len(results) == 1


def test_obsidian_list_and_read(tmp_path):
    """Test Obsidian tools with real file system."""
    # Setup: create real files
    note = tmp_path / "Project.md"
    note.write_text("# Project\nContent with #work tag")

    subdir = tmp_path / "Archive"
    subdir.mkdir()
    (subdir / "Old.md").write_text("Archived content")

    ctx = Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        obsidian_vault_path=tmp_path,
        session_id="test",
    ))

    # Test list_notes
    notes = list_notes(ctx)
    assert "Project.md" in notes
    assert "Archive/Old.md" in notes

    # Test list_notes with tag filter
    tagged = list_notes(ctx, "#work")
    assert "Project.md" in tagged
    assert "Archive/Old.md" not in tagged

    # Test read_note
    content = read_note(ctx, "Project.md")
    assert "# Project" in content
    assert "#work" in content
