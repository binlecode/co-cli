"""Archive and restore memory artifact files.

Archived artifacts live in ``memory_dir/_archive/`` and are removed from the
FTS index. Restore moves a file back to the active directory and re-indexes it.
The ``_archive/`` subdir is never traversed by the default top-level loaders
(see ``load_memory_items``), so archived files are invisible to recall
but preserved on disk for later restore.

Collisions on the destination filename are resolved by suffixing the stem with
a short counter so archive is always recoverable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.memory.item import MemoryItem

if TYPE_CHECKING:
    from co_cli.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_ARCHIVE_SUBDIR = "_archive"
_MAX_COLLISION_SUFFIX = 1000


def _non_colliding_path(dest_dir: Path, filename: str) -> Path:
    """Return a path in ``dest_dir`` that does not overwrite an existing file."""
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
    entries: list[MemoryItem],
    memory_dir: Path,
    memory_store: MemoryStore | None = None,
) -> int:
    """Move artifact files to ``memory_dir/_archive/`` and remove from the index.

    Creates the ``_archive/`` subdirectory on demand. Entries whose source file
    no longer exists are logged and skipped. Filename collisions inside
    ``_archive/`` are resolved by appending a numeric suffix so prior archives
    are never clobbered. Returns the number of artifacts actually archived.
    """
    archive_dir = memory_dir / _ARCHIVE_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived = 0
    for entry in entries:
        source_path = entry.path
        if not source_path.exists():
            logger.warning("archive_artifacts: source missing, skipping: %s", source_path)
            continue

        original_path = source_path
        dest_path = _non_colliding_path(archive_dir, source_path.name)
        if dest_path.name != source_path.name:
            logger.info(
                "archive_artifacts: renaming to avoid collision: %s -> %s",
                source_path.name,
                dest_path.name,
            )
        source_path.rename(dest_path)

        if memory_store is not None:
            memory_store.remove(original_path)

        archived += 1

    return archived


def restore_artifact(
    slug: str,
    memory_dir: Path,
    memory_store: MemoryStore | None = None,
) -> bool:
    """Move an archived file whose filename starts with ``slug`` back to active.

    Returns True on a single unambiguous match. Returns False if zero matches
    or multiple matches.
    """
    archive_dir = memory_dir / _ARCHIVE_SUBDIR
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
    dest_path = _non_colliding_path(memory_dir, source_path.name)
    if dest_path.name != source_path.name:
        logger.info(
            "restore_artifact: renaming to avoid collision: %s -> %s",
            source_path.name,
            dest_path.name,
        )
    source_path.rename(dest_path)

    if memory_store is not None:
        memory_store.sync_dir(memory_dir, glob="*.md")

    return True
