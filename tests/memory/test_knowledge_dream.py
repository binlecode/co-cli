"""Tests for dream-cycle state persistence (TASK-5.3)."""

from __future__ import annotations

import json
from pathlib import Path

from co_cli.memory.dream import (
    DreamState,
    DreamStats,
    _chunk_dream_window,
    dream_state_path,
    load_dream_state,
    save_dream_state,
)


def test_load_dream_state_returns_default_when_missing(tmp_path: Path) -> None:
    state = load_dream_state(tmp_path)

    assert state.last_dream_at is None
    assert state.processed_sessions == []
    assert state.stats == DreamStats()


def test_save_then_load_roundtrips_all_fields(tmp_path: Path) -> None:
    original = DreamState(
        last_dream_at="2026-04-16T22:00:00+00:00",
        processed_sessions=["session-a.jsonl", "session-b.jsonl"],
        stats=DreamStats(
            total_cycles=3,
            total_extracted=12,
            total_merged=4,
            total_decayed=7,
        ),
    )

    save_dream_state(tmp_path, original)
    reloaded = load_dream_state(tmp_path)

    assert reloaded == original


def test_save_dream_state_creates_missing_knowledge_dir(tmp_path: Path) -> None:
    nested = tmp_path / "fresh_knowledge"
    assert not nested.exists()

    save_dream_state(nested, DreamState(last_dream_at="2026-04-16T22:00:00+00:00"))

    assert dream_state_path(nested).exists()


def test_load_dream_state_ignores_corrupt_file(tmp_path: Path) -> None:
    dream_state_path(tmp_path).write_text("{ not valid json", encoding="utf-8")

    state = load_dream_state(tmp_path)

    assert state == DreamState()


def test_save_dream_state_writes_indented_json(tmp_path: Path) -> None:
    save_dream_state(
        tmp_path,
        DreamState(last_dream_at="2026-04-16T22:00:00+00:00"),
    )

    payload = json.loads(dream_state_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["last_dream_at"] == "2026-04-16T22:00:00+00:00"
    assert payload["processed_sessions"] == []
    assert payload["stats"]["total_cycles"] == 0


def test_dream_stats_increment_and_persist(tmp_path: Path) -> None:
    state = load_dream_state(tmp_path)
    state.stats.total_cycles += 1
    state.stats.total_extracted += 5
    state.processed_sessions.append("2026-04-16-session.jsonl")
    save_dream_state(tmp_path, state)

    reloaded = load_dream_state(tmp_path)

    assert reloaded.stats.total_cycles == 1
    assert reloaded.stats.total_extracted == 5
    assert reloaded.processed_sessions == ["2026-04-16-session.jsonl"]


# ---------------------------------------------------------------------------
# _chunk_dream_window
# ---------------------------------------------------------------------------


def test_chunk_dream_window_returns_single_chunk_when_under_soft_limit() -> None:
    window = "x" * 100
    chunks = _chunk_dream_window(window)
    assert chunks == [window]


def test_chunk_dream_window_at_soft_limit_stays_single_chunk() -> None:
    # Exactly 16000 chars — at the limit, no split (<=)
    window = "x" * 16_000
    chunks = _chunk_dream_window(window)
    assert len(chunks) == 1
    assert chunks[0] == window


def test_chunk_dream_window_oversized_produces_multiple_overlapping_chunks() -> None:
    # 30000 chars: step=10000, chunk_size=12000, overlap=2000
    # chunk1: [0:12000], chunk2: [10000:22000], chunk3: [20000:30000]
    window = "A" * 10_000 + "B" * 10_000 + "C" * 10_000
    chunks = _chunk_dream_window(window)

    assert len(chunks) == 3
    assert chunks[0] == "A" * 10_000 + "B" * 2_000
    assert chunks[1] == "B" * 10_000 + "C" * 2_000
    assert chunks[2] == "C" * 10_000
    # overlap: tail of chunk[i] equals head of chunk[i+1]
    assert chunks[0][-2_000:] == chunks[1][:2_000]
    assert chunks[1][-2_000:] == chunks[2][:2_000]


def test_chunk_dream_window_first_and_last_chars_covered() -> None:
    window = "START" + "m" * 20_000 + "END"
    chunks = _chunk_dream_window(window)
    assert chunks[0].startswith("START")
    assert chunks[-1].endswith("END")
