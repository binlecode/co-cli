"""Helios storage layer — SQLite-backed event store.

This is the module slated for the SQLite -> DuckDB migration (see the
architecture review). All query paths go through ``connect()`` and the
``sqlite3`` row factory below.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "helios.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def recent_events(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT id, ts, kind, payload FROM events ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()


def event_counts_by_kind() -> dict[str, int]:
    with connect() as conn:
        cur = conn.execute("SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")
        return {row["kind"]: row["n"] for row in cur.fetchall()}
