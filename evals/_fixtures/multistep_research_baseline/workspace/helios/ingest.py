"""Helios ingest path — writes raw events into the SQLite event store."""

import json
import time

from db import connect


def insert_event(kind: str, payload: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (time.time(), kind, json.dumps(payload)),
        )
        conn.commit()
        return cur.lastrowid


def bulk_insert(events: list[tuple[str, dict]]) -> int:
    rows = [(time.time(), kind, json.dumps(payload)) for kind, payload in events]
    with connect() as conn:
        conn.executemany("INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", rows)
        conn.commit()
        return len(rows)


def vacuum() -> None:
    """Reclaim space — SQLite-specific maintenance run nightly."""
    with connect() as conn:
        conn.execute("VACUUM")
