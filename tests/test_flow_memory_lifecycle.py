"""Tests for knowledge artifact mutation — frontmatter integrity and body replacement."""

from pathlib import Path

import yaml

from co_cli.memory.artifact import load_knowledge_artifact
from co_cli.memory.service import mutate_artifact


def _write_artifact(path: Path, body: str) -> None:
    frontmatter = {
        "kind": "knowledge",
        "artifact_kind": "note",
        "id": "test-123",
        "created": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


def test_mutate_artifact_replace_preserves_frontmatter(tmp_path: Path) -> None:
    """mutate_artifact action='replace' must update the body without corrupting frontmatter."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    artifact_path = knowledge_dir / "test-art.md"
    _write_artifact(artifact_path, "original body content")

    mutate_artifact(
        knowledge_dir,
        filename_stem="test-art",
        action="replace",
        content="updated body content",
        target="original body content",
    )

    art = load_knowledge_artifact(artifact_path)
    assert art.content.strip() == "updated body content"
    assert art.id == "test-123"
    assert art.created == "2026-01-01T00:00:00+00:00"
