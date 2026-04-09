"""Layer 1 tests for the tool result persistence engine."""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import test_settings

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_output import tool_output, tool_output_raw
from co_cli.tools.tool_result_storage import (
    PERSISTED_OUTPUT_TAG,
    TOOL_RESULT_MAX_SIZE,
    persist_if_oversized,
)

_CONFIG = settings
_AGENT = build_agent(config=_CONFIG)


def _make_ctx(tmp_path: Path, tool_name: str = "read_file") -> RunContext[CoDeps]:
    """Build a real RunContext with tool_results_dir pointing at tmp_path."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        tool_results_dir=tmp_path / "tool-results",
    )
    return RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name=tool_name,
    )


def test_tool_output_persists_oversized_content(tmp_path: Path) -> None:
    """tool_output() with ctx and oversized content persists to disk and returns placeholder."""
    ctx = _make_ctx(tmp_path)
    # Content larger than TOOL_RESULT_MAX_SIZE
    big_content = "x" * (TOOL_RESULT_MAX_SIZE + 1)

    result = tool_output(big_content, ctx=ctx)

    display = result.return_value
    assert display.startswith(PERSISTED_OUTPUT_TAG)
    assert "read_file" in display
    # Verify a file was actually created
    results_dir = tmp_path / "tool-results"
    assert results_dir.exists()
    files = list(results_dir.iterdir())
    assert len(files) == 1
    # Verify the file path appears in the placeholder
    assert str(files[0]) in display
    # Verify persisted file contains the full content
    assert files[0].read_text(encoding="utf-8") == big_content


def test_tool_output_small_content_unchanged(tmp_path: Path) -> None:
    """tool_output() with ctx and small content returns content unchanged."""
    ctx = _make_ctx(tmp_path)
    small_content = "hello world"

    result = tool_output(small_content, ctx=ctx)

    assert result.return_value == small_content
    # No files should be created
    results_dir = tmp_path / "tool-results"
    assert not results_dir.exists()


def test_tool_output_raw_oversized_unchanged() -> None:
    """tool_output_raw() without ctx and oversized content returns content unchanged."""
    big_content = "y" * (TOOL_RESULT_MAX_SIZE + 1)

    result = tool_output_raw(big_content)

    assert result.return_value == big_content


def test_persist_if_oversized_idempotent(tmp_path: Path) -> None:
    """Same content produces the same file — content-addressed hash."""
    results_dir = tmp_path / "tool-results"
    content = "z" * (TOOL_RESULT_MAX_SIZE + 1)

    result1 = persist_if_oversized(content, results_dir, "read_file")
    files_after_first = list(results_dir.iterdir())

    result2 = persist_if_oversized(content, results_dir, "read_file")
    files_after_second = list(results_dir.iterdir())

    # Same placeholder returned
    assert result1 == result2
    # Same single file on disk
    assert len(files_after_first) == 1
    assert len(files_after_second) == 1
    assert files_after_first[0] == files_after_second[0]
