"""Canonical atomic file-write helper for full-overwrite mutations across co_cli."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write content to path atomically via tempfile + os.replace.

    Tempfile lives in path.parent so os.replace stays on the same filesystem
    (required for atomic rename on POSIX). On exception at any point — entering
    the context, writing content, or os.replace — the tempfile is cleaned up.
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, suffix=".tmp", delete=False, encoding=encoding
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
