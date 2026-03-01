"""Tests for personality helpers (_load_personality_memories)."""

from co_cli.tools.personality import _load_personality_memories


def test_load_personality_memories_with_tagged_files(tmp_path, monkeypatch):
    """Personality-context memories return Learned Context section."""
    memory_dir = tmp_path / ".co-cli" / "knowledge"
    memory_dir.mkdir(parents=True)
    memory_file = memory_dir / "001-user-prefers-direct.md"
    memory_file.write_text(
        "---\n"
        "id: 1\n"
        "created: '2026-01-15T00:00:00+00:00'\n"
        "tags:\n"
        "  - preference\n"
        "  - personality-context\n"
        "---\n\n"
        "User prefers direct answers without hedging\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = _load_personality_memories()
    assert "Learned Context" in result
    assert "User prefers direct answers without hedging" in result


def test_load_personality_memories_no_dir(tmp_path, monkeypatch):
    """No memory directory returns empty string."""
    monkeypatch.chdir(tmp_path)
    result = _load_personality_memories()
    assert result == ""


def test_load_personality_memories_no_matching_tags(tmp_path, monkeypatch):
    """Memories without personality-context tag are excluded."""
    memory_dir = tmp_path / ".co-cli" / "knowledge"
    memory_dir.mkdir(parents=True)
    memory_file = memory_dir / "001-unrelated.md"
    memory_file.write_text(
        "---\n"
        "id: 1\n"
        "created: '2026-01-15T00:00:00+00:00'\n"
        "tags:\n"
        "  - preference\n"
        "---\n\n"
        "User prefers dark mode\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = _load_personality_memories()
    assert result == ""
