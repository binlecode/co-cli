"""Relevance-floor + lexical-mode + runtime-degradation behavior at the search boundary.

TASK-1 (vector-similarity floor) and TASK-2 (reranker-score floor) need real
embeddings + a real cross-encoder, so those cases skip when TEI is unreachable.
TASK-3 (lexical mode skips the reranker; hybrid degradation emits a span event)
runs without TEI: a recording HTTP server proves non-invocation, and a closed
embed port forces the documented fallback.
"""

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.index.store import IndexStore
from co_cli.memory.service import reindex, save_memory_item
from co_cli.observability import tracing


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


_TEI_UP = _port_open("127.0.0.1", 8283) and _port_open("127.0.0.1", 8282)
_needs_tei = pytest.mark.skipif(not _TEI_UP, reason="TEI embed/rerank services not reachable")


def _seed(memory_dir: Path, index: IndexStore, *, title: str, body: str) -> None:
    r = save_memory_item(memory_dir, content=body, memory_kind="note", title=title)
    reindex(
        index,
        r.path,
        r.content,
        r.markdown_content,
        r.frontmatter_dict,
        r.filename_stem,
        chunk_tokens=600,
        chunk_overlap_tokens=80,
    )


def _hybrid_settings():
    return SETTINGS.model_copy(
        update={"memory": SETTINGS.memory.model_copy(update={"search_backend": "hybrid"})}
    )


def _fts5_settings(**memory_overrides):
    base = {"search_backend": "fts5", "embedding_provider": "none"}
    base.update(memory_overrides)
    return SETTINGS.model_copy(update={"memory": SETTINGS.memory.model_copy(update=base)})


# ---------------------------------------------------------------------------
# TASK-1 / TASK-2 — relevance floors (real TEI)
# ---------------------------------------------------------------------------


@_needs_tei
def test_no_match_query_returns_nothing_real_lexical_hit_survives(tmp_path: Path) -> None:
    """A token-disjoint no-match query yields 0 results; a lexical match still returns."""
    mem = tmp_path / "memory"
    index = IndexStore(config=_hybrid_settings(), db_path=tmp_path / "search.db")
    try:
        _seed(
            mem,
            index,
            title="deploy runbook",
            body="SILVER-FALCON deployment rollout canary kubernetes rollback dashboards.",
        )
        _seed(
            mem,
            index,
            title="sensor pipeline",
            body="sensor telemetry ingestion columnar storage temperature humidity compaction.",
        )

        no_match, _ = index.search("airline flight booking itinerary", limit=10)
        assert no_match == [], f"expected no junk hits, got {[r.path for r in no_match]}"

        lexical, _ = index.search("deployment rollback", limit=10)
        assert any("deploy" in r.path for r in lexical), "real lexical hit must survive the floor"
    finally:
        index.close()


def _hybrid_with_floor(floor: float):
    return SETTINGS.model_copy(
        update={
            "memory": SETTINGS.memory.model_copy(
                update={"search_backend": "hybrid", "rerank_score_floor": floor}
            )
        }
    )


@_needs_tei
def test_reranker_floor_culls_by_score(tmp_path: Path) -> None:
    """Raising rerank_score_floor (only that) culls reranked hits the default keeps."""
    docs = [
        ("sales report", "quarterly sales revenue figures bookings pipeline growth"),
        ("weather report", "weather rain forecast humidity wind temperature outlook"),
        ("incident report", "server outage downtime postmortem mitigation rollback"),
    ]
    query = "quarterly sales revenue"

    default_index = IndexStore(config=_hybrid_with_floor(0.2), db_path=tmp_path / "lo.db")
    try:
        for title, body in docs:
            _seed(tmp_path / "lo_memory", default_index, title=title, body=body)
        default_hits, _ = default_index.search(query, limit=10)
    finally:
        default_index.close()

    high_index = IndexStore(config=_hybrid_with_floor(99.0), db_path=tmp_path / "hi.db")
    try:
        for title, body in docs:
            _seed(tmp_path / "hi_memory", high_index, title=title, body=body)
        high_hits, _ = high_index.search(query, limit=10)
    finally:
        high_index.close()

    assert any("sales" in r.path for r in default_hits), (
        "the on-topic hit must survive the default floor"
    )
    assert all(r.score >= 0.2 for r in default_hits), "no returned hit may score below the floor"
    assert len(high_hits) < len(default_hits), (
        "an unreachable floor must cull more than the default"
    )


# ---------------------------------------------------------------------------
# TASK-3 — lexical mode skips reranker; hybrid degradation emits an event
# ---------------------------------------------------------------------------


class _RecordingHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_POST(self) -> None:
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args) -> None:
        pass


def test_fts5_mode_never_calls_the_reranker(tmp_path: Path) -> None:
    """search_backend=fts5 issues zero calls to a configured reranker URL."""
    _RecordingHandler.hits = 0
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = _fts5_settings(cross_encoder_reranker_url=f"http://127.0.0.1:{port}")
        index = IndexStore(config=settings, db_path=tmp_path / "search.db")
        try:
            _seed(
                tmp_path / "memory",
                index,
                title="note",
                body="event pipeline columnar analytics review ledger",
            )
            hits, _ = index.search("event pipeline", limit=5)
            assert hits, "expected a lexical hit"
        finally:
            index.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert _RecordingHandler.hits == 0, "fts5 mode must not invoke the reranker"


def test_hybrid_degradation_to_fts_emits_span_event(tmp_path: Path) -> None:
    """When the embedder is unreachable, a hybrid query emits the degradation event."""
    log = tmp_path / "spans.jsonl"
    tracing._SPAN_STACK.set(())
    tracing.setup_log(log)
    try:
        settings = SETTINGS.model_copy(
            update={
                "memory": SETTINGS.memory.model_copy(
                    update={
                        "search_backend": "hybrid",
                        "embedding_provider": "tei",
                        "embed_api_url": "http://127.0.0.1:1",
                        "cross_encoder_reranker_url": None,
                    }
                )
            }
        )
        index = IndexStore(config=settings, db_path=tmp_path / "search.db")
        try:
            _seed(
                tmp_path / "memory",
                index,
                title="note",
                body="event pipeline columnar analytics review ledger",
            )
            index.search("event pipeline", limit=5)
        finally:
            index.close()
    finally:
        for handler in logging.getLogger("co_cli.observability.spans").handlers:
            handler.flush()

    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    search_spans = [r for r in records if r["name"] == "index.search"]
    assert search_spans, "expected an index.search span"
    event_names = {e["name"] for r in search_spans for e in r.get("events", [])}
    assert "index.hybrid_degraded_to_fts" in event_names
