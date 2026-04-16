"""Archive and restore knowledge artifact files.

Archived artifacts live in ``knowledge_dir/_archive/`` and are removed from the
FTS index. Restore moves a file back to the active directory and re-indexes it.
The ``_archive/`` subdir is never traversed by the default top-level loaders
(see ``load_knowledge_artifacts``), so archived files are invisible to recall
but preserved on disk for later restore.

Collisions on the destination filename are resolved by suffixing the stem with
a short counter so archive is always recoverable — rename never clobbers an
existing file on either leg.
"""

from __future__ import annotations

import logging
from pathlib import Path

from co_cli.knowledge._artifact import KnowledgeArtifact
from co_cli.knowledge._store import KnowledgeStore

logger = logging.getLogger(__name__)

_ARCHIVE_SUBDIR = "_archive"
_KNOWLEDGE_SOURCE = "knowledge"
_MAX_COLLISION_SUFFIX = 1000


def _non_colliding_path(dest_dir: Path, filename: str) -> Path:
    """Return a path in ``dest_dir`` that does not overwrite an existing file.

    If ``filename`` is already free, return ``dest_dir / filename``. Otherwise
    append ``-1``, ``-2``, … to the stem until a free slot is found. Bounded
    by ``_MAX_COLLISION_SUFFIX`` to prevent infinite loops on a malformed dir.
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for counter in range(1, _MAX_COLLISION_SUFFIX):
        numbered = dest_dir / f"{stem}-{counter}{suffix}"
        if not numbered.exists():
            return numbered
    raise FileExistsError(
        f"Refusing to place {filename} in {dest_dir}: >{_MAX_COLLISION_SUFFIX} collisions"
    )


def archive_artifacts(
    entries: list[KnowledgeArtifact],
    knowledge_dir: Path,
    knowledge_store: KnowledgeStore | None = None,
) -> int:
    """Move artifact files to ``knowledge_dir/_archive/`` and remove them from the FTS index.

    Creates the ``_archive/`` subdirectory on demand. Entries whose source file
    no longer exists are logged and skipped. Filename collisions inside
    ``_archive/`` are resolved by appending a numeric suffix so prior archives
    are never clobbered. Returns the number of artifacts actually archived
    (missing files excluded from the count).
    """
    archive_dir = knowledge_dir / _ARCHIVE_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived = 0
    for entry in entries:
        source_path = entry.path
        if not source_path.exists():
            logger.warning("archive_artifacts: source missing, skipping: %s", source_path)
            continue

        original_path_str = str(source_path)
        dest_path = _non_colliding_path(archive_dir, source_path.name)
        if dest_path.name != source_path.name:
            logger.info(
                "archive_artifacts: renaming to avoid collision: %s -> %s",
                source_path.name,
                dest_path.name,
            )
        source_path.rename(dest_path)

        if knowledge_store is not None:
            knowledge_store.remove(_KNOWLEDGE_SOURCE, original_path_str)

        archived += 1

    return archived


def restore_artifact(
    slug: str,
    knowledge_dir: Path,
    knowledge_store: KnowledgeStore | None = None,
) -> bool:
    """Move an archived file whose filename starts with ``slug`` back to the active dir.

    Searches ``knowledge_dir/_archive/`` for files whose name starts with the
    given slug. Returns True on a single unambiguous match (file moved back
    and re-indexed if a store is provided). Returns False if zero matches or
    multiple matches (caller must disambiguate). Filename collisions against
    the active directory are resolved by appending a numeric suffix so
    restore never clobbers an existing artifact.
    """
    archive_dir = knowledge_dir / _ARCHIVE_SUBDIR
    if not archive_dir.exists():
        return False

    matches = [path for path in archive_dir.glob(f"{slug}*") if path.is_file()]
    if len(matches) != 1:
        if len(matches) > 1:
            logger.warning(
                "restore_artifact: ambiguous slug %r matched %d files: %s",
                slug,
                len(matches),
                [str(path) for path in matches],
            )
        return False

    source_path = matches[0]
    dest_path = _non_colliding_path(knowledge_dir, source_path.name)
    if dest_path.name != source_path.name:
        logger.info(
            "restore_artifact: renaming to avoid collision: %s -> %s",
            source_path.name,
            dest_path.name,
        )
    source_path.rename(dest_path)

    if knowledge_store is not None:
        knowledge_store.sync_dir(_KNOWLEDGE_SOURCE, knowledge_dir, glob="*.md")

    return True
