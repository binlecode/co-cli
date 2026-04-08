"""Functional tests for Obsidian tools."""

from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.knowledge._store import KnowledgeStore
from tests._settings import test_settings
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.shell_backend import ShellBackend
from co_cli.deps import CoDeps

_AGENT = build_agent(config=settings)


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def test_search_notes(tmp_path):
    """Test multi-keyword search with word boundaries."""
    # Setup: create notes with different content
    (tmp_path / "meeting.md").write_text("# Meeting Notes\nDiscussed project timeline with team.")
    (tmp_path / "ideas.md").write_text("# Ideas\nBrainstorm session about new features.")
    (tmp_path / "todo.md").write_text("# Todo\n- Review project proposal\n- Send timeline")
    (tmp_path / "projector.md").write_text("# Equipment\nThe projector needs repair.")

    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))

    # Single keyword - word boundary (should NOT match "projector")
    result = search_notes(ctx, "project")
    assert result.metadata["count"] == 2
    assert "meeting.md" in result.return_value
    assert "todo.md" in result.return_value
    assert "projector.md" not in result.return_value  # Word boundary check

    # Multi-keyword AND logic
    result = search_notes(ctx, "project team")
    assert result.metadata["count"] == 1
    assert "meeting.md" in result.return_value  # Has both "project" and "team"
    assert "todo.md" not in result.return_value  # Has "project" but not "team"

    # Limit parameter with has_more
    result = search_notes(ctx, "project", limit=1)
    assert result.metadata["count"] == 1
    assert result.metadata["has_more"] is True

    # Limit higher than total matches — has_more should be False
    result = search_notes(ctx, "project", limit=10)
    assert result.metadata["count"] == 2
    assert result.metadata["has_more"] is False


def test_search_notes_folder_filter(tmp_path):
    """Test search restricted to a subfolder."""
    work = tmp_path / "Work"
    work.mkdir()
    personal = tmp_path / "Personal"
    personal.mkdir()
    (work / "standup.md").write_text("# Standup\nDiscussed project status.")
    (personal / "journal.md").write_text("# Journal\nWorking on project at home.")

    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))

    # Without folder — finds both
    result = search_notes(ctx, "project")
    assert result.metadata["count"] == 2

    # With folder — only Work
    result = search_notes(ctx, "project", folder="Work")
    assert result.metadata["count"] == 1
    assert "Work/standup.md" in result.return_value
    assert "Personal" not in result.return_value


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

    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))

    # Filter by frontmatter tag
    result = search_notes(ctx, "project", tag="#active")
    assert result.metadata["count"] == 2
    assert "active.md" in result.return_value
    assert "inline.md" in result.return_value
    assert "archived.md" not in result.return_value
    assert "untagged.md" not in result.return_value

    # Tag without # prefix — should normalize
    result = search_notes(ctx, "project", tag="archived")
    assert result.metadata["count"] == 1
    assert "archived.md" in result.return_value



def test_list_notes_pagination(tmp_path):
    """list_notes returns correct pages with offset/limit."""
    for i in range(1, 6):
        (tmp_path / f"note-{i:02d}.md").write_text(f"# Note {i}\nContent {i}")

    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))

    # Page 1: offset=0, limit=2
    r1 = list_notes(ctx, offset=0, limit=2)
    assert r1.metadata["count"] == 2
    assert r1.metadata["total"] == 5
    assert r1.metadata["offset"] == 0
    assert r1.metadata["limit"] == 2
    assert r1.metadata["has_more"] is True

    # Verify sorted order is stable
    assert "note-01.md" in r1.return_value
    assert "note-02.md" in r1.return_value
    assert "note-03.md" not in r1.return_value

    # Page 3: offset=4, limit=2 — partial last page
    r3 = list_notes(ctx, offset=4, limit=2)
    assert r3.metadata["count"] == 1
    assert r3.metadata["total"] == 5
    assert r3.metadata["has_more"] is False


def test_obsidian_list_and_read(tmp_path):
    """Test Obsidian tools with real file system."""
    # Setup: create real files
    note = tmp_path / "Project.md"
    note.write_text("# Project\nContent with #work tag")

    subdir = tmp_path / "Archive"
    subdir.mkdir()
    (subdir / "Old.md").write_text("Archived content")

    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))

    # Test list_notes
    notes = list_notes(ctx)
    assert notes.metadata["count"] == 2
    assert "Project.md" in notes.return_value
    assert "Archive/Old.md" in notes.return_value

    # Test list_notes with tag filter
    tagged = list_notes(ctx, "#work")
    assert tagged.metadata["count"] == 1
    assert "Project.md" in tagged.return_value
    assert "Archive/Old.md" not in tagged.return_value

    # Test read_note
    result = read_note(ctx, "Project.md")
    assert "# Project" in result.return_value
    assert "#work" in result.return_value
    assert result.metadata["path"] == "Project.md"


def test_fts_folder_filter_excludes_siblings(tmp_path):
    """FTS path with folder= must not return results from sibling folders.

    Simulates the case where both Work/ and Personal/ were previously indexed
    (broad index), then a search restricted to Work/ must exclude Personal/ notes.
    """
    vault = tmp_path / "vault"
    work = vault / "Work"
    personal = vault / "Personal"
    work.mkdir(parents=True)
    personal.mkdir(parents=True)

    keyword = "xylofts-unique-keyword"
    (work / "standup.md").write_text(f"# Standup\n{keyword} in work note.")
    (personal / "diary.md").write_text(f"# Diary\n{keyword} in personal note.")

    # Broad index — both folders indexed under source='obsidian'
    idx = KnowledgeStore(config=test_settings(), knowledge_db_path=tmp_path / "search.db")
    idx.sync_dir("obsidian", vault)

    ctx = _ctx(CoDeps(
        shell=ShellBackend(), knowledge_store=idx,
        config=test_settings(),
        obsidian_vault_path=vault,
    ))

    result = search_notes(ctx, keyword, folder="Work")

    assert result.metadata["count"] == 1, f"Expected 1 result (Work only), got {result.metadata['count']}"
    assert "Work" in result.return_value
    assert "Personal" not in result.return_value

    idx.close()


def test_fts_folder_filter_excludes_common_prefix_sibling(tmp_path):
    """FTS folder filter must not bleed into folders sharing a name prefix.

    Searching folder='Work' must not return results from 'Workbench/'.
    """
    vault = tmp_path / "vault"
    (vault / "Work").mkdir(parents=True)
    (vault / "Workbench").mkdir(parents=True)

    keyword = "xylofts-prefix-leak"
    (vault / "Work" / "standup.md").write_text(f"# Standup\n{keyword} in work note.")
    (vault / "Workbench" / "bench.md").write_text(f"# Bench\n{keyword} in workbench note.")

    idx = KnowledgeStore(config=test_settings(), knowledge_db_path=tmp_path / "search.db")
    idx.sync_dir("obsidian", vault)

    ctx = _ctx(CoDeps(
        shell=ShellBackend(), knowledge_store=idx,
        config=test_settings(),
        obsidian_vault_path=vault,
    ))

    result = search_notes(ctx, keyword, folder="Work")

    assert result.metadata["count"] == 1, f"Expected 1 result (Work only), got {result.metadata['count']}: {result.return_value}"
    assert "Work/standup.md" in result.return_value
    assert "Workbench" not in result.return_value

    idx.close()


def test_fts_tag_filter_works_with_index(tmp_path):
    """FTS path must apply tag filter correctly when knowledge_store is active."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "active.md").write_text(
        "---\ntags: [active, work]\n---\n# Active\nProject alpha xylofts-tag-test is on track."
    )
    (vault / "archived.md").write_text(
        "---\ntags: [archived]\n---\n# Archived\nProject beta xylofts-tag-test was cancelled."
    )

    idx = KnowledgeStore(config=test_settings(), knowledge_db_path=tmp_path / "search.db")
    idx.sync_dir("obsidian", vault)

    ctx = _ctx(CoDeps(
        shell=ShellBackend(), knowledge_store=idx,
        config=test_settings(),
        obsidian_vault_path=vault,
    ))

    result = search_notes(ctx, "xylofts-tag-test", tag="#active")

    assert result.metadata["count"] == 1, f"Expected 1 result (active tag only), got {result.metadata['count']}: {result.return_value}"
    assert "active.md" in result.return_value
    assert "archived.md" not in result.return_value

    idx.close()


# --- read_note error paths ---


def test_read_note_missing_file_raises_model_retry(tmp_path):
    """read_note raises ModelRetry when the note file does not exist."""
    (tmp_path / "exists.md").write_text("# Exists")
    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))
    with pytest.raises(ModelRetry, match="not found"):
        read_note(ctx, "nonexistent.md")


def test_read_note_path_traversal_blocked(tmp_path):
    """read_note blocks path traversal outside the vault."""
    ctx = _ctx(CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        obsidian_vault_path=tmp_path,
    ))
    with pytest.raises(ModelRetry, match="outside the vault"):
        read_note(ctx, "../../etc/passwd")
