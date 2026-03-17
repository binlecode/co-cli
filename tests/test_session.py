"""Critical wake-up tests for session persistence behavior."""

from datetime import datetime, timedelta, timezone

import pytest

from co_cli.context._session import (
    increment_compaction,
    is_fresh,
    load_session,
    new_session,
    save_session,
    touch_session,
)


def test_save_and_load_round_trip_with_secure_permissions(tmp_path) -> None:
    """Wake-up must reliably restore a previously persisted session."""
    path = tmp_path / ".co-cli" / "session.json"
    session = new_session()
    save_session(path, session)

    loaded = load_session(path)
    assert loaded == session
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_session_invalid_json_returns_none(tmp_path) -> None:
    """Corrupt session files must fail open (new session on wake-up)."""
    path = tmp_path / ".co-cli" / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")
    assert load_session(path) is None


@pytest.mark.parametrize(
    ("session", "ttl_minutes", "expected"),
    [
        (None, 60, False),
        ({**new_session(), "last_used_at": ""}, 60, False),
        ({**new_session(), "last_used_at": "not-a-date"}, 60, False),
        (new_session(), 60, True),
        (
            {
                **new_session(),
                "last_used_at": (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat(),
            },
            60,
            False,
        ),
        (
            {
                **new_session(),
                "last_used_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            },
            60,
            True,
        ),
    ],
)
def test_is_fresh_critical_wakeup_cases(session, ttl_minutes: int, expected: bool) -> None:
    """Wake-up freshness gate decides restore-vs-new session path."""
    assert is_fresh(session, ttl_minutes) is expected


def test_touch_and_increment_are_non_mutating() -> None:
    """Session lifecycle updates should not mutate the existing session dict."""
    original = new_session()
    old_last_used = original["last_used_at"]

    touched = touch_session(original)
    incremented = increment_compaction(original)

    assert touched is not original
    assert touched["last_used_at"] != old_last_used
    assert original["last_used_at"] == old_last_used

    assert incremented is not original
    assert incremented["compaction_count"] == 1
    assert original["compaction_count"] == 0
