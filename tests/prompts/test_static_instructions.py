"""Tests for build_static_instructions — static system prompt assembly."""

from pathlib import Path

import yaml
from tests._settings import make_settings

from co_cli.prompts._assembly import build_static_instructions


def test_static_instructions_includes_personality_memories(tmp_path: Path) -> None:
    """When personality memories are present, they appear in the static instructions string."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    frontmatter = {
        "id": "test-pc-001",
        "kind": "knowledge",
        "artifact_kind": "preference",
        "created": "2026-01-01T00:00:00+00:00",
        "tags": ["personality-context"],
    }
    (knowledge_dir / "test-pc-sentinel.md").write_text(
        f"---\n{yaml.dump(frontmatter)}---\n\npersonality-static-sentinel-XYZ789\n",
        encoding="utf-8",
    )

    config = make_settings().model_copy(update={"personality": "finch"})
    result = build_static_instructions(config, knowledge_dir=knowledge_dir)

    assert "personality-static-sentinel-XYZ789" in result, (
        f"personality memories missing from static instructions; got excerpt: {result[:200]!r}"
    )
