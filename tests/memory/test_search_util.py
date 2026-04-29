"""Tests for sanitize_fts5_query in search_util."""

import pytest

from co_cli.memory.search_util import sanitize_fts5_query

# ---------------------------------------------------------------------------
# Basic passthrough / empty
# ---------------------------------------------------------------------------


def test_plain_words_pass_through():
    assert sanitize_fts5_query("hello world") == "hello world"


def test_empty_string_returns_empty():
    assert sanitize_fts5_query("") == ""


def test_whitespace_only_returns_empty():
    assert sanitize_fts5_query("   ") == ""


# ---------------------------------------------------------------------------
# FTS5 special chars stripped
# ---------------------------------------------------------------------------


def test_plus_stripped():
    result = sanitize_fts5_query("C++")
    assert "+" not in result


def test_parentheses_stripped():
    result = sanitize_fts5_query("foo(bar)")
    assert "(" not in result
    assert ")" not in result
    assert "foo" in result
    assert "bar" in result


def test_braces_stripped():
    result = sanitize_fts5_query("{test}")
    assert "{" not in result
    assert "}" not in result


def test_caret_stripped():
    result = sanitize_fts5_query("hello^world")
    assert "^" not in result


def test_unmatched_quote_stripped():
    result = sanitize_fts5_query('"unterminated')
    assert '"' not in result
    assert "unterminated" in result


# ---------------------------------------------------------------------------
# Wildcard normalisation
# ---------------------------------------------------------------------------


def test_valid_prefix_wildcard_preserved():
    assert sanitize_fts5_query("deploy*") == "deploy*"


def test_repeated_wildcards_collapsed():
    result = sanitize_fts5_query("deploy***")
    assert "***" not in result
    assert result == "deploy*"


def test_leading_wildcard_removed():
    result = sanitize_fts5_query("*word")
    assert not result.startswith("*")


# ---------------------------------------------------------------------------
# Dangling boolean operators
# ---------------------------------------------------------------------------


def test_trailing_and_removed():
    assert sanitize_fts5_query("hello AND") == "hello"


def test_leading_or_removed():
    assert sanitize_fts5_query("OR world") == "world"


def test_leading_not_removed():
    assert sanitize_fts5_query("NOT world") == "world"


def test_inline_boolean_preserved():
    result = sanitize_fts5_query("python AND NOT java")
    assert "AND" in result
    assert "NOT" in result


# ---------------------------------------------------------------------------
# Quoted phrases preserved
# ---------------------------------------------------------------------------


def test_quoted_phrase_preserved():
    assert sanitize_fts5_query('"exact phrase"') == '"exact phrase"'


def test_quoted_phrase_in_mixed_query():
    result = sanitize_fts5_query('"docker networking" setup')
    assert '"docker networking"' in result
    assert "setup" in result


def test_multiple_quoted_phrases_preserved():
    result = sanitize_fts5_query('"hello world" OR "foo bar"')
    assert '"hello world"' in result
    assert '"foo bar"' in result
    assert "OR" in result


# ---------------------------------------------------------------------------
# Hyphenated terms quoted
# ---------------------------------------------------------------------------


def test_hyphenated_term_quoted():
    assert sanitize_fts5_query("chat-send") == '"chat-send"'


def test_multi_hyphen_term_quoted():
    assert sanitize_fts5_query("docker-compose-up") == '"docker-compose-up"'


def test_hyphenated_term_in_sentence():
    result = sanitize_fts5_query("fix chat-send bug")
    assert '"chat-send"' in result
    assert "fix" in result
    assert "bug" in result


def test_already_quoted_hyphenated_not_double_quoted():
    result = sanitize_fts5_query('"chat-send"')
    assert result == '"chat-send"'
    assert '""' not in result


# ---------------------------------------------------------------------------
# Dotted terms quoted (filenames, version strings)
# ---------------------------------------------------------------------------


def test_dotted_term_quoted():
    assert sanitize_fts5_query("P2.2") == '"P2.2"'


def test_filename_with_extension_quoted():
    result = sanitize_fts5_query("session_store.py")
    assert '"session_store.py"' in result


def test_multi_dot_path_quoted():
    result = sanitize_fts5_query("simulate.p2.test.ts")
    assert '"simulate.p2.test.ts"' in result


def test_mixed_hyphen_dot_quoted_single_term():
    result = sanitize_fts5_query("my-app.config.ts")
    assert '"my-app.config.ts"' in result


# ---------------------------------------------------------------------------
# Underscored terms quoted
# ---------------------------------------------------------------------------


def test_underscored_term_quoted():
    assert sanitize_fts5_query("sp_new") == '"sp_new"'


def test_multi_underscore_term_quoted():
    assert sanitize_fts5_query("a_b_c") == '"a_b_c"'


def test_mixed_hyphen_underscore_quoted():
    result = sanitize_fts5_query("docker-compose_up")
    assert '"docker-compose_up"' in result


def test_already_quoted_underscored_not_double_quoted():
    result = sanitize_fts5_query('"sp_new"')
    assert result == '"sp_new"'
    assert '""' not in result


# ---------------------------------------------------------------------------
# Real-world co-cli query patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_fragments"),
    [
        ("co-cli memory", ['"co-cli"', "memory"]),
        ("session_store.py search", ['"session_store.py"', "search"]),
        ('"connection pool" timeout', ['"connection pool"', "timeout"]),
        ("python NOT java", ["python", "NOT", "java"]),
        ("memory_search tool", ['"memory_search"', "tool"]),
        ("v0.8.54", ['"v0.8.54"']),
    ],
)
def test_real_world_patterns(raw: str, expected_fragments: list[str]):
    result = sanitize_fts5_query(raw)
    for fragment in expected_fragments:
        assert fragment in result, f"{fragment!r} not found in {result!r}"
