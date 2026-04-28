"""Skill install operations — fetch, write, discover, and inspect skill files.

Pure I/O layer with no console output, prompts, or CLI context.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.skills.loader import _inject_source_url

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 15


class SkillFetchError(Exception):
    """Raised when fetch_skill_content fails (network, IO, or content-type)."""


def fetch_skill_content(target: str) -> tuple[str, str]:
    """Fetch a skill .md from URL (http/https) or local path.

    Returns (content, filename). For URL targets, calls _inject_source_url
    so the source is preserved in frontmatter.
    Raises SkillFetchError on network failure, non-text content-type,
    read error, or filename not ending in .md.
    """
    if target.startswith("http://") or target.startswith("https://"):
        return _fetch_from_url(target)
    return _fetch_from_path(target)


def _fetch_from_url(url: str) -> tuple[str, str]:
    """Fetch skill content from an http/https URL."""
    try:
        response = httpx.get(url, timeout=_FETCH_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SkillFetchError(f"Network error fetching {url!r}: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    mime = content_type.split(";")[0].strip().lower()
    if mime and not mime.startswith("text/"):
        raise SkillFetchError(
            f"Unexpected content-type {content_type!r} for {url!r}; expected text/*"
        )

    parsed = urlparse(url)
    filename = Path(parsed.path).name
    if not filename.endswith(".md"):
        raise SkillFetchError(
            f"URL path does not resolve to a .md filename: {url!r} (got {filename!r})"
        )

    content = _inject_source_url(response.text, url)
    return content, filename


def _fetch_from_path(target: str) -> tuple[str, str]:
    """Fetch skill content from a local file path."""
    path = Path(target)
    filename = path.name
    if not filename.endswith(".md"):
        raise SkillFetchError(
            f"Local path does not point to a .md file: {target!r} (got {filename!r})"
        )
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillFetchError(f"Read error for {target!r}: {exc}") from exc
    return content, filename


def write_skill_file(content: str, filename: str, dest_dir: Path) -> Path:
    """Create dest_dir if needed; write content to dest_dir/filename. Returns dest path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    dest_path.write_text(content, encoding="utf-8")
    return dest_path


def find_skill_source_url(skill_path: Path) -> str | None:
    """Read skill_path frontmatter; return source-url field if present and non-empty, else None."""
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _ = parse_frontmatter(content)
    value = meta.get("source-url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def discover_skill_files(bundled_dir: Path, user_dir: Path) -> list[Path]:
    """Return sorted .md paths from both dirs (bundled first, user second).

    Skips dirs that don't exist.
    """
    result: list[Path] = []
    if bundled_dir.exists():
        result.extend(sorted(bundled_dir.glob("*.md")))
    if user_dir.exists():
        result.extend(sorted(user_dir.glob("*.md")))
    return result


def read_skill_meta(skill_path: Path) -> dict:
    """Read frontmatter metadata from a skill .md file. Returns {} on any read or parse error."""
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    meta, _ = parse_frontmatter(content)
    return meta
