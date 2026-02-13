"""Tests for YAML frontmatter parsing and validation."""

import pytest

from co_cli._frontmatter import (
    parse_frontmatter,
    strip_frontmatter,
    validate_memory_frontmatter,
)


def test_parse_frontmatter_valid():
    """Test parsing valid frontmatter."""
    content = """---
version: 1
updated: 2026-02-09T14:30:00Z
---

# Body content
Some text here."""

    frontmatter, body = parse_frontmatter(content)

    assert frontmatter == {"version": 1, "updated": "2026-02-09T14:30:00Z"}
    assert body == "# Body content\nSome text here."


def test_parse_frontmatter_missing():
    """Test parsing content without frontmatter."""
    content = "# No frontmatter\nJust body text."

    frontmatter, body = parse_frontmatter(content)

    assert frontmatter == {}
    assert body == content


def test_parse_frontmatter_malformed_yaml():
    """Test parsing with malformed YAML frontmatter."""
    content = """---
invalid: [unclosed list
---

Body text."""

    frontmatter, body = parse_frontmatter(content)

    # Should treat as no frontmatter when YAML is malformed
    assert frontmatter == {}
    assert body == content


def test_parse_frontmatter_empty():
    """Test parsing with empty frontmatter."""
    content = """---
---

Body text."""

    frontmatter, body = parse_frontmatter(content)

    # Empty frontmatter is treated as no frontmatter
    assert frontmatter == {}
    assert body == content  # Whole content returned as body


def test_strip_frontmatter():
    """Test stripping frontmatter to get body only."""
    content = """---
version: 1
---

Body content here."""

    body = strip_frontmatter(content)

    assert body == "Body content here."


def test_validate_memory_frontmatter_valid():
    """Test validating valid memory frontmatter."""
    frontmatter = {
        "id": 1,
        "created": "2026-02-09T14:30:00Z",
        "tags": ["python", "style"],
        "source": "user-told",
    }

    # Should not raise
    validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_minimal():
    """Test validating memory frontmatter with only required fields."""
    frontmatter = {"id": 1, "created": "2026-02-09T14:30:00Z"}

    # Should not raise
    validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_missing_id():
    """Test validation fails when id is missing."""
    frontmatter = {"created": "2026-02-09T14:30:00Z"}

    with pytest.raises(ValueError, match="missing required field: id"):
        validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_invalid_id_type():
    """Test validation fails when id is wrong type."""
    frontmatter = {"id": "1", "created": "2026-02-09T14:30:00Z"}

    with pytest.raises(ValueError, match="must be an integer"):
        validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_missing_created():
    """Test validation fails when created is missing."""
    frontmatter = {"id": 1}

    with pytest.raises(ValueError, match="missing required field: created"):
        validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_invalid_tags_type():
    """Test validation fails when tags is not a list."""
    frontmatter = {"id": 1, "created": "2026-02-09T14:30:00Z", "tags": "not-a-list"}

    with pytest.raises(ValueError, match="must be a list"):
        validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_invalid_tags_elements():
    """Test validation fails when tags contains non-strings."""
    frontmatter = {"id": 1, "created": "2026-02-09T14:30:00Z", "tags": ["valid", 123]}

    with pytest.raises(ValueError, match="must contain only strings"):
        validate_memory_frontmatter(frontmatter)


def test_validate_memory_frontmatter_invalid_source_type():
    """Test validation fails when source is not a string."""
    frontmatter = {"id": 1, "created": "2026-02-09T14:30:00Z", "source": 123}

    with pytest.raises(ValueError, match="must be a string"):
        validate_memory_frontmatter(frontmatter)
