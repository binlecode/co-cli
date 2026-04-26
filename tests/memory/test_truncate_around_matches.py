"""Tests for _truncate_around_matches in co_cli.memory._summary."""

from co_cli.memory._summary import _truncate_around_matches

_MAX = 200  # small max for test determinism


def test_short_text_returned_unchanged() -> None:
    """Text shorter than max_chars is returned as-is with no truncation."""
    text = "short text about docker networking"
    result = _truncate_around_matches(text, "docker", max_chars=_MAX)
    assert result == text


def test_exact_phrase_match_window_centered() -> None:
    """Long text with an exact phrase match: window is centered around the phrase."""
    prefix = "A" * 300
    phrase = "docker networking"
    suffix = "B" * 300
    text = prefix + phrase + suffix

    result = _truncate_around_matches(text, "docker networking", max_chars=_MAX)

    assert "docker networking" in result
    assert len(result) <= _MAX + len("...[earlier conversation truncated]...\n\n") + len(
        "\n\n...[later conversation truncated]..."
    )


def test_proximity_match_covers_region() -> None:
    """Terms within 200 chars of each other trigger a proximity window."""
    filler = "X" * 500
    # 'pytest' and 'fixtures' within 50 chars of each other
    needle = "pytest works great with fixtures for test isolation"
    text = filler + needle + filler

    result = _truncate_around_matches(text, "pytest fixtures", max_chars=200)

    assert "pytest" in result or "fixtures" in result


def test_no_match_fallback_returns_first_max_chars() -> None:
    """Text with no match returns the first max_chars characters."""
    text = "A" * 500
    result = _truncate_around_matches(text, "zzznomatch", max_chars=_MAX)

    assert result.startswith("A" * _MAX)
    assert "\n\n...[later conversation truncated]..." in result


def test_truncated_prefix_marker_present() -> None:
    """When the window starts after position 0, a prefix truncation marker is present."""
    # Place the query near the end so the window start > 0
    query = "target"
    padding = "P" * 300
    text = padding + query + "E" * 300

    result = _truncate_around_matches(text, query, max_chars=50)

    assert "...[earlier conversation truncated]..." in result


def test_truncated_suffix_marker_present() -> None:
    """When the window ends before len(text), a suffix truncation marker is present."""
    query = "start"
    # Query appears at the very beginning; long tail follows
    text = query + "Z" * 500

    result = _truncate_around_matches(text, query, max_chars=50)

    assert "...[later conversation truncated]..." in result


def test_no_markers_when_full_text_fits() -> None:
    """No truncation markers when the whole text fits in max_chars."""
    text = "fits in window"
    result = _truncate_around_matches(text, "fits", max_chars=1000)
    assert "truncated" not in result
    assert result == text
