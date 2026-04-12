"""Functional tests for session filename helpers, find_latest_session, and migration."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from co_cli.context.session import (
    find_latest_session,
    migrate_session_files,
    parse_session_filename,
    session_filename,
)


def test_session_filename_format(tmp_path: Path) -> None:
    """session_filename output matches YYYY-MM-DD-THHMMSSz-{8chars}.jsonl format."""
    created_at = datetime(2026, 4, 11, 14, 23, 5, tzinfo=UTC)
    session_id = "550e8400-e29b-41d4-a716-446655440000"
    name = session_filename(created_at, session_id)
    assert name == "2026-04-11-T142305Z-550e8400.jsonl"


def test_session_filename_sortable() -> None:
    """Two filenames from sequential datetimes sort lexicographically = chronologically."""
    earlier = datetime(2026, 4, 10, 8, 0, 0, tzinfo=UTC)
    later = datetime(2026, 4, 11, 8, 0, 0, tzinfo=UTC)
    session_id = "aaaaaaaa-0000-0000-0000-000000000000"
    name_earlier = session_filename(earlier, session_id)
    name_later = session_filename(later, session_id)
    assert sorted([name_later, name_earlier]) == [name_earlier, name_later]


def test_find_latest_session_returns_path(tmp_path: Path) -> None:
    """find_latest_session returns the most recent path from two new-format files."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    older = datetime(2026, 4, 10, 8, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 4, 11, 8, 0, 0, tzinfo=UTC)
    old_path = sessions_dir / session_filename(older, "aaaaaaaa-0000-0000-0000-000000000000")
    new_path = sessions_dir / session_filename(newer, "bbbbbbbb-0000-0000-0000-000000000000")
    old_path.touch()
    new_path.touch()
    result = find_latest_session(sessions_dir)
    assert result == new_path


def test_find_latest_session_empty(tmp_path: Path) -> None:
    """find_latest_session returns None on an empty directory."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    assert find_latest_session(sessions_dir) is None


def test_migrate_renames_jsonl_and_deletes_json(tmp_path: Path) -> None:
    """Old-format pair → migration → new filename exists, .json sidecar deleted."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_id = str(uuid.uuid4())
    created_at = "2026-04-11T14:23:05+00:00"
    # Write old-format pair
    json_path = sessions_dir / f"{session_id}.json"
    jsonl_path = sessions_dir / f"{session_id}.jsonl"
    json_path.write_text(
        json.dumps({"session_id": session_id, "created_at": created_at}), encoding="utf-8"
    )
    jsonl_path.write_text('{"type":"test"}\n', encoding="utf-8")

    migrate_session_files(sessions_dir)

    expected_name = session_filename(datetime.fromisoformat(created_at), session_id)
    assert (sessions_dir / expected_name).exists(), "Migrated JSONL must exist at new path"
    assert not json_path.exists(), ".json sidecar must be deleted after migration"
    assert not jsonl_path.exists(), "Old-format JSONL must no longer exist at original path"


def test_migrate_skips_already_migrated(tmp_path: Path) -> None:
    """New-format .jsonl with no paired .json is untouched by migration."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    created_at = datetime(2026, 4, 11, 14, 23, 5, tzinfo=UTC)
    name = session_filename(created_at, "550e8400-e29b-41d4-a716-446655440000")
    existing = sessions_dir / name
    existing.write_text('{"type":"test"}\n', encoding="utf-8")

    migrate_session_files(sessions_dir)

    assert existing.exists(), "Already-migrated JSONL must remain untouched"
    assert list(sessions_dir.glob("*.json")) == [], "No .json files must be created"


def test_parse_session_filename_valid() -> None:
    """parse_session_filename correctly parses a valid session filename."""
    result = parse_session_filename("2026-04-11-T142305Z-550e8400.jsonl")
    assert result is not None
    uuid8, created_at = result
    assert uuid8 == "550e8400"
    assert created_at == datetime(2026, 4, 11, 14, 23, 5, tzinfo=UTC)


def test_parse_session_filename_invalid_returns_none() -> None:
    """parse_session_filename returns None for non-matching names."""
    assert parse_session_filename("random.jsonl") is None
    assert parse_session_filename("550e8400-e29b-41d4-a716-446655440000.jsonl") is None
