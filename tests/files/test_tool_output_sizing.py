"""Tests for tool_output() and persist_if_oversized() — per-tool sizing and persistence mechanics."""

import math
import os
from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import (
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    TOOL_RESULT_PREVIEW_SIZE,
    _generate_preview,
    persist_if_oversized,
    sweep_tool_result_orphans,
    tool_output,
    tool_output_raw,
)

_CONFIG = settings
_AGENT = build_agent(config=_CONFIG)


def _make_ctx(tmp_path: Path, tool_name: str = "file_read") -> RunContext[CoDeps]:
    """Build a RunContext with tool_results_dir pointing at tmp_path."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        tool_results_dir=tmp_path / "tool-results",
    )
    return RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name=tool_name,
    )


def _make_ctx_with_index(
    tmp_path: Path,
    tool_name: str,
    max_result_size: int | float | None,
) -> RunContext[CoDeps]:
    """Build a RunContext with a tool_index entry for the given tool."""
    info = ToolInfo(
        name=tool_name,
        description="test tool",
        approval=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
        max_result_size=max_result_size,
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        tool_results_dir=tmp_path / "tool-results",
        tool_index={tool_name: info},
    )
    return RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name=tool_name,
    )


def test_tool_output_uses_per_tool_threshold(tmp_path: Path) -> None:
    """tool_output() persists when content exceeds the per-tool max_result_size."""
    ctx = _make_ctx_with_index(tmp_path, "test_tool", max_result_size=100)
    content = "x" * 150
    result = tool_output(content, ctx=ctx)
    assert PERSISTED_OUTPUT_TAG in result.return_value


def test_tool_output_under_per_tool_threshold(tmp_path: Path) -> None:
    """tool_output() does not persist when content is under the per-tool max_result_size."""
    ctx = _make_ctx_with_index(tmp_path, "test_tool", max_result_size=100)
    content = "x" * 50
    result = tool_output(content, ctx=ctx)
    assert result.return_value == content


def test_tool_output_raw_returns_unchanged() -> None:
    """tool_output_raw() returns content unchanged regardless of size (no ctx)."""
    content = "x" * 100_000
    result = tool_output_raw(content)
    assert result.return_value == content


def test_tool_output_falls_back_to_config_when_tool_not_in_index(tmp_path: Path) -> None:
    """tool_output() falls back to config.tools.result_persist_chars when tool has no ToolInfo."""
    config = make_settings()
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_results_dir=tmp_path / "tool-results",
        tool_index={},
    )
    ctx = RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name="unknown_tool",
    )
    # Content under config threshold → no persistence
    threshold = config.tools.result_persist_chars
    content = "x" * (threshold - 100)
    result = tool_output(content, ctx=ctx)
    assert result.return_value == content


def test_persist_if_oversized_with_explicit_max_size(tmp_path: Path) -> None:
    """persist_if_oversized() respects an explicit max_size argument."""
    content = "y" * 200
    result = persist_if_oversized(content, tmp_path, "test_tool", max_size=100)
    assert PERSISTED_OUTPUT_TAG in result


def test_persist_if_oversized_under_threshold_unchanged(tmp_path: Path) -> None:
    """persist_if_oversized() returns content unchanged when under explicit max_size."""
    content = "z" * 100
    result = persist_if_oversized(content, tmp_path, "test_tool", max_size=200)
    assert result == content


def test_tool_output_persists_oversized_content(tmp_path: Path) -> None:
    """tool_output() with ctx and oversized content persists to disk and returns placeholder."""
    ctx = _make_ctx(tmp_path)
    config_threshold = ctx.deps.config.tools.result_persist_chars
    big_content = "x" * (config_threshold + 1)

    result = tool_output(big_content, ctx=ctx)

    display = result.return_value
    assert display.startswith(PERSISTED_OUTPUT_TAG)
    assert "file_read" in display
    results_dir = tmp_path / "tool-results"
    assert results_dir.exists()
    files = list(results_dir.iterdir())
    assert len(files) == 1
    assert str(files[0]) in display
    assert files[0].read_text(encoding="utf-8") == big_content


def test_persist_if_oversized_idempotent(tmp_path: Path) -> None:
    """Same content produces the same file — content-addressed hash."""
    results_dir = tmp_path / "tool-results"
    content = "z" * 60_000

    result1 = persist_if_oversized(content, results_dir, "file_read", max_size=50_000)
    files_after_first = list(results_dir.iterdir())

    result2 = persist_if_oversized(content, results_dir, "file_read", max_size=50_000)
    files_after_second = list(results_dir.iterdir())

    assert result1 == result2
    assert len(files_after_first) == 1
    assert len(files_after_second) == 1
    assert files_after_first[0] == files_after_second[0]


def test_persist_if_oversized_includes_closing_tag(tmp_path: Path) -> None:
    """persist_if_oversized() output contains the closing XML tag."""
    content = "a" * 60_000
    result = persist_if_oversized(content, tmp_path, "read_file", max_size=50_000)
    assert PERSISTED_OUTPUT_CLOSING_TAG in result


def test_persist_if_oversized_kb_size_format(tmp_path: Path) -> None:
    """persist_if_oversized() shows size in KB for content under 1 MB."""
    content = "a" * 60_000
    result = persist_if_oversized(content, tmp_path, "read_file", max_size=50_000)
    assert "KB" in result
    assert "MB" not in result


def test_persist_if_oversized_mb_size_format(tmp_path: Path) -> None:
    """persist_if_oversized() shows size in MB for content >= 1 MB."""
    content = "a" * (1024 * 1024 + 1)
    result = persist_if_oversized(content, tmp_path, "read_file", max_size=50_000)
    assert "MB" in result


def test_persist_if_oversized_elision_marker_when_content_exceeds_preview(tmp_path: Path) -> None:
    """persist_if_oversized() adds elision marker when content exceeds TOOL_RESULT_PREVIEW_SIZE."""
    content = "b" * (TOOL_RESULT_PREVIEW_SIZE + 1_000)
    result = persist_if_oversized(content, tmp_path, "read_file", max_size=100)
    assert "\n..." in result


def test_persist_if_oversized_no_elision_when_content_fits_preview(tmp_path: Path) -> None:
    """persist_if_oversized() omits elision marker when content fits within TOOL_RESULT_PREVIEW_SIZE."""
    content = "c" * (TOOL_RESULT_PREVIEW_SIZE - 100)
    result = persist_if_oversized(content, tmp_path, "read_file", max_size=100)
    assert PERSISTED_OUTPUT_TAG in result
    assert "\n..." not in result


def test_tool_output_with_inf_max_result_size_never_persists(tmp_path: Path) -> None:
    """tool_output() with max_result_size=math.inf never persists, even for very large content."""
    ctx = _make_ctx_with_index(tmp_path, "read_file", max_result_size=math.inf)
    big_content = "x" * 1_000_000
    result = tool_output(big_content, ctx=ctx)
    assert result.return_value == big_content
    assert not (tmp_path / "tool-results").exists()


def test_generate_preview_content_shorter_than_max() -> None:
    """_generate_preview returns (content, False) when content fits within max_chars."""
    content = "hello world"
    preview, has_more = _generate_preview(content, max_chars=100)
    assert preview == content
    assert has_more is False


def test_generate_preview_truncates_at_newline_when_possible() -> None:
    """_generate_preview truncates at a newline past the halfway point, has_more=True."""
    # 60-char prefix + newline + 40-char suffix = 101 chars total; max=80
    # Newline is at position 60 which is past the halfway point (40)
    content = "a" * 60 + "\n" + "b" * 40
    preview, has_more = _generate_preview(content, max_chars=80)
    assert preview == "a" * 60 + "\n"
    assert has_more is True


def test_generate_preview_hard_cuts_when_no_newline_in_latter_half() -> None:
    """_generate_preview hard-cuts at max_chars when no newline appears in the latter half."""
    content = "x" * 200
    preview, has_more = _generate_preview(content, max_chars=50)
    assert preview == "x" * 50
    assert has_more is True


def _find_dead_pid() -> int:
    """Return a PID guaranteed not to be live. Avoids a 1-in-4B flake on 99999999."""
    for candidate in (99_999_999, 99_999_997, 99_999_991, 99_999_983):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            continue
    raise RuntimeError("could not locate a dead PID for test — exhaust candidates")


def test_sweep_tool_result_orphans_removes_dead_pid_files(tmp_path: Path) -> None:
    """Sweep unlinks `.tmp.<dead_pid>.<uuid>` sidecars and returns the count removed."""
    dead_pid = _find_dead_pid()
    orphan = tmp_path / f"deadbeefdeadbeef.txt.tmp.{dead_pid}.abcd1234"
    orphan.write_text("stale", encoding="utf-8")

    removed = sweep_tool_result_orphans(tmp_path)

    assert removed == 1
    assert not orphan.exists()


def test_sweep_tool_result_orphans_preserves_live_pid_files(tmp_path: Path) -> None:
    """Sweep preserves sidecars whose PID is currently a live process."""
    live = tmp_path / f"cafecafecafecafe.txt.tmp.{os.getpid()}.deadbeef"
    live.write_text("in-flight", encoding="utf-8")

    removed = sweep_tool_result_orphans(tmp_path)

    assert removed == 0
    assert live.exists()


def test_sweep_tool_result_orphans_preserves_final_files(tmp_path: Path) -> None:
    """Sweep leaves final `<hash>.txt` files (no `.tmp.*`) untouched."""
    final = tmp_path / "abcdef1234567890.txt"
    final.write_text("committed content", encoding="utf-8")

    removed = sweep_tool_result_orphans(tmp_path)

    assert removed == 0
    assert final.exists()


def test_sweep_tool_result_orphans_ignores_malformed_names(tmp_path: Path) -> None:
    """Sweep is fail-safe — unrecognized filename shapes are preserved, not deleted."""
    malformed = tmp_path / "abcd.txt.tmp.notapid.abcd1234"
    malformed.write_text("unknown", encoding="utf-8")
    truncated = tmp_path / "abcd.txt.tmp."
    truncated.write_text("partial", encoding="utf-8")

    removed = sweep_tool_result_orphans(tmp_path)

    assert removed == 0
    assert malformed.exists()
    assert truncated.exists()


def test_sweep_tool_result_orphans_missing_dir(tmp_path: Path) -> None:
    """Sweep on a nonexistent directory returns 0 without raising."""
    missing = tmp_path / "does-not-exist"
    assert sweep_tool_result_orphans(missing) == 0
