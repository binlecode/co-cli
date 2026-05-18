"""Atomic full-file overwrite primitives for co_cli."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> None:
    """Atomic full-file overwrite. Creates parent dirs as needed.

    Tempfile lives in path.parent so os.replace stays on the same filesystem
    (required for atomic rename on POSIX). On exception at any point — entering
    the context, writing content, or os.replace — the tempfile is cleaned up
    and the target is untouched (left as old content or absent).

    Pass errors="replace" when writing content from arbitrary subprocess
    output that may contain non-UTF-8 bytes (see tools/tool_io.py).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
            encoding=encoding,
            errors=errors,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Binary variant — same atomicity contract, no encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, suffix=".tmp", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
