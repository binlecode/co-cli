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
    result = search_notes(ctx, "project")
    assert isinstance(result, dict)
    assert result["count"] == 2
    assert "meeting.md" in result["display"]
    assert "todo.md" in result["display"]
    assert "projector.md" not in result["display"]  # Word boundary check

    # Multi-keyword AND logic
    result = search_notes(ctx, "project team")
    assert result["count"] == 1
    assert "meeting.md" in result["display"]  # Has both "project" and "team"
    assert "todo.md" not in result["display"]  # Has "project" but not "team"

    # Limit parameter with has_more
    result = search_notes(ctx, "project", limit=1)
    assert result["count"] == 1
    assert result["has_more"] is True

    # Limit higher than total matches — has_more should be False
    result = search_notes(ctx, "project", limit=10)
    assert result["count"] == 2
    assert result["has_more"] is False


def test_search_notes_folder_filter(tmp_path):
    """Test search restricted to a subfolder."""
    work = tmp_path / "Work"
    work.mkdir()
    personal = tmp_path / "Personal"
    personal.mkdir()
    (work / "standup.md").write_text("# Standup\nDiscussed project status.")
    (personal / "journal.md").write_text("# Journal\nWorking on project at home.")

    ctx = Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        obsidian_vault_path=tmp_path,
        session_id="test",
    ))

    # Without folder — finds both
    result = search_notes(ctx, "project")
    assert result["count"] == 2

    # With folder — only Work
    result = search_notes(ctx, "project", folder="Work")
    assert result["count"] == 1
    assert "Work/standup.md" in result["display"]
    assert "Personal" not in result["display"]


def test_search_notes_tag_filter(tmp_path):
    """Test search filtered by frontmatter and inline tags."""
    (tmp_path / "active.md").write_text(
        "---\ntags: [active, work]\n---\n# Active\nProject alpha is on track."
    )
    (tmp_path / "archived.md").write_text(
        "---\ntags: [archived]\n---\n# Archived\nProject beta was cancelled."
    )
    (tmp_path / "inline.md").write_text(
        "# Inline\nProject gamma #active is in progress."
    )
    (tmp_path / "untagged.md").write_text(
        "# Untagged\nProject delta has no tags."
    )

    ctx = Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        obsidian_vault_path=tmp_path,
        session_id="test",
    ))

    # Filter by frontmatter tag
    result = search_notes(ctx, "project", tag="#active")
    assert result["count"] == 2
    assert "active.md" in result["display"]
    assert "inline.md" in result["display"]
    assert "archived.md" not in result["display"]
    assert "untagged.md" not in result["display"]

    # Tag without # prefix — should normalize
    result = search_notes(ctx, "project", tag="archived")
    assert result["count"] == 1
    assert "archived.md" in result["display"]


def test_search_notes_snippet_word_boundaries(tmp_path):
    """Test that snippets break at word boundaries, not mid-word."""
    long_content = "The quick brown fox jumps over the lazy dog. " * 10
    long_content += "KEYWORD found here in the middle of a very long document. "
    long_content += "More words follow after the match to test the boundary. " * 10
    (tmp_path / "long.md").write_text(long_content)

    ctx = Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        obsidian_vault_path=tmp_path,
        session_id="test",
    ))

    result = search_notes(ctx, "KEYWORD")
    assert result["count"] == 1
    snippet = result["display"].split("\n")[1].strip()
    # Snippet should start/end with ... and not cut mid-word
    assert snippet.startswith("...")
    assert snippet.endswith("...")
    # Should not start or end with a partial word (no leading/trailing word fragments)
    inner = snippet.strip(".")
    assert not inner[0].isalpha() or inner.startswith(" ") or inner[0].isupper()


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
    assert isinstance(notes, dict)
    assert notes["count"] == 2
    assert "Project.md" in notes["display"]
    assert "Archive/Old.md" in notes["display"]

    # Test list_notes with tag filter
    tagged = list_notes(ctx, "#work")
    assert tagged["count"] == 1
    assert "Project.md" in tagged["display"]
    assert "Archive/Old.md" not in tagged["display"]

    # Test read_note
    content = read_note(ctx, "Project.md")
    assert "# Project" in content
    assert "#work" in content
