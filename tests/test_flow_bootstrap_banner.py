"""Banner Memory row rendering — pure string-building logic tests."""

from co_cli.bootstrap.banner import build_memory_line


def test_hybrid_backend_with_degradation_and_counts() -> None:
    """hybrid backend with a degradation renders both degradation suffix and counts."""
    backend_label = "hybrid · openai/text-embedding-3-small 1536d"
    result = build_memory_line(
        backend="hybrid",
        backend_label=backend_label,
        memory_degradation="hybrid → fts5",
        memory_count=10,
        session_count=3,
    )
    assert f"[accent]{backend_label}[/accent]" in result
    assert "[yellow](hybrid → fts5)[/yellow]" in result
    assert "memory: 10  sessions: 3" in result


def test_grep_backend_omits_counts() -> None:
    """grep backend renders label only — no knowledge/session counts."""
    result = build_memory_line(
        backend="grep",
        backend_label="grep (no index)",
        memory_degradation=None,
        memory_count=99,
        session_count=5,
    )
    assert result == "    Memory: [accent]grep (no index)[/accent]"
    assert "memory:" not in result
    assert "sessions:" not in result
