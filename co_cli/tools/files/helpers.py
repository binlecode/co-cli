"""Shared helpers for file system tools."""

from pathlib import Path


def _enforce_workspace_boundary(path: Path, workspace_root: Path) -> Path:
    """Resolve path against workspace_root and verify it stays within.

    Defense in depth: CoToolLifecycle.before_tool_execute pre-resolves paths,
    but this function handles both pre-resolved (absolute) and raw (relative)
    paths as a safety net.

    Raises ValueError if path escapes workspace.
    """
    resolved = (workspace_root / path).resolve()
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def _safe_mtime(p: Path) -> float:
    """Return file mtime, falling back to 0.0 for broken symlinks or inaccessible paths."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _detect_encoding(path: Path) -> str:
    """Detect file encoding from BOM prefix — returns 'utf-16' or 'utf-8'."""
    with open(path, "rb") as fh:
        raw = fh.read(2048)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    return "utf-8"


def _is_recursive_pattern(pattern: str) -> bool:
    """Return True when the glob pattern requires recursive traversal."""
    return "**" in pattern or "/" in pattern
