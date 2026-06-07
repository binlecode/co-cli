"""File system guards and utilities for file tools."""

from pathlib import Path


def enforce_read_boundary(path: Path, roots: list[Path]) -> tuple[Path, Path]:
    """Resolve path against a list of read roots; accept the first that contains it.

    The single-base join-then-resolve guard lifted over a list. For each root in
    order, compute ``(root / path).resolve()`` and accept the first whose result
    is_relative_to that root; return ``(resolved, root)``.

    Consequences of the ``(root / path)`` join: an absolute path ignores the left
    operand and passes through, accepted under whichever root contains it (the
    multi-root display form); a relative path anchors to ``roots[0]`` (always
    is_relative_to it barring ``..`` escape), so single-root relative resolution
    is byte-identical to the old single-base behavior.

    No filesystem-existence probing — pure boundary logic. Blocks ``..`` traversal
    and in-vault symlinks whose resolved target is under no root (the post-resolve
    is_relative_to check; ``.resolve()`` follows the link out of bounds).

    Raises ValueError if no root contains the path.
    """
    for root in roots:
        resolved = (root / path).resolve()
        if resolved.is_relative_to(root.resolve()):
            return resolved, root
    raise ValueError(f"Path escapes all read roots: {path}")


def enforce_write_boundary(path: Path, workspace_dir: Path) -> Path:
    """Resolve path against workspace_dir and verify it stays within.

    Handles both absolute and workspace-relative paths: ``(workspace_dir / path)``
    leaves an absolute path unchanged and joins a relative one, so file_write /
    file_patch land at the correct absolute location with no pre-resolution step.

    Raises ValueError if path escapes workspace.
    """
    resolved = (workspace_dir / path).resolve()
    if not resolved.is_relative_to(workspace_dir.resolve()):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def safe_mtime(p: Path) -> float:
    """Return file mtime, falling back to 0.0 for broken symlinks or inaccessible paths."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def detect_encoding(path: Path) -> str:
    """Detect file encoding from BOM prefix — returns 'utf-16' or 'utf-8'."""
    with open(path, "rb") as fh:
        raw = fh.read(2048)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    return "utf-8"


def is_recursive_pattern(pattern: str) -> bool:
    """Return True when the glob pattern requires recursive traversal."""
    return "**" in pattern or "/" in pattern
