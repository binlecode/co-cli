"""Tests for YAML frontmatter parsing."""

from co_cli._frontmatter import parse_frontmatter


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


