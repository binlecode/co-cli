"""Behavioral tests for knowledge_manage — delete action and approval subjects.

Exercises: delete removes artifact file, delete on missing file → tool_error,
approval subject shape for each action, and post-delete search absence.
No LLM — real filesystem + real FTS5 only.
"""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.memory_store import MemoryStore
from co_cli.memory.service import reindex, save_artifact
from co_cli.tools.memory.manage import (
    _knowledge_manage_approval_subject,
    knowledge_manage,
)
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, name: str = "test-search.db") -> MemoryStore:
    return MemoryStore(config=SETTINGS, memory_db_path=tmp_path / name)


def _make_deps(tmp_path: Path, store: MemoryStore | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        knowledge_dir=tmp_path / "knowledge",
        memory_store=store,
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


# ---------------------------------------------------------------------------
# Tests — delete action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_manage_delete_removes_file(tmp_path: Path) -> None:
    """knowledge_manage(action='delete') must remove the artifact file from disk.

    Regression guard: if delete is a no-op or uses the wrong path, the file
    persists and the artifact continues to appear in searches.
    """
    knowledge_dir = tmp_path / "knowledge"
    saved = save_artifact(
        knowledge_dir,
        content="content to be deleted",
        artifact_kind="note",
        title="delete me",
    )
    assert saved.path.exists(), "precondition: artifact file must exist before delete"

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await knowledge_manage(ctx, action="delete", name=saved.filename_stem)

    assert not saved.path.exists(), "artifact file must be removed after delete"
    assert result.metadata is not None
    assert result.metadata.get("error") is not True, "successful delete must not set error flag"
    assert result.metadata.get("action") == "deleted"


@pytest.mark.asyncio
async def test_artifact_manage_delete_missing_artifact_returns_error(tmp_path: Path) -> None:
    """knowledge_manage(action='delete') on a non-existent name must return tool_error.

    Regression guard: a silent no-op on missing names would mask typos in
    filename_stem and leave the caller thinking the delete succeeded.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await knowledge_manage(ctx, action="delete", name="nonexistent-artifact-xyz")

    assert result.metadata is not None, "tool_error must populate metadata"
    assert result.metadata.get("error") is True, "tool_error must set error=True in metadata"
    assert "nonexistent-artifact-xyz" in result.return_value, (
        "error message must include the bad name so the caller can diagnose it"
    )


@pytest.mark.asyncio
async def test_artifact_manage_delete_removes_from_index(tmp_path: Path) -> None:
    """knowledge_manage(action='delete') must remove the artifact from the FTS5 index.

    Regression guard: if the index entry is not removed, memory_search would
    continue returning a result whose file no longer exists.
    """
    knowledge_dir = tmp_path / "knowledge"
    store = _make_store(tmp_path)
    try:
        saved = save_artifact(
            knowledge_dir,
            content="uniquetoken_to_find_in_index",
            artifact_kind="note",
            title="indexed note",
        )
        reindex(
            store,
            saved.path,
            saved.content,
            saved.markdown_content,
            saved.frontmatter_dict,
            saved.filename_stem,
            chunk_tokens=600,
            chunk_overlap_tokens=80,
        )

        hits_before = store.search("uniquetoken_to_find_in_index")
        assert any(saved.filename_stem in h.path for h in hits_before), (
            "precondition: artifact must be findable in index before delete"
        )

        deps = _make_deps(tmp_path, store=store)
        ctx = _make_ctx(deps)
        await knowledge_manage(ctx, action="delete", name=saved.filename_stem)

        hits_after = store.search("uniquetoken_to_find_in_index")
        assert not any(saved.filename_stem in h.path for h in hits_after), (
            "artifact must not appear in FTS5 index after delete"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Tests — create action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_manage_create_rejects_canon_artifact_kind(tmp_path: Path) -> None:
    """knowledge_manage(action='create') must reject artifact_kind='canon' — canon is read-only.

    Regression guard: adding CANON to ArtifactKindEnum would silently admit it as a writable
    kind without this check.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        knowledge_dir=tmp_path / "knowledge",
    )
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    result = await knowledge_manage(
        ctx, action="create", name="test", content="test content", kind="canon"
    )

    assert "canon" in result.return_value.lower(), "error message must mention the rejected kind"
    assert result.metadata is not None, "tool_error must populate metadata"
    assert result.metadata.get("error") is True, "tool_error must set error=True in metadata"


# ---------------------------------------------------------------------------
# Tests — approval subjects
# ---------------------------------------------------------------------------


def test_approval_subject_create_shape() -> None:
    """Approval subject for action='create' must use the tool:knowledge_manage:create:<name> key.

    Regression guard: wrong key format breaks session-level approval rules —
    a remembered approval would not match future calls.
    """
    subject = _knowledge_manage_approval_subject({"action": "create", "name": "my-note"})

    assert subject.tool_name == "knowledge_manage"
    assert subject.value == "tool:knowledge_manage:create:my-note"
    assert subject.can_remember is True


def test_approval_subject_delete_shape() -> None:
    """Approval subject for action='delete' must use the tool:knowledge_manage:delete:<name> key."""
    subject = _knowledge_manage_approval_subject({"action": "delete", "name": "old-note"})

    assert subject.value == "tool:knowledge_manage:delete:old-note"


def test_approval_subject_append_shape() -> None:
    """Approval subject for action='append' must use the tool:knowledge_manage:append:<name> key."""
    subject = _knowledge_manage_approval_subject({"action": "append", "name": "existing-note"})

    assert subject.value == "tool:knowledge_manage:append:existing-note"


def test_approval_subject_replace_shape() -> None:
    """Approval subject for action='replace' must use the tool:knowledge_manage:replace:<name> key."""
    subject = _knowledge_manage_approval_subject({"action": "replace", "name": "existing-note"})

    assert subject.value == "tool:knowledge_manage:replace:existing-note"
