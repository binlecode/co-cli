"""Tests for memory management tools."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.tools.memory import (
    _next_memory_id,
    _slugify,
    _search_memories,
    save_memory,
    recall_memory,
    list_memories,
)


@pytest.fixture
def temp_project_dir(tmp_path, monkeypatch):
    """Set up temporary project directory."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    return project_dir


@pytest.fixture
def mock_ctx():
    """Create mock RunContext for testing."""
    # Memory tools don't actually use ctx.deps, so return a minimal mock
    class MockCtx:
        pass

    return MockCtx()


def test_slugify():
    """Test slugification of text."""
    assert _slugify("User prefers async/await") == "user-prefers-async-await"
    assert _slugify("Uses SQLAlchemy 2.0") == "uses-sqlalchemy-2-0"
    assert _slugify("  Multiple   Spaces  ") == "multiple-spaces"
    # Test truncation
    long_text = "a" * 100
    slug = _slugify(long_text)
    assert len(slug) == 50
    assert slug == "a" * 50


def test_next_memory_id_no_memories(temp_project_dir):
    """Test getting next memory ID when no memories exist."""
    memory_id = _next_memory_id()
    assert memory_id == 1


def test_next_memory_id_with_existing(temp_project_dir):
    """Test getting next memory ID with existing memories."""
    memory_dir = temp_project_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    # Create some existing memories
    (memory_dir / "001-first.md").write_text(
        """---
id: 1
created: 2026-02-09T14:00:00Z
---

First memory.
"""
    )

    (memory_dir / "003-third.md").write_text(
        """---
id: 3
created: 2026-02-09T14:30:00Z
---

Third memory.
"""
    )

    memory_id = _next_memory_id()
    assert memory_id == 4  # Should be max(1, 3) + 1


@pytest.mark.asyncio
async def test_save_memory(temp_project_dir, mock_ctx):
    """Test saving a memory."""
    result = await save_memory(
        mock_ctx, content="User prefers async/await over callbacks", tags=["python", "style"]
    )

    assert result["memory_id"] == 1
    assert "001-user-prefers-async-await" in result["path"]
    assert "Saved memory 1" in result["display"]

    # Verify file was created
    memory_file = Path(result["path"])
    assert memory_file.exists()

    content = memory_file.read_text()
    assert "id: 1" in content
    assert "tags:" in content
    assert "python" in content
    assert "User prefers async/await" in content


@pytest.mark.asyncio
async def test_save_memory_no_tags(temp_project_dir, mock_ctx):
    """Test saving a memory without tags."""
    result = await save_memory(mock_ctx, content="Uses SQLAlchemy for ORM")

    assert result["memory_id"] == 1
    memory_file = Path(result["path"])
    content = memory_file.read_text()
    assert "tags: []" in content


@pytest.mark.asyncio
async def test_recall_memory_no_matches(temp_project_dir, mock_ctx):
    """Test recalling memories with no matches."""
    result = await recall_memory(mock_ctx, query="nonexistent")

    assert result["count"] == 0
    assert "No memories found" in result["display"]
    assert result["results"] == []


@pytest.mark.asyncio
async def test_recall_memory_with_matches(temp_project_dir, mock_ctx):
    """Test recalling memories with matches."""
    # Save some memories first
    await save_memory(mock_ctx, content="User prefers async/await", tags=["python"])
    await save_memory(mock_ctx, content="Uses SQLAlchemy ORM", tags=["database"])
    await save_memory(mock_ctx, content="Async database connections", tags=["python", "database"])

    # Search for "async"
    result = await recall_memory(mock_ctx, query="async", max_results=5)

    assert result["count"] == 2
    assert len(result["results"]) == 2
    assert "Found 2 memories" in result["display"]
    # Check that both async memories are returned
    contents = [r["content"] for r in result["results"]]
    assert any("async/await" in c for c in contents)
    assert any("Async database" in c for c in contents)


@pytest.mark.asyncio
async def test_recall_memory_tag_search(temp_project_dir, mock_ctx):
    """Test recalling memories by tag."""
    await save_memory(mock_ctx, content="Python memory", tags=["python"])
    await save_memory(mock_ctx, content="JavaScript memory", tags=["javascript"])

    result = await recall_memory(mock_ctx, query="python", max_results=5)

    assert result["count"] == 1
    assert "Python memory" in result["display"]


@pytest.mark.asyncio
async def test_recall_memory_max_results(temp_project_dir, mock_ctx):
    """Test max_results limit in recall."""
    # Create 10 memories with "test" in content
    for i in range(10):
        await save_memory(mock_ctx, content=f"Test memory {i}", tags=["test"])

    result = await recall_memory(mock_ctx, query="test", max_results=3)

    assert result["count"] == 3
    assert len(result["results"]) == 3


@pytest.mark.asyncio
async def test_list_memories_empty(temp_project_dir, mock_ctx):
    """Test listing memories when none exist."""
    result = await list_memories(mock_ctx)

    assert result["count"] == 0
    assert "No memories saved yet" in result["display"]
    assert result["memories"] == []


@pytest.mark.asyncio
async def test_list_memories_with_content(temp_project_dir, mock_ctx):
    """Test listing memories with content."""
    await save_memory(mock_ctx, content="First memory", tags=["python"])
    await save_memory(mock_ctx, content="Second memory\nWith multiple lines", tags=["database"])

    result = await list_memories(mock_ctx)

    assert result["count"] == 2
    assert len(result["memories"]) == 2
    assert "Total memories: 2" in result["display"]

    # Check summaries (first line only)
    summaries = [m["summary"] for m in result["memories"]]
    assert "First memory" in summaries
    assert "Second memory" in summaries
    assert "With multiple lines" not in result["display"]  # Should only show first line


@pytest.mark.asyncio
async def test_list_memories_sorted_by_id(temp_project_dir, mock_ctx):
    """Test that list_memories returns sorted by ID."""
    await save_memory(mock_ctx, content="Memory 1")
    await save_memory(mock_ctx, content="Memory 2")
    await save_memory(mock_ctx, content="Memory 3")

    result = await list_memories(mock_ctx)

    ids = [m["id"] for m in result["memories"]]
    assert ids == [1, 2, 3]


def test_search_memories_case_insensitive(temp_project_dir):
    """Test that search is case insensitive."""
    memory_dir = temp_project_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    (memory_dir / "001-test.md").write_text(
        """---
id: 1
created: 2026-02-09T14:00:00Z
tags: []
---

ASYNC/AWAIT pattern in Python.
"""
    )

    results = _search_memories("async", memory_dir, max_results=5)

    assert len(results) == 1
    assert "ASYNC/AWAIT" in results[0]["content"]


def test_search_memories_sorts_by_recency(temp_project_dir):
    """Test that search results are sorted by recency (created desc)."""
    memory_dir = temp_project_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    (memory_dir / "001-old.md").write_text(
        """---
id: 1
created: 2026-02-09T10:00:00Z
tags: []
---

Test memory old.
"""
    )

    (memory_dir / "002-new.md").write_text(
        """---
id: 2
created: 2026-02-09T20:00:00Z
tags: []
---

Test memory new.
"""
    )

    results = _search_memories("test", memory_dir, max_results=5)

    assert len(results) == 2
    # Most recent first
    assert results[0]["id"] == 2
    assert results[1]["id"] == 1
