"""File-based lexical search over session transcripts.

Ripgrep (fixed-string, case-insensitive) over ``sessions_dir/*.jsonl``, with a
Python line-scan fallback when ``rg`` is absent. Matched JSONL lines are mapped
back to readable message content via ``extract_messages`` and returned as
``SessionHit`` records. ``SessionHit.path`` carries the session uuid8 (not a
filesystem path) to match the attribute contract the recall tool consumes.

Matching is a case-insensitive substring of the raw, on-disk JSONL line. co
writes transcripts via pydantic-core ``dump_json`` (literal UTF-8 — only ``"``
and ``\\`` are escaped), so unicode / accented / CJK queries match the raw text.
A match that lands only on a structural JSON key/value — not inside any retained
message part's content — is dropped: there is no readable snippet to cite.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from co_cli.session.filename import parse_session_filename
from co_cli.session.transcript import ExtractedMessage, extract_messages
from co_cli.tools.shell_env import build_subprocess_env

logger = logging.getLogger(__name__)

_SOURCE_LABEL = "session"


@dataclass
class SessionHit:
    """One ranked session-search result.

    Field names mirror the subset of the index SearchResult the recall tool
    reads, so the recall logic is unchanged. ``path`` carries the session uuid8,
    not a filesystem path. ``start_line`` / ``end_line`` are 1-indexed JSONL
    lines (matching the ``session_view`` line contract).
    """

    path: str
    snippet: str
    start_line: int
    end_line: int
    created_at: str
    source: str
    score: float


def search_sessions(sessions_dir: Path, query: str, limit: int) -> list[SessionHit]:
    """Lexically search session transcripts; return ranked ``SessionHit`` records.

    Ranking is (match_count desc, recency desc); ``score`` is the match count.
    """
    query = query.strip()
    if not query or not sessions_dir.exists():
        return []

    matches = _ripgrep_line_matches(sessions_dir, query)
    if matches is None:
        matches = _python_line_matches(sessions_dir, query)

    hits = _build_hits(matches, query)
    hits.sort(key=lambda h: (h.score, h.created_at), reverse=True)
    return hits[:limit]


def _ripgrep_line_matches(sessions_dir: Path, query: str) -> dict[Path, list[int]] | None:
    """Run rg; return {file: [0-indexed matched line]}, or None if rg is unavailable.

    Mirrors ``tools/files/read.py``'s invocation hygiene: sanitized env and
    ``--no-config`` so the user's ripgrep.toml cannot interfere. ``--no-ignore``
    and ``--hidden`` keep parity with the Python fallback, which globs every
    file regardless of ignore rules. Exit code 1 (no match) is success with
    empty output.
    """
    if shutil.which("rg") is None:
        return None
    args = [
        "rg",
        "--null",
        "--line-number",
        "--no-heading",
        "--with-filename",
        "--fixed-strings",
        "--ignore-case",
        "--no-config",
        "--no-ignore",
        "--hidden",
        "-e",
        query,
        "--glob",
        "*.jsonl",
        str(sessions_dir),
    ]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            env=build_subprocess_env(),
            check=False,
        )
    except Exception:
        return None
    if proc.returncode not in (0, 1):
        return None
    return _parse_rg_output(proc.stdout)


def _parse_rg_output(stdout: bytes) -> dict[Path, list[int]]:
    """Parse ``path\\0lineno:text`` rg output into {file: [0-indexed matched line]}."""
    matches: dict[Path, list[int]] = {}
    for raw in stdout.split(b"\n"):
        if not raw:
            continue
        nul = raw.find(b"\0")
        if nul == -1:
            continue
        rest = raw[nul + 1 :]
        colon = rest.find(b":")
        if colon == -1:
            continue
        try:
            lineno = int(rest[:colon])
        except ValueError:
            continue
        path = Path(raw[:nul].decode("utf-8", errors="replace"))
        matches.setdefault(path, []).append(lineno - 1)
    return matches


def _python_line_matches(sessions_dir: Path, query: str) -> dict[Path, list[int]]:
    """rg-absent fallback: case-insensitive substring scan over each JSONL file."""
    needle = query.lower()
    matches: dict[Path, list[int]] = {}
    for path in sessions_dir.glob("*.jsonl"):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    if needle in line.lower():
                        matches.setdefault(path, []).append(idx)
        except (OSError, UnicodeDecodeError):
            continue
    return matches


def _build_hits(matches: dict[Path, list[int]], query: str) -> list[SessionHit]:
    """Map matched JSONL lines to readable snippets, dropping structural-only matches.

    For each matched line, select the retained message part whose content
    contains the raw query (case-insensitive). A line that matched only on a
    structural JSON key/value yields no such part and is skipped; a session with
    no content-bearing match produces no hit (CD-M-1).
    """
    needle = query.lower()
    hits: list[SessionHit] = []
    for path, line_indices in matches.items():
        parsed = parse_session_filename(path.name)
        if parsed is None:
            continue
        uuid8, created_at = parsed

        by_line: dict[int, list[ExtractedMessage]] = {}
        for message in extract_messages(path):
            by_line.setdefault(message.line_index, []).append(message)

        snippet: str | None = None
        first_line: int | None = None
        content_match_count = 0
        for line_index in line_indices:
            part = next(
                (m for m in by_line.get(line_index, []) if needle in m.content.lower()),
                None,
            )
            if part is None:
                continue
            content_match_count += 1
            if snippet is None:
                snippet = part.content
                first_line = line_index

        if snippet is None or first_line is None:
            continue

        hits.append(
            SessionHit(
                path=uuid8,
                snippet=snippet,
                start_line=first_line + 1,
                end_line=first_line + 1,
                created_at=created_at.isoformat(),
                source=_SOURCE_LABEL,
                score=float(content_match_count),
            )
        )
    return hits
