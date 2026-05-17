"""Tests for ``co tail`` against the structured-log spans file."""

import json
from pathlib import Path

from co_cli.observability import tail


def _write_record(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _make_record(
    *,
    name: str,
    kind: str = "co",
    trace_id: str = "t_abc",
    span_id: str = "s_1",
    parent_span_id: str | None = None,
    duration_ms: float = 1.0,
    attributes: dict | None = None,
    status: str = "OK",
) -> dict:
    return {
        "ts": "2026-05-17T00:00:00.000000Z",
        "schema_version": 1,
        "session_id": None,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "start_ts": "2026-05-17T00:00:00.000000Z",
        "duration_ms": duration_ms,
        "status": status,
        "status_msg": None,
        "attributes": attributes or {},
        "events": [],
    }


def test_tail_last_n_records_reads_last_n(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    for i in range(20):
        _write_record(log, _make_record(name=f"span_{i}", span_id=f"s_{i}"))

    records = tail._tail_last_n_records(log, 5)
    assert len(records) == 5
    assert records[0]["name"] == "span_15"
    assert records[-1]["name"] == "span_19"


def test_read_records_from_byte_offset(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    _write_record(log, _make_record(name="first"))
    offset_after_first = log.stat().st_size
    _write_record(log, _make_record(name="second"))
    _write_record(log, _make_record(name="third"))

    records, new_offset = tail._read_records_from(log, offset_after_first)
    assert len(records) == 2
    assert records[0]["name"] == "second"
    assert records[1]["name"] == "third"
    assert new_offset == log.stat().st_size


def test_record_filters(tmp_path: Path) -> None:
    agent_rec = _make_record(name="invoke_agent", kind="agent", trace_id="t_1")
    tool_rec = _make_record(name="tool foo", kind="tool", trace_id="t_2")
    model_rec = _make_record(name="chat m", kind="model", trace_id="t_1")

    assert tail._record_passes_filters(tool_rec, trace_id=None, tools_only=True, models_only=False)
    assert not tail._record_passes_filters(
        agent_rec, trace_id=None, tools_only=True, models_only=False
    )
    assert tail._record_passes_filters(
        model_rec, trace_id=None, tools_only=False, models_only=True
    )
    assert tail._record_passes_filters(
        agent_rec, trace_id="t_1", tools_only=False, models_only=False
    )
    assert not tail._record_passes_filters(
        tool_rec, trace_id="t_1", tools_only=False, models_only=False
    )


def test_rotation_detected_via_inode_change(tmp_path: Path) -> None:
    """When the spans log inode changes (rotation), `run_tail` reopens from byte 0.

    Lower-level check: ``_inode_of`` returns the inode for an existing file and
    None for a missing path. The full follow-loop is integration-level.
    """
    log = tmp_path / "spans.jsonl"
    _write_record(log, _make_record(name="orig"))
    inode_a = tail._inode_of(log)
    assert inode_a is not None

    # Simulate rotation: remove and recreate.
    log.unlink()
    _write_record(log, _make_record(name="post_rotation"))
    inode_b = tail._inode_of(log)
    assert inode_b is not None
    assert inode_b != inode_a, "inode must change after rotation for follow-loop to detect"


def test_format_line_summary_includes_kind_and_name(tmp_path: Path) -> None:
    rec = _make_record(
        name="chat fn",
        kind="model",
        attributes={"co.model.tokens.input": 100, "co.model.tokens.output": 5},
        duration_ms=42.0,
    )
    lines = tail._format_line(rec, detail=False)
    rendered = "".join(seg.plain for seg in lines[0])
    assert "model" in rendered
    assert "chat fn" in rendered
    assert "in=100" in rendered
    assert "out=5" in rendered


def test_format_line_detail_renders_model_input_and_output(tmp_path: Path) -> None:
    input_msgs = json.dumps([{"role": "request", "parts": [{"type": "user", "content": "hello"}]}])
    output_parts = json.dumps(
        [{"type": "thinking", "content": "consider"}, {"type": "text", "content": "hi"}]
    )
    rec = _make_record(
        name="chat fn",
        kind="model",
        attributes={"co.model.input": input_msgs, "co.model.output": output_parts},
    )
    lines = tail._format_line(rec, detail=True)
    all_text = "\n".join(seg.plain for seg in lines)
    assert "hello" in all_text
    assert "consider" in all_text
    assert "hi" in all_text


def test_format_line_error_status_renders_error_marker(tmp_path: Path) -> None:
    rec = _make_record(name="tool boom", kind="tool", status="ERROR")
    lines = tail._format_line(rec, detail=False)
    rendered = "".join(seg.plain for seg in lines[0])
    assert "ERROR" in rendered
