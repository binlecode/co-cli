"""Tests for ``co trace <trace_id>`` snapshot tree view."""

import json
from pathlib import Path

from co_cli.observability import trace_view


def _write_record(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _make_record(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    name: str,
    kind: str = "co",
    start_ts: str = "2026-05-17T00:00:00.000000Z",
    duration_ms: float = 1.0,
    attributes: dict | None = None,
    status: str = "OK",
) -> dict:
    return {
        "ts": start_ts,
        "schema_version": 1,
        "session_id": None,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "start_ts": start_ts,
        "duration_ms": duration_ms,
        "status": status,
        "status_msg": None,
        "attributes": attributes or {},
        "events": [],
    }


def test_read_records_filters_by_trace_id(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    _write_record(
        log, _make_record(trace_id="t_a", span_id="s_1", parent_span_id=None, name="root_a")
    )
    _write_record(
        log, _make_record(trace_id="t_b", span_id="s_2", parent_span_id=None, name="root_b")
    )
    _write_record(
        log, _make_record(trace_id="t_a", span_id="s_3", parent_span_id="s_1", name="child_a")
    )

    records = trace_view._read_records_for_trace("t_a", log_path=log)
    assert len(records) == 2
    assert {r["name"] for r in records} == {"root_a", "child_a"}


def test_read_records_includes_rotated_backups(tmp_path: Path) -> None:
    base = tmp_path / "spans.jsonl"
    backup = tmp_path / "spans.jsonl.1"
    _write_record(
        backup,
        _make_record(trace_id="t_a", span_id="s_1", parent_span_id=None, name="from_backup"),
    )
    _write_record(
        base, _make_record(trace_id="t_a", span_id="s_2", parent_span_id="s_1", name="from_live")
    )

    records = trace_view._read_records_for_trace("t_a", log_path=base)
    names = {r["name"] for r in records}
    assert names == {"from_backup", "from_live"}


def test_build_tree_parent_child_structure(tmp_path: Path) -> None:
    records = [
        _make_record(
            trace_id="t",
            span_id="root",
            parent_span_id=None,
            name="root",
            start_ts="2026-05-17T00:00:00.000000Z",
        ),
        _make_record(
            trace_id="t",
            span_id="child2",
            parent_span_id="root",
            name="child2",
            start_ts="2026-05-17T00:00:02.000000Z",
        ),
        _make_record(
            trace_id="t",
            span_id="child1",
            parent_span_id="root",
            name="child1",
            start_ts="2026-05-17T00:00:01.000000Z",
        ),
        _make_record(
            trace_id="t",
            span_id="grand",
            parent_span_id="child1",
            name="grand",
            start_ts="2026-05-17T00:00:01.500000Z",
        ),
    ]
    roots = trace_view._build_tree(records)
    assert len(roots) == 1
    root = roots[0]
    assert root["name"] == "root"
    # Children sorted by start_ts: child1 before child2
    assert [c["name"] for c in root["children"]] == ["child1", "child2"]
    assert [c["name"] for c in root["children"][0]["children"]] == ["grand"]


def test_render_trace_prints_tree(tmp_path: Path, capsys) -> None:
    log = tmp_path / "spans.jsonl"
    _write_record(
        log,
        _make_record(
            trace_id="t_a",
            span_id="root",
            parent_span_id=None,
            name="invoke_agent agent",
            kind="agent",
        ),
    )
    _write_record(
        log,
        _make_record(
            trace_id="t_a",
            span_id="model",
            parent_span_id="root",
            name="chat fn",
            kind="model",
            attributes={"co.model.tokens.input": 50, "co.model.tokens.output": 5},
        ),
    )

    trace_view.render_trace("t_a", log_path=log)
    captured = capsys.readouterr()
    assert "trace t_a" in captured.out
    assert "invoke_agent agent" in captured.out
    assert "chat fn" in captured.out
    assert "in=50" in captured.out


def test_render_trace_missing_trace_emits_yellow_warning(tmp_path: Path, capsys) -> None:
    log = tmp_path / "spans.jsonl"
    log.write_text("")
    trace_view.render_trace("nonexistent", log_path=log)
    captured = capsys.readouterr()
    assert "No records for trace nonexistent" in captured.out
