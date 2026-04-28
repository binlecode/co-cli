"""Tests for new file tool behaviors: fuzzy suggestions, pagination hints, diff, auto-lint."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps
from co_cli.tools.files.read import file_read
from co_cli.tools.files.write import file_patch
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_ctx(workspace: Path) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        workspace_root=workspace,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# ---------------------------------------------------------------------------
# TASK-1: read_file — fuzzy suggestions on missing files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_missing_with_similar_name_suggests(tmp_path):
    """Missing file with a near-typo name returns similar filenames in the error message."""
    (tmp_path / "config.yaml").write_text("key: value")
    (tmp_path / "config.yml").write_text("other: data")

    result = await file_read(_make_ctx(tmp_path), path="conifg.yaml")

    assert result.metadata.get("error") is True
    assert "Similar files" in result.return_value
    assert "config.yaml" in result.return_value


@pytest.mark.asyncio
async def test_read_file_missing_no_similar_names(tmp_path):
    """Missing file with no close matches returns a clean error without a suggestions line."""
    (tmp_path / "main.py").write_text("x = 1")

    result = await file_read(_make_ctx(tmp_path), path="zzz_totally_different.txt")

    assert result.metadata.get("error") is True
    assert "File not found" in result.return_value
    assert "Similar files" not in result.return_value


@pytest.mark.asyncio
async def test_read_file_missing_parent_dir_no_crash(tmp_path):
    """Missing file whose parent directory does not exist returns error without crashing."""
    result = await file_read(_make_ctx(tmp_path), path="nonexistent_dir/file.txt")

    assert result.metadata.get("error") is True
    assert "Similar files" not in result.return_value


# ---------------------------------------------------------------------------
# TASK-1: read_file — pagination hints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_partial_read_emits_continuation_hint(tmp_path):
    """Partial read that does not reach EOF includes a start_line continuation hint."""
    lines = [f"line{i}\n" for i in range(1, 21)]
    (tmp_path / "long.txt").write_text("".join(lines))

    result = await file_read(_make_ctx(tmp_path), path="long.txt", start_line=1, end_line=10)

    assert not result.metadata.get("error")
    assert "start_line=11" in result.return_value
    assert "more lines" in result.return_value


@pytest.mark.asyncio
async def test_read_file_partial_read_at_eof_no_hint(tmp_path):
    """Partial read that reaches the last line does not emit a continuation hint."""
    lines = [f"line{i}\n" for i in range(1, 6)]
    (tmp_path / "short.txt").write_text("".join(lines))

    result = await file_read(_make_ctx(tmp_path), path="short.txt", start_line=3, end_line=5)

    assert not result.metadata.get("error")
    assert "more lines" not in result.return_value
    assert "start_line=" not in result.return_value


@pytest.mark.asyncio
async def test_read_file_full_read_no_hint(tmp_path):
    """Full file read (no start_line/end_line) never emits a continuation hint."""
    (tmp_path / "full.txt").write_text("a\nb\nc\nd\ne\n")

    result = await file_read(_make_ctx(tmp_path), path="full.txt")

    assert not result.metadata.get("error")
    assert "more lines" not in result.return_value
    assert "start_line=" not in result.return_value


# ---------------------------------------------------------------------------
# TASK-2: patch — context-expansion error message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_ambiguous_exact_match_guidance(tmp_path):
    """Ambiguous exact match error contains 'provide more surrounding context'."""
    (tmp_path / "dup.txt").write_text("foo\nfoo\nbar\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="dup.txt")

    result = await file_patch(ctx, path="dup.txt", old_string="foo", new_string="baz")

    assert result.metadata.get("error") is True
    assert "provide more surrounding context" in result.return_value


@pytest.mark.asyncio
async def test_patch_ambiguous_fuzzy_match_guidance(tmp_path):
    """Ambiguous fuzzy match error contains 'provide more surrounding context'."""
    (tmp_path / "dup.py").write_text("def x():  \n    pass\ndef x():  \n    pass\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="dup.py")

    result = await file_patch(
        ctx, path="dup.py", old_string="def x():\n    pass\n", new_string="def y():\n    pass\n"
    )

    assert result.metadata.get("error") is True
    assert "provide more surrounding context" in result.return_value


# ---------------------------------------------------------------------------
# TASK-2: patch — opt-in unified diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_show_diff_exact_match_contains_diff_block(tmp_path):
    """patch with show_diff=True returns a [Diff] block with +/- lines on exact match."""
    (tmp_path / "code.py").write_text("x = 1\ny = 2\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="code.py")

    result = await file_patch(
        ctx, path="code.py", old_string="x = 1", new_string="x = 99", show_diff=True
    )

    assert not result.metadata.get("error")
    lines = result.return_value.splitlines()
    assert any(ln == "[Diff]" for ln in lines), "must have a standalone [Diff] header line"
    assert any(ln == "-x = 1" for ln in lines), "must have a standalone removal line"
    assert any(ln == "+x = 99" for ln in lines), "must have a standalone addition line"
    assert any(ln.startswith("--- ") for ln in lines), "must have a standalone --- header line"
    assert any(ln.startswith("+++ ") for ln in lines), "must have a standalone +++ header line"


@pytest.mark.asyncio
async def test_patch_show_diff_fuzzy_match_contains_diff_block(tmp_path):
    """patch with show_diff=True returns a [Diff] block on fuzzy match."""
    (tmp_path / "src.py").write_text("def foo():  \n    return 1\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="src.py")

    result = await file_patch(
        ctx,
        path="src.py",
        old_string="def foo():\n    return 1\n",
        new_string="def bar():\n    return 2\n",
        show_diff=True,
    )

    assert not result.metadata.get("error")
    assert "[Diff]" in result.return_value


@pytest.mark.asyncio
async def test_patch_no_show_diff_default_omits_diff_block(tmp_path):
    """patch without show_diff (default False) returns no [Diff] block."""
    (tmp_path / "data.txt").write_text("hello world\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="data.txt")

    result = await file_patch(ctx, path="data.txt", old_string="hello", new_string="goodbye")

    assert not result.metadata.get("error")
    assert "[Diff]" not in result.return_value


@pytest.mark.asyncio
async def test_patch_show_diff_same_string_produces_no_diff(tmp_path):
    """patch with show_diff=True where old_string == new_string shows (no diff)."""
    (tmp_path / "same.txt").write_text("unchanged line\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="same.txt")

    result = await file_patch(
        ctx,
        path="same.txt",
        old_string="unchanged line",
        new_string="unchanged line",
        show_diff=True,
    )

    assert not result.metadata.get("error")
    assert "(no diff)" in result.return_value


# ---------------------------------------------------------------------------
# TASK-3: patch — auto-linting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_lint_warns_on_invalid_python(tmp_path):
    """Patching a .py file that introduces a lint violation appends [Auto-Lint Warnings] in display."""
    (tmp_path / "bad.py").write_text("x = 1\nimport os\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="bad.py")

    # Introduce an unused import violation (E401 / F401 — ruff will warn)
    result = await file_patch(
        ctx, path="bad.py", old_string="x = 1", new_string="x = 1\nimport sys"
    )

    assert not result.metadata.get("error")
    assert "[Auto-Lint Warnings]" in result.return_value


@pytest.mark.asyncio
async def test_patch_lint_clean_python_no_warnings(tmp_path):
    """Patching a .py file that remains lint-clean produces no [Auto-Lint Warnings] block."""
    (tmp_path / "clean.py").write_text("x = 1\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="clean.py")

    result = await file_patch(ctx, path="clean.py", old_string="x = 1", new_string="x = 2")

    assert not result.metadata.get("error")
    assert "[Auto-Lint Warnings]" not in result.return_value


@pytest.mark.asyncio
async def test_patch_lint_skipped_for_non_python(tmp_path):
    """Patching a non-.py file produces no lint output regardless of content."""
    (tmp_path / "data.txt").write_text("import os\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="data.txt")

    result = await file_patch(
        ctx, path="data.txt", old_string="import os", new_string="import sys"
    )

    assert not result.metadata.get("error")
    assert "[Auto-Lint Warnings]" not in result.return_value
