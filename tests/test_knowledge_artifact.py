"""Tests for the canonical KnowledgeArtifact data model and its loader/renderer."""

from pathlib import Path

import pytest

from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    PinModeEnum,
    SourceTypeEnum,
    load_knowledge_artifact,
    load_knowledge_artifacts,
    load_standing_artifacts,
)
from co_cli.knowledge._frontmatter import (
    parse_frontmatter,
    render_knowledge_file,
    validate_knowledge_frontmatter,
)


def _write(path: Path, frontmatter: str, body: str = "body text") -> Path:
    path.write_text(f"---\n{frontmatter}---\n\n{body}\n", encoding="utf-8")
    return path


def test_load_canonical_knowledge_artifact(tmp_path: Path) -> None:
    """Canonical kind=knowledge frontmatter loads fields directly."""
    path = _write(
        tmp_path / "canon.md",
        (
            "id: abc-123\n"
            "kind: knowledge\n"
            "artifact_kind: preference\n"
            "title: Prefers pytest\n"
            "created: '2026-04-16T10:00:00Z'\n"
            "pin_mode: standing\n"
            "source_type: detected\n"
            "source_ref: session-abc\n"
            "tags:\n- testing\n"
        ),
        "User prefers pytest over unittest.",
    )
    artifact = load_knowledge_artifact(path)
    assert artifact.id == "abc-123"
    assert artifact.artifact_kind == ArtifactKindEnum.PREFERENCE.value
    assert artifact.title == "Prefers pytest"
    assert artifact.pin_mode == PinModeEnum.STANDING.value
    assert artifact.source_type == SourceTypeEnum.DETECTED.value
    assert artifact.source_ref == "session-abc"
    assert artifact.tags == ["testing"]
    assert artifact.content == "User prefers pytest over unittest."


def test_non_canonical_kind_rejected(tmp_path: Path) -> None:
    """Files with kind != knowledge are rejected by the loader (no legacy reader)."""
    path = _write(
        tmp_path / "legacy.md",
        "id: old\nkind: memory\ncreated: '2026-04-16T10:00:00Z'\n",
    )
    with pytest.raises(ValueError, match="expected kind='knowledge'"):
        load_knowledge_artifact(path)


def test_missing_required_field_raises(tmp_path: Path) -> None:
    """Missing id or created raises ValueError."""
    path = _write(tmp_path / "bad.md", "kind: knowledge\nartifact_kind: note\n")
    with pytest.raises(ValueError, match="missing required field 'id'"):
        load_knowledge_artifact(path)


def test_load_knowledge_artifacts_skips_malformed(tmp_path: Path) -> None:
    """Batch loader loads canonical files and skips broken ones with a warning."""
    _write(
        tmp_path / "a.md",
        "id: a\nkind: knowledge\nartifact_kind: preference\ncreated: '2026-04-16T10:00:00Z'\n",
    )
    _write(
        tmp_path / "b.md",
        "id: b\nkind: knowledge\nartifact_kind: article\ncreated: '2026-04-16T10:00:00Z'\n",
    )
    _write(tmp_path / "bad.md", "id: c\ncreated: '2026-04-16T10:00:00Z'\n")
    (tmp_path / "not-frontmatter.md").write_text("just text", encoding="utf-8")

    artifacts = load_knowledge_artifacts(tmp_path)
    kinds = sorted(a.artifact_kind for a in artifacts)
    assert kinds == [
        ArtifactKindEnum.ARTICLE.value,
        ArtifactKindEnum.PREFERENCE.value,
    ]


def test_load_knowledge_artifacts_filters_by_kind(tmp_path: Path) -> None:
    """artifact_kind filter selects only matching artifacts."""
    _write(
        tmp_path / "a.md",
        "id: a\nkind: knowledge\nartifact_kind: preference\ncreated: '2026-04-16T10:00:00Z'\n",
    )
    _write(
        tmp_path / "b.md",
        "id: b\nkind: knowledge\nartifact_kind: article\n"
        "title: T\nsource_ref: http://x.y\n"
        "created: '2026-04-16T10:00:00Z'\n",
    )
    articles = load_knowledge_artifacts(tmp_path, artifact_kind="article")
    assert len(articles) == 1
    assert articles[0].id == "b"


def test_load_standing_artifacts_caps_and_filters(tmp_path: Path) -> None:
    """Only pin_mode='standing' entries returned, capped at `cap`."""
    for idx in range(7):
        _write(
            tmp_path / f"s{idx}.md",
            f"id: s{idx}\nkind: knowledge\nartifact_kind: preference\n"
            f"created: '2026-04-16T10:00:00Z'\npin_mode: standing\n",
        )
    _write(
        tmp_path / "plain.md",
        "id: plain\nkind: knowledge\nartifact_kind: preference\ncreated: '2026-04-16T10:00:00Z'\n",
    )
    standing = load_standing_artifacts(tmp_path, cap=5)
    assert len(standing) == 5
    assert all(a.pin_mode == PinModeEnum.STANDING.value for a in standing)


def test_render_knowledge_file_emits_canonical_kind(tmp_path: Path) -> None:
    """render_knowledge_file writes kind=knowledge with artifact_kind populated."""
    artifact = KnowledgeArtifact(
        id="abc-1",
        path=tmp_path / "x.md",
        artifact_kind=ArtifactKindEnum.PREFERENCE.value,
        title="Prefers pytest",
        content="User prefers pytest.",
        created="2026-04-16T10:00:00Z",
        tags=["testing"],
        source_type=SourceTypeEnum.DETECTED.value,
        source_ref="session-abc",
        pin_mode=PinModeEnum.STANDING.value,
    )
    rendered = render_knowledge_file(artifact)
    fm, body = parse_frontmatter(rendered)
    assert fm["kind"] == "knowledge"
    assert fm["artifact_kind"] == "preference"
    assert fm["title"] == "Prefers pytest"
    assert fm["pin_mode"] == "standing"
    assert fm["source_ref"] == "session-abc"
    assert body.strip() == "User prefers pytest."
    validate_knowledge_frontmatter(fm)


def test_render_knowledge_file_round_trip_via_loader(tmp_path: Path) -> None:
    """render_knowledge_file → disk → load_knowledge_artifact preserves all set fields."""
    artifact = KnowledgeArtifact(
        id="r-1",
        path=tmp_path / "r.md",
        artifact_kind=ArtifactKindEnum.ARTICLE.value,
        title="Asyncio",
        content="Body text.",
        created="2026-04-16T10:00:00Z",
        tags=["python"],
        source_type=SourceTypeEnum.WEB_FETCH.value,
        source_ref="https://docs.python.org/asyncio",
        decay_protected=True,
        recall_count=3,
    )
    path = tmp_path / "r.md"
    path.write_text(render_knowledge_file(artifact), encoding="utf-8")
    loaded = load_knowledge_artifact(path)
    assert loaded.id == "r-1"
    assert loaded.artifact_kind == "article"
    assert loaded.title == "Asyncio"
    assert loaded.source_ref == "https://docs.python.org/asyncio"
    assert loaded.decay_protected is True
    assert loaded.recall_count == 3
    assert loaded.content == "Body text."


def test_render_knowledge_file_omits_defaults(tmp_path: Path) -> None:
    """Default values (pin_mode=none, decay_protected=False, recall_count=0) are omitted from YAML."""
    artifact = KnowledgeArtifact(
        id="m-1",
        path=tmp_path / "m.md",
        artifact_kind=ArtifactKindEnum.NOTE.value,
        title=None,
        content="Just a note.",
        created="2026-04-16T10:00:00Z",
    )
    rendered = render_knowledge_file(artifact)
    fm, _ = parse_frontmatter(rendered)
    assert "pin_mode" not in fm
    assert "decay_protected" not in fm
    assert "recall_count" not in fm
    assert "title" not in fm


def test_validate_knowledge_frontmatter_rejects_non_canonical() -> None:
    """Validator rejects files that aren't kind=knowledge or are missing artifact_kind."""
    with pytest.raises(ValueError, match="'kind' must be 'knowledge'"):
        validate_knowledge_frontmatter(
            {
                "id": "x",
                "kind": "memory",
                "created": "2026-04-16T10:00:00Z",
                "artifact_kind": "preference",
            }
        )
    with pytest.raises(ValueError, match="missing required field: artifact_kind"):
        validate_knowledge_frontmatter(
            {"id": "x", "kind": "knowledge", "created": "2026-04-16T10:00:00Z"}
        )


def test_validate_knowledge_frontmatter_description_rules() -> None:
    """Description must be single-line, non-empty, ≤200 chars when present."""
    good = {
        "id": "x",
        "kind": "knowledge",
        "artifact_kind": "preference",
        "created": "2026-04-16T10:00:00Z",
        "description": "fine summary",
    }
    validate_knowledge_frontmatter(good)

    with pytest.raises(ValueError, match="must not contain newlines"):
        validate_knowledge_frontmatter({**good, "description": "line1\nline2"})
    with pytest.raises(ValueError, match="must be ≤200"):
        validate_knowledge_frontmatter({**good, "description": "x" * 201})
