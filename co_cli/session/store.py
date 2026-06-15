"""SessionStore — file-based store over session transcripts.

Past session transcripts live as JSONL files in ``sessions_dir``. Search is
lexical (ripgrep over the raw files, see ``_search.py``) — there is no index,
chunk pipeline, or embedding. ``count`` is the number of transcript files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.session._search import SessionHit, search_sessions

if TYPE_CHECKING:
    from co_cli.config.core import Settings

logger = logging.getLogger(__name__)


class SessionStore:
    """Domain store for past session transcripts (file-based, no index)."""

    def __init__(self, *, config: Settings, sessions_dir: Path) -> None:
        self._config = config
        self._sessions_dir = sessions_dir

    def search(self, query: str, limit: int) -> list[SessionHit]:
        """Lexical ripgrep search over session transcripts."""
        return search_sessions(self._sessions_dir, query, limit)

    def count(self) -> int:
        """Number of session transcript files on disk."""
        if not self._sessions_dir.exists():
            return 0
        return sum(1 for _ in self._sessions_dir.glob("*.jsonl"))
