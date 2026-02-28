"""Tests for YAML frontmatter parsing and memory frontmatter validation."""

import pytest

from co_cli._frontmatter import parse_frontmatter, validate_memory_frontmatter


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


def test_parse_frontmatter_valid():
    """Valid frontmatter is parsed into dict + body."""
    content = """---
version: 1
updated: 2026-02-09T14:30:00Z
---

# Body content
Some text here."""

    frontmatter, body = parse_frontmatter(content)
    assert frontmatter == {"version": 1, "updated": "2026-02-09T14:30:00Z"}
    assert body == "# Body content\nSome text here."


def test_parse_frontmatter_malformed_yaml():
    """Malformed YAML treated as no frontmatter."""
    content = """---
invalid: [unclosed list
---

Body text."""
    frontmatter, body = parse_frontmatter(content)
    assert frontmatter == {}
    assert body == content


def test_parse_frontmatter_no_frontmatter():
    """Content without frontmatter delimiter returns empty dict + full content."""
    content = "# Just a heading\nSome body."
    fm, body = parse_frontmatter(content)
    assert fm == {}
    assert body == content


def test_parse_frontmatter_scalar_yaml_returns_empty():
    """YAML that parses to a non-dict (e.g. a bare string) returns {}."""
    content = "---\njust a string\n---\nbody"
    fm, body = parse_frontmatter(content)
    assert fm == {}


def test_parse_frontmatter_datetime_converted_to_string():
    """YAML datetime values are converted to ISO8601 strings with Z suffix."""
    content = "---\ncreated: 2026-02-09T14:30:00Z\n---\nbody"
    fm, _ = parse_frontmatter(content)
    assert isinstance(fm["created"], str)
    assert fm["created"] == "2026-02-09T14:30:00Z"


def test_parse_frontmatter_no_leading_slash_required():
    """Frontmatter not at string start (leading text) is not parsed."""
    content = "some text\n---\nkey: val\n---\nbody"
    fm, body = parse_frontmatter(content)
    assert fm == {}
    assert body == content


# ---------------------------------------------------------------------------
# validate_memory_frontmatter — bug-finding
# ---------------------------------------------------------------------------


def _valid_fm(**kwargs) -> dict:
    base = {"id": 1, "created": "2026-01-01T00:00:00Z"}
    base.update(kwargs)
    return base


def test_validate_rejects_missing_id():
    """Missing id raises ValueError."""
    with pytest.raises(ValueError, match="id"):
        validate_memory_frontmatter({"created": "2026-01-01T00:00:00Z"})


def test_validate_rejects_missing_created():
    """Missing created raises ValueError."""
    with pytest.raises(ValueError, match="created"):
        validate_memory_frontmatter({"id": 1})


def test_validate_rejects_float_id():
    """Float id (1.5) is not a valid integer."""
    with pytest.raises(ValueError, match="id"):
        validate_memory_frontmatter(_valid_fm(id=1.5))


def test_validate_rejects_string_id():
    """String id is not a valid integer."""
    with pytest.raises(ValueError, match="id"):
        validate_memory_frontmatter(_valid_fm(id="1"))


def test_validate_rejects_bool_as_id():
    """bool is a subclass of int in Python — True must NOT be accepted as a memory id.

    Bug: isinstance(True, int) is True, so the current check
    `if not isinstance(fm['id'], int)` passes for True and False,
    silently accepting them as valid ids.
    """
    with pytest.raises(ValueError, match="id"):
        validate_memory_frontmatter(_valid_fm(id=True))


def test_validate_rejects_malformed_created():
    """Non-ISO8601 'created' value raises ValueError."""
    with pytest.raises(ValueError, match="created"):
        validate_memory_frontmatter({"id": 1, "created": "not-a-date"})


def test_validate_rejects_tags_not_a_list():
    """tags value that is not a list raises ValueError."""
    with pytest.raises(ValueError, match="tags"):
        validate_memory_frontmatter(_valid_fm(tags="python"))


def test_validate_rejects_tags_containing_non_string():
    """tags list with non-string items raises ValueError."""
    with pytest.raises(ValueError, match="tags"):
        validate_memory_frontmatter(_valid_fm(tags=["python", 42]))


def test_validate_rejects_integer_as_decay_protected():
    """Integer 1 is not a valid bool for decay_protected (int is not subclass of bool)."""
    with pytest.raises(ValueError, match="decay_protected"):
        validate_memory_frontmatter(_valid_fm(decay_protected=1))


def test_validate_rejects_malformed_updated():
    """Non-ISO8601 'updated' value raises ValueError."""
    with pytest.raises(ValueError, match="updated"):
        validate_memory_frontmatter(_valid_fm(updated="yesterday"))
