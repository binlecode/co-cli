"""Functional tests for native file tools."""

import os
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.files.read import file_find, file_read, file_search
from co_cli.tools.files.write import file_patch, file_write
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx(workspace: Path) -> RunContext:
    """Return a real RunContext scoped to a workspace directory."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        workspace_root=workspace,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# --- glob ---


@pytest.mark.asyncio
async def test_glob_basic(tmp_path):
    """Lists files and subdirectories in a workspace directory."""
    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    (tmp_path / "subdir").mkdir()

    result = await file_find(_make_ctx(tmp_path), path=".")

    assert not result.metadata.get("error")
    assert result.metadata["count"] >= 3
    names = [e["name"] for e in result.metadata["entries"]]
    assert "a.py" in names
    assert "b.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_glob_pattern(tmp_path):
    """Pattern filter restricts results to matching files only."""
    (tmp_path / "main.py").write_text("")
    (tmp_path / "util.py").write_text("")
    (tmp_path / "readme.txt").write_text("")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="*.py")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert "main.py" in names
    assert "util.py" in names
    assert "readme.txt" not in names


@pytest.mark.asyncio
async def test_glob_recursive_glob(tmp_path):
    """Recursive glob pattern finds files across nested directories, sorted by mtime."""
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    # Create files with controlled mtime ordering
    import time

    (tmp_path / "root.py").write_text("r")
    time.sleep(0.05)
    (sub / "deep.py").write_text("d")
    time.sleep(0.05)
    (tmp_path / "src" / "mid.py").write_text("m")
    (tmp_path / "readme.txt").write_text("ignore me")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="**/*.py")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert len(names) == 3
    # All three .py files found, .txt excluded
    assert any("root.py" in n for n in names)
    assert any("deep.py" in n for n in names)
    assert any("mid.py" in n for n in names)
    assert not any(".txt" in n for n in names)
    # Newest first (mid.py was written last)
    assert "mid.py" in names[0]


@pytest.mark.asyncio
async def test_glob_recursive_truncation(tmp_path):
    """Recursive glob truncates at max_entries and reports truncation."""
    for i in range(10):
        (tmp_path / f"file{i:02d}.py").write_text("")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="**/*.py", max_entries=3)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3
    assert result.metadata["truncated"] is True
    assert "truncated" in result.return_value


@pytest.mark.asyncio
async def test_glob_broken_symlink(tmp_path):
    """Broken symlinks do not crash recursive glob; real files are returned normally."""
    (tmp_path / "real.txt").write_text("exists")
    (tmp_path / "broken_link").symlink_to(tmp_path / "nonexistent_target")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="**/*")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert any("real.txt" in n for n in names)


@pytest.mark.asyncio
async def test_glob_shallow_truncation(tmp_path):
    """Shallow listing truncates at max_entries and reports truncation."""
    for i in range(10):
        (tmp_path / f"file{i:02d}.txt").write_text("")

    result = await file_find(_make_ctx(tmp_path), path=".", max_entries=3)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_glob_recursive_includes_directories(tmp_path):
    """Recursive glob returns only files (not directories) when rg is used."""
    nested_dir = tmp_path / "src" / "pkg"
    nested_dir.mkdir(parents=True)
    (nested_dir / "module.py").write_text("x = 1\n")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="**/*")

    assert not result.metadata.get("error")
    entries = result.metadata["entries"]
    assert all(entry["type"] == "file" for entry in entries)
    assert any(
        entry["name"] == "src/pkg/module.py" and entry["type"] == "file" for entry in entries
    )


@pytest.mark.asyncio
async def test_glob_recursive_scoped_pattern_respected(tmp_path):
    """Recursive glob keeps parent path constraints in scoped patterns."""
    (tmp_path / "src").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "src" / "inside.py").write_text("x = 1\n")
    (tmp_path / "other" / "outside.py").write_text("x = 2\n")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="src/**/*.py")

    assert not result.metadata.get("error")
    names = [entry["name"] for entry in result.metadata["entries"]]
    assert names == ["src/inside.py"]


@pytest.mark.asyncio
async def test_glob_not_found(tmp_path):
    """Returns error dict when path does not exist."""
    result = await file_find(_make_ctx(tmp_path), path="nonexistent_dir")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_glob_not_a_dir(tmp_path):
    """Returns error dict when path points to a file, not a directory."""
    (tmp_path / "afile.txt").write_text("content")

    result = await file_find(_make_ctx(tmp_path), path="afile.txt")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_file_find_ripgrep_recursive(tmp_path):
    """file_find with **/*.py uses ripgrep when available, returning files sorted by mtime."""
    import time

    sub = tmp_path / "src"
    sub.mkdir()
    (tmp_path / "old.py").write_text("old")
    time.sleep(0.05)
    (sub / "new.py").write_text("new")

    result = await file_find(_make_ctx(tmp_path), path=".", pattern="**/*.py")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert any("old.py" in n for n in names)
    assert any("new.py" in n for n in names)
    assert all(e["type"] == "file" for e in result.metadata["entries"])
    # Newest first: new.py was written after old.py
    assert "new.py" in names[0]


@pytest.mark.asyncio
async def test_file_find_fallback(tmp_path):
    """file_find falls back to Python glob when ripgrep is unavailable."""
    (tmp_path / "alpha.py").write_text("")
    (tmp_path / "beta.py").write_text("")

    original_path = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        result = await file_find(_make_ctx(tmp_path), path=".", pattern="**/*.py")
    finally:
        if original_path is None:
            del os.environ["PATH"]
        else:
            os.environ["PATH"] = original_path

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert any("alpha.py" in n for n in names)
    assert any("beta.py" in n for n in names)


# --- read_file ---


@pytest.mark.asyncio
async def test_read_file_full(tmp_path):
    """Reads complete file content and returns it in display."""
    (tmp_path / "hello.txt").write_text("hello\nworld\n")

    result = await file_read(_make_ctx(tmp_path), path="hello.txt")

    assert not result.metadata.get("error")
    assert "hello" in result.return_value
    assert "world" in result.return_value
    assert result.metadata["lines"] == 2


@pytest.mark.asyncio
async def test_read_file_line_range(tmp_path):
    """Reads only the requested line range (1-indexed, inclusive)."""
    lines = [f"line{i}\n" for i in range(1, 11)]
    (tmp_path / "numbered.txt").write_text("".join(lines))

    result = await file_read(_make_ctx(tmp_path), path="numbered.txt", start_line=3, end_line=7)

    assert not result.metadata.get("error")
    assert result.metadata["lines"] == 10
    display = result.return_value
    assert "line3" in display
    assert "line7" in display
    # Lines outside the range must not appear
    assert "line1" not in display
    assert "line2" not in display
    assert "line8" not in display


@pytest.mark.asyncio
async def test_read_file_path_is_dir(tmp_path):
    """Returns error dict when path points to a directory."""
    (tmp_path / "adir").mkdir()

    result = await file_read(_make_ctx(tmp_path), path="adir")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_read_file_binary(tmp_path):
    """Returns error for binary files instead of crashing with UnicodeDecodeError."""
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")

    result = await file_read(_make_ctx(tmp_path), path="image.png")

    assert result.metadata.get("error") is True
    assert "Binary file" in result.return_value


@pytest.mark.asyncio
async def test_read_file_path_escape(tmp_path):
    """Returns error when path escapes workspace — tool-level wiring test.

    Complements test_enforce_workspace_boundary_escape_blocked which tests the
    boundary function directly. This test verifies read_file catches the ValueError
    and converts it to tool_error.
    """
    result = await file_read(_make_ctx(tmp_path), path="../../etc/passwd")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_read_file_line_numbers(tmp_path):
    """Full-file read returns cat -n style numbered output."""
    (tmp_path / "five.txt").write_text("aaa\nbbb\nccc\nddd\neee\n")

    result = await file_read(_make_ctx(tmp_path), path="five.txt")

    assert not result.metadata.get("error")
    assert "     1\t" in result.return_value
    assert "     5\t" in result.return_value


@pytest.mark.asyncio
async def test_read_file_line_numbers_ranged(tmp_path):
    """Ranged read line numbers reflect actual file positions."""
    lines = [f"line{i}\n" for i in range(1, 11)]
    (tmp_path / "ten.txt").write_text("".join(lines))

    result = await file_read(_make_ctx(tmp_path), path="ten.txt", start_line=3, end_line=5)

    assert not result.metadata.get("error")
    # First line in output should be numbered 3
    first_line = result.return_value.split("\n")[0]
    assert first_line.startswith("     3\t")
    assert "     5\t" in result.return_value


@pytest.mark.asyncio
async def test_read_file_default_limit(tmp_path):
    """No-range read on a file exceeding the 500-line default returns 500 lines with a continuation hint."""
    content = "".join(f"line{i}\n" for i in range(600))
    (tmp_path / "big.txt").write_text(content)

    result = await file_read(_make_ctx(tmp_path), path="big.txt")

    assert not result.metadata.get("error")
    assert "start_line=501" in result.return_value
    assert "   500\t" in result.return_value
    assert "   501\t" not in result.return_value


@pytest.mark.asyncio
async def test_read_file_hard_ceiling(tmp_path):
    """Explicit ranges exceeding the 2000-line ceiling are capped."""
    content = "".join(f"line{i}\n" for i in range(3000))
    (tmp_path / "large.txt").write_text(content)

    # Sub-case (a): end_line beyond the ceiling
    result_a = await file_read(_make_ctx(tmp_path), path="large.txt", start_line=1, end_line=3000)
    assert not result_a.metadata.get("error")
    assert "  2000\t" in result_a.return_value
    assert "  2001\t" not in result_a.return_value

    # Sub-case (b): start_line only, no end_line
    result_b = await file_read(_make_ctx(tmp_path), path="large.txt", start_line=1)
    assert not result_b.metadata.get("error")
    assert "  2000\t" in result_b.return_value
    assert "  2001\t" not in result_b.return_value


@pytest.mark.asyncio
async def test_read_file_line_truncation(tmp_path):
    """Lines exceeding _READ_MAX_LINE_CHARS are truncated inline with a marker."""
    (tmp_path / "wide.txt").write_text("x" * 3000 + "\n")

    result = await file_read(_make_ctx(tmp_path), path="wide.txt")

    assert not result.metadata.get("error")
    assert "...[truncated]" in result.return_value


@pytest.mark.asyncio
async def test_read_file_size_gate(tmp_path):
    """Full-file read on a file exceeding the 500 KB size limit returns an error with start_line guidance."""
    (tmp_path / "huge.txt").write_bytes(b"x" * (500_000 + 1))

    result = await file_read(_make_ctx(tmp_path), path="huge.txt")

    assert result.metadata.get("error") is True
    assert "start_line" in result.return_value


# --- grep ---


@pytest.mark.asyncio
async def test_grep(tmp_path):
    """Finds regex matches across files; only files containing pattern are returned."""
    (tmp_path / "alpha.txt").write_text("foo bar\nbaz\n")
    (tmp_path / "beta.txt").write_text("hello foo\n")
    (tmp_path / "gamma.txt").write_text("nothing here\n")

    result = await file_search(_make_ctx(tmp_path), pattern="foo")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 2
    # Content mode output: "file:line: text" — verify which files appear
    assert "alpha.txt" in result.return_value
    assert "beta.txt" in result.return_value
    assert "gamma.txt" not in result.return_value


@pytest.mark.asyncio
async def test_grep_invalid_regex(tmp_path):
    """Returns error dict for malformed regex patterns."""
    result = await file_search(_make_ctx(tmp_path), pattern="[unclosed")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_path):
    """Returns zero count and empty matches list when nothing matches."""
    (tmp_path / "sample.txt").write_text("no match here\n")

    result = await file_search(_make_ctx(tmp_path), pattern="zzz_will_never_match")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 0
    assert "(no matches)" in result.return_value


@pytest.mark.asyncio
async def test_grep_recursive_subdirectory(tmp_path):
    """Recursive glob reaches files in deeply nested subdirectories."""
    deep = tmp_path / "src" / "pkg"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("TARGET_MARKER = True\n")
    (tmp_path / "top.txt").write_text("no match here\n")

    result = await file_search(_make_ctx(tmp_path), pattern="TARGET_MARKER")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "src/pkg/deep.py" in result.return_value


@pytest.mark.asyncio
async def test_search_glob_filters_content_search_to_matching_files(tmp_path):
    """file_search(glob=...) searches content only within the matching file set."""
    (tmp_path / "match.py").write_text("TOKEN = 1\n")
    (tmp_path / "skip.txt").write_text("TOKEN = 2\n")

    result = await file_search(_make_ctx(tmp_path), pattern="TOKEN", glob="**/*.py")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "match.py" in result.return_value
    assert "skip.txt" not in result.return_value


# --- write_file ---


@pytest.mark.asyncio
async def test_write_file_creates_dirs(tmp_path):
    """Creates parent directories automatically when writing to nested path."""
    result = await file_write(
        _make_ctx(tmp_path), path="deep/nested/dir/file.txt", content="test content"
    )

    assert not result.metadata.get("error")
    written = tmp_path / "deep" / "nested" / "dir" / "file.txt"
    assert written.exists()
    assert written.read_text() == "test content"


@pytest.mark.asyncio
async def test_write_file_overwrites_existing(tmp_path):
    """Overwrites file content when file already exists."""
    (tmp_path / "existing.txt").write_text("old content")

    result = await file_write(_make_ctx(tmp_path), path="existing.txt", content="new content")

    assert not result.metadata.get("error")
    assert (tmp_path / "existing.txt").read_text() == "new content"


@pytest.mark.asyncio
async def test_write_file_returns_byte_count(tmp_path):
    """Returned byte count matches actual written bytes."""
    content = "hello"
    result = await file_write(_make_ctx(tmp_path), path="bytes.txt", content=content)

    assert not result.metadata.get("error")
    assert result.metadata["bytes"] == len(content.encode("utf-8"))


@pytest.mark.asyncio
async def test_write_file_path_escape(tmp_path):
    """Returns error dict when path escapes the workspace root."""
    result = await file_write(_make_ctx(tmp_path), path="../../etc/passwd", content="evil")

    assert result.metadata.get("error") is True


# --- patch ---


@pytest.mark.asyncio
async def test_patch_exact_match(tmp_path):
    """Exact strategy replaces a unique string in a file."""
    (tmp_path / "config.txt").write_text("host=localhost\nport=8080\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="config.txt")

    result = await file_patch(
        ctx, path="config.txt", old_string="localhost", new_string="example.com"
    )

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    assert result.metadata["strategy"] == "exact"
    assert (tmp_path / "config.txt").read_text() == "host=example.com\nport=8080\n"


@pytest.mark.asyncio
async def test_patch_line_trimmed(tmp_path):
    """line-trimmed strategy matches when old_string has trailing whitespace per line."""
    (tmp_path / "code.py").write_text("def foo():\n    return 1\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="code.py")

    # old_string has trailing spaces on each line — not present in file
    result = await file_patch(
        ctx,
        path="code.py",
        old_string="def foo():  \n    return 1  \n",
        new_string="def bar():\n    return 2\n",
    )

    assert not result.metadata.get("error")
    assert result.metadata["strategy"] == "line-trimmed"
    assert (tmp_path / "code.py").read_text() == "def bar():\n    return 2\n"


@pytest.mark.asyncio
async def test_patch_indent_stripped(tmp_path):
    """Fuzzy matching succeeds when old_string uses wrong indentation (tabs vs spaces)."""
    (tmp_path / "src.py").write_text("    x = 1\n    y = 2\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="src.py")

    # old_string uses tabs instead of spaces — a fuzzy strategy normalises the indentation
    result = await file_patch(
        ctx,
        path="src.py",
        old_string="\tx = 1\n\ty = 2\n",
        new_string="    x = 10\n    y = 20\n",
    )

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    assert (tmp_path / "src.py").read_text() == "    x = 10\n    y = 20\n"


@pytest.mark.asyncio
async def test_patch_escape_expanded(tmp_path):
    """escape-expanded strategy matches when old_string uses literal \\n instead of actual newline."""
    (tmp_path / "data.txt").write_text("line one\nline two\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="data.txt")

    # old_string has literal backslash-n — should match actual newline in file
    result = await file_patch(
        ctx,
        path="data.txt",
        old_string="line one\\nline two\\n",
        new_string="replaced\n",
    )

    assert not result.metadata.get("error")
    assert result.metadata["strategy"] == "escape-expanded"
    assert (tmp_path / "data.txt").read_text() == "replaced\n"


@pytest.mark.asyncio
async def test_patch_ambiguous_returns_error(tmp_path):
    """Returns error when old_string matches multiple times without replace_all=True."""
    (tmp_path / "repeat.txt").write_text("foo\nfoo\nbar\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="repeat.txt")

    result = await file_patch(ctx, path="repeat.txt", old_string="foo", new_string="baz")

    assert result.metadata.get("error") is True
    assert "2" in result.return_value


@pytest.mark.asyncio
async def test_patch_no_match_returns_error(tmp_path):
    """Returns error when no strategy matches — error mentions the file."""
    (tmp_path / "notes.txt").write_text("some content here\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="notes.txt")

    result = await file_patch(ctx, path="notes.txt", old_string="absent_string", new_string="x")

    assert result.metadata.get("error") is True
    assert "notes.txt" in result.return_value


@pytest.mark.asyncio
async def test_patch_not_found_path(tmp_path):
    """Returns error dict when the target file does not exist."""
    result = await file_patch(
        _make_ctx(tmp_path), path="ghost.txt", old_string="x", new_string="y"
    )

    assert result.metadata.get("error") is True


# --- patch (V4A mode) ---


@pytest.mark.asyncio
async def test_patch_v4a_update_single_file(tmp_path):
    """V4A patch updates a single file via Update File directive."""
    (tmp_path / "config.txt").write_text("host=localhost\nport=8080\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="config.txt")

    patch_str = (
        "*** Begin Patch\n"
        "*** Update File: config.txt\n"
        "@@ update port @@\n"
        " host=localhost\n"
        "-port=8080\n"
        "+port=9000\n"
        "*** End Patch"
    )
    result = await file_patch(ctx, mode="patch", patch=patch_str)

    assert not result.metadata.get("error")
    assert "config.txt" in result.metadata["files_modified"]
    assert (tmp_path / "config.txt").read_text() == "host=localhost\nport=9000\n"


@pytest.mark.asyncio
async def test_patch_v4a_update_multi_file(tmp_path):
    """V4A patch applies edits to multiple files in a single call."""
    (tmp_path / "a.txt").write_text("value=old\n")
    (tmp_path / "b.txt").write_text("name=old\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="a.txt")
    await file_read(ctx, path="b.txt")

    patch_str = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "-value=old\n"
        "+value=new\n"
        "*** Update File: b.txt\n"
        "-name=old\n"
        "+name=new\n"
        "*** End Patch"
    )
    result = await file_patch(ctx, mode="patch", patch=patch_str)

    assert not result.metadata.get("error")
    assert "a.txt" in result.metadata["files_modified"]
    assert "b.txt" in result.metadata["files_modified"]
    assert (tmp_path / "a.txt").read_text() == "value=new\n"
    assert (tmp_path / "b.txt").read_text() == "name=new\n"


@pytest.mark.asyncio
async def test_patch_v4a_add_file(tmp_path):
    """V4A patch creates a new file via Add File directive."""
    ctx = _make_ctx(tmp_path)

    patch_str = "*** Begin Patch\n*** Add File: newfile.txt\n+line one\n+line two\n*** End Patch"
    result = await file_patch(ctx, mode="patch", patch=patch_str)

    assert not result.metadata.get("error")
    assert "newfile.txt" in result.metadata["files_created"]
    assert (tmp_path / "newfile.txt").read_text() == "line one\nline two"


@pytest.mark.asyncio
async def test_patch_v4a_delete_file(tmp_path):
    """V4A patch deletes an existing file via Delete File directive."""
    (tmp_path / "victim.txt").write_text("goodbye\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="victim.txt")

    patch_str = "*** Begin Patch\n*** Delete File: victim.txt\n*** End Patch"
    result = await file_patch(ctx, mode="patch", patch=patch_str)

    assert not result.metadata.get("error")
    assert "victim.txt" in result.metadata["files_deleted"]
    assert not (tmp_path / "victim.txt").exists()


@pytest.mark.asyncio
async def test_patch_v4a_requires_read_before_update(tmp_path):
    """V4A UPDATE returns error when file_read was not called first."""
    (tmp_path / "target.txt").write_text("x=1\n")
    ctx = _make_ctx(tmp_path)
    # No file_read call

    patch_str = "*** Begin Patch\n*** Update File: target.txt\n-x=1\n+x=99\n*** End Patch"
    result = await file_patch(ctx, mode="patch", patch=patch_str)

    assert result.metadata.get("error") is True
    assert "file_read" in result.return_value or "Read" in result.return_value


@pytest.mark.asyncio
async def test_patch_v4a_hunk_not_found(tmp_path):
    """V4A UPDATE returns error when old_string is not present in the file."""
    (tmp_path / "target.txt").write_text("hello world\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="target.txt")

    patch_str = (
        "*** Begin Patch\n"
        "*** Update File: target.txt\n"
        "-nonexistent line\n"
        "+replacement\n"
        "*** End Patch"
    )
    result = await file_patch(ctx, mode="patch", patch=patch_str)

    assert result.metadata.get("error") is True
    assert "not found" in result.return_value.lower()


@pytest.mark.asyncio
async def test_patch_v4a_invalid_format(tmp_path):
    """V4A patch with no operations returns a parse error."""
    ctx = _make_ctx(tmp_path)

    result = await file_patch(ctx, mode="patch", patch="*** Begin Patch\n*** End Patch")

    assert result.metadata.get("error") is True


# --- CoToolLifecycle path normalization ---


@pytest.mark.asyncio
async def test_lifecycle_normalizes_relative_path(tmp_path):
    """CoToolLifecycle.before_tool_execute resolves relative paths to absolute for file tools."""
    from pydantic_ai.capabilities import ValidatedToolArgs
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from co_cli.tools.lifecycle import CoToolLifecycle

    lifecycle = CoToolLifecycle()
    ctx = _make_ctx(tmp_path)
    call = ToolCallPart(tool_name="file_read", args={"path": "subdir/file.txt"})
    tool_def = ToolDefinition(name="file_read", description="read", parameters_json_schema={})
    args: ValidatedToolArgs = {"path": "subdir/file.txt"}

    result_args = await lifecycle.before_tool_execute(
        ctx,
        call=call,
        tool_def=tool_def,
        args=args,
    )

    expected = str((tmp_path / "subdir" / "file.txt").resolve())
    assert result_args["path"] == expected


@pytest.mark.asyncio
async def test_lifecycle_skips_non_file_tools(tmp_path):
    """CoToolLifecycle.before_tool_execute leaves non-file-tool args unchanged."""
    from pydantic_ai.capabilities import ValidatedToolArgs
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from co_cli.tools.lifecycle import CoToolLifecycle

    lifecycle = CoToolLifecycle()
    ctx = _make_ctx(tmp_path)
    call = ToolCallPart(tool_name="web_search", args={"query": "hello"})
    tool_def = ToolDefinition(name="web_search", description="search", parameters_json_schema={})
    args: ValidatedToolArgs = {"query": "hello"}

    result_args = await lifecycle.before_tool_execute(
        ctx,
        call=call,
        tool_def=tool_def,
        args=args,
    )

    assert result_args == {"query": "hello"}


# --- staleness guard ---


@pytest.mark.asyncio
async def test_read_file_records_mtime(tmp_path):
    """read_file records the file's mtime in file_read_mtimes on success."""
    target = tmp_path / "tracked.txt"
    target.write_text("content")
    ctx = _make_ctx(tmp_path)

    await file_read(ctx, path="tracked.txt")

    assert str(target) in ctx.deps.file_read_mtimes
    assert ctx.deps.file_read_mtimes[str(target)] == target.stat().st_mtime


@pytest.mark.asyncio
async def test_write_file_staleness_blocked(tmp_path):
    """write_file returns error when file was modified since last read."""
    target = tmp_path / "stale.txt"
    target.write_text("original")
    ctx = _make_ctx(tmp_path)
    # Simulate: agent recorded an old mtime of 0.0 — file on disk has a newer mtime
    ctx.deps.file_read_mtimes[str(target)] = 0.0

    result = await file_write(ctx, path="stale.txt", content="overwrite")

    assert result.metadata.get("error") is True
    assert "changed since last read" in result.return_value


@pytest.mark.asyncio
async def test_patch_staleness_blocked(tmp_path):
    """patch raises ModelRetry when file was modified since last read."""
    target = tmp_path / "stale.txt"
    target.write_text("original content")
    ctx = _make_ctx(tmp_path)
    # Seed mtime as 0 to simulate a stale read (real mtime will differ)
    ctx.deps.file_read_mtimes[str(target)] = 0.0

    with pytest.raises(ModelRetry, match="changed since last read"):
        await file_patch(ctx, path="stale.txt", old_string="original", new_string="new")


@pytest.mark.asyncio
async def test_patch_requires_read_first(tmp_path):
    """patch raises ModelRetry when file has never been read (not in file_read_mtimes)."""
    target = tmp_path / "unread.txt"
    target.write_text("original content")
    ctx = _make_ctx(tmp_path)
    # Do NOT call read_file — file_read_mtimes is empty

    with pytest.raises(ModelRetry, match="file_read"):
        await file_patch(ctx, path="unread.txt", old_string="original", new_string="replaced")


@pytest.mark.asyncio
async def test_patch_return_preview(tmp_path):
    """patch return value includes old/new text preview after successful edit."""
    target = tmp_path / "code.py"
    target.write_text("x = 1\ny = 2\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="code.py")

    result = await file_patch(ctx, path="code.py", old_string="x = 1", new_string="x = 99")

    assert not result.metadata.get("error")
    assert "x = 1" in result.return_value
    assert "x = 99" in result.return_value


@pytest.mark.asyncio
async def test_grep_scoped_to_path(tmp_path):
    """grep with path scopes search to the given subdirectory."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inside.py").write_text("TARGET_TOKEN = 1")
    (tmp_path / "outside.py").write_text("TARGET_TOKEN = 2")
    ctx = _make_ctx(tmp_path)

    result = await file_search(ctx, pattern="TARGET_TOKEN", path="sub")

    assert not result.metadata.get("error")
    assert "inside.py" in result.return_value
    assert "outside.py" not in result.return_value


@pytest.mark.asyncio
async def test_write_file_new_file_skips_staleness(tmp_path):
    """write_file skips staleness check for paths that were never read."""
    ctx = _make_ctx(tmp_path)

    result = await file_write(ctx, path="brand_new.txt", content="hello")

    assert not result.metadata.get("error")


@pytest.mark.asyncio
async def test_write_file_staleness_mtime_updated_after_write(tmp_path):
    """write_file updates file_read_mtimes after a successful write so the second write does not false-positive."""
    target = tmp_path / "data.txt"
    target.write_text("initial")
    ctx = _make_ctx(tmp_path)

    # Read the file so mtime is registered
    await file_read(ctx, path="data.txt")

    result1 = await file_write(ctx, path="data.txt", content="first write")
    assert not result1.metadata.get("error")

    # Second write must also succeed — mtime was updated after the first write
    result2 = await file_write(ctx, path="data.txt", content="second write")
    assert not result2.metadata.get("error")


# --- file size guard + encoding detection ---


@pytest.mark.asyncio
async def test_patch_size_guard(tmp_path):
    """patch returns error when file exceeds the 10 MB edit size limit."""
    target = tmp_path / "huge.txt"
    target.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    ctx = _make_ctx(tmp_path)
    # Register the mtime directly — avoids reading 10 MB of data in this test
    ctx.deps.file_read_mtimes[str(target)] = target.stat().st_mtime

    result = await file_patch(ctx, path="huge.txt", old_string="x", new_string="y")

    assert result.metadata.get("error") is True
    assert "too large" in result.return_value


@pytest.mark.asyncio
async def test_read_file_utf16le(tmp_path):
    """read_file succeeds on UTF-16LE files with BOM."""
    target = tmp_path / "utf16.txt"
    # BOM (FF FE) + UTF-16LE payload
    target.write_bytes(b"\xff\xfe" + "hello utf16".encode("utf-16-le"))

    result = await file_read(_make_ctx(tmp_path), path="utf16.txt")

    assert not result.metadata.get("error")
    assert "hello utf16" in result.return_value


@pytest.mark.asyncio
async def test_patch_utf16(tmp_path):
    """patch succeeds on UTF-16 files with BOM — edit applies and file remains valid UTF-16."""
    target = tmp_path / "utf16edit.txt"
    target.write_bytes(b"\xff\xfe" + "hello world".encode("utf-16-le"))
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="utf16edit.txt")

    result = await file_patch(ctx, path="utf16edit.txt", old_string="hello", new_string="goodbye")

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    # Encoding must be preserved — file is still readable as UTF-16 with the replacement applied
    updated_content = target.read_text(encoding="utf-16")
    assert "goodbye world" in updated_content


# --- partial-read guard ---


@pytest.mark.asyncio
async def test_patch_partial_read_blocked(tmp_path):
    """patch raises ModelRetry when only a line range of the file was read."""
    target = tmp_path / "large.py"
    target.write_text("line one\nline two\nline three\n")
    ctx = _make_ctx(tmp_path)
    # Only read lines 1-2 — file is in file_read_mtimes but also in file_partial_reads
    await file_read(ctx, path="large.py", start_line=1, end_line=2)

    with pytest.raises(ModelRetry, match=r"start_line|end_line"):
        await file_patch(ctx, path="large.py", old_string="line one", new_string="replaced")


@pytest.mark.asyncio
async def test_patch_partial_clears_on_full_read(tmp_path):
    """A full read after a partial read clears the partial flag and allows patch."""
    target = tmp_path / "data.py"
    target.write_text("alpha\nbeta\ngamma\n")
    ctx = _make_ctx(tmp_path)
    await file_read(ctx, path="data.py", start_line=1, end_line=1)
    # Confirm patch is blocked after partial read
    with pytest.raises(ModelRetry, match=r"start_line|end_line"):
        await file_patch(ctx, path="data.py", old_string="alpha", new_string="x")

    # Full read clears the partial flag
    await file_read(ctx, path="data.py")
    result = await file_patch(ctx, path="data.py", old_string="alpha", new_string="x")
    assert not result.metadata.get("error")


# --- grep extended capabilities ---


@pytest.mark.asyncio
async def test_grep_case_insensitive(tmp_path):
    """grep with case_insensitive=True matches regardless of case."""
    (tmp_path / "a.txt").write_text("Hello World\n")
    ctx = _make_ctx(tmp_path)

    result = await file_search(ctx, pattern="hello world", case_insensitive=True)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "a.txt" in result.return_value


@pytest.mark.asyncio
async def test_grep_files_with_matches_mode(tmp_path):
    """output_mode='files_with_matches' returns only file paths, not line content."""
    (tmp_path / "hit.txt").write_text("TARGET here\n")
    (tmp_path / "miss.txt").write_text("nothing relevant\n")
    ctx = _make_ctx(tmp_path)

    result = await file_search(ctx, pattern="TARGET", output_mode="files_with_matches")

    assert not result.metadata.get("error")
    assert "hit.txt" in result.return_value
    assert "miss.txt" not in result.return_value
    # Output is just the file path, no line numbers
    assert ":" not in result.return_value


@pytest.mark.asyncio
async def test_grep_count_mode(tmp_path):
    """output_mode='count' returns file: N for each matched file."""
    (tmp_path / "many.txt").write_text("MARK\nMARK\nno\n")
    (tmp_path / "one.txt").write_text("MARK\n")
    ctx = _make_ctx(tmp_path)

    result = await file_search(ctx, pattern="MARK", output_mode="count")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3  # 2 + 1 total matches
    assert "many.txt: 2" in result.return_value
    assert "one.txt: 1" in result.return_value


@pytest.mark.asyncio
async def test_grep_context_lines(tmp_path):
    """context_lines includes surrounding lines around each match."""
    (tmp_path / "src.py").write_text("before\nTARGET\nafter\n")
    ctx = _make_ctx(tmp_path)

    result = await file_search(ctx, pattern="TARGET", context_lines=1)

    assert not result.metadata.get("error")
    assert "before" in result.return_value
    assert "TARGET" in result.return_value
    assert "after" in result.return_value


@pytest.mark.asyncio
async def test_grep_head_limit_and_offset(tmp_path):
    """head_limit caps output; offset skips the first N entries."""
    content = "\n".join(f"MATCH line {i}" for i in range(10))
    (tmp_path / "big.txt").write_text(content + "\n")
    ctx = _make_ctx(tmp_path)

    result_limited = await file_search(ctx, pattern="MATCH", head_limit=3)
    assert not result_limited.metadata.get("error")
    assert result_limited.metadata["truncated"] is True
    assert result_limited.return_value.count("MATCH") == 3

    result_offset = await file_search(ctx, pattern="MATCH", head_limit=3, offset=3)
    assert not result_offset.metadata.get("error")
    # First result_limited entry must not appear in result_offset
    first_line = result_limited.return_value.splitlines()[0]
    assert first_line not in result_offset.return_value


@pytest.mark.asyncio
async def test_grep_searches_hidden_and_gitignored_files(tmp_path):
    """grep preserves the Python search surface for hidden and gitignored files."""
    hidden_dir = tmp_path / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("TOKEN\n")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()
    (ignored_dir / "ignored.txt").write_text("TOKEN\n")
    ctx = _make_ctx(tmp_path)

    result = await file_search(ctx, pattern="TOKEN")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 2
    assert ".hidden/secret.txt" in result.return_value
    assert "ignored/ignored.txt" in result.return_value


@pytest.mark.asyncio
async def test_grep_without_rg_falls_back_to_python(tmp_path):
    """grep falls back to the Python engine when ripgrep is unavailable."""
    target = tmp_path / "sample.txt"
    target.write_text("TARGET\n")
    ctx = _make_ctx(tmp_path)
    original_path = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        result = await file_search(ctx, pattern="TARGET")
    finally:
        if original_path is None:
            del os.environ["PATH"]
        else:
            os.environ["PATH"] = original_path

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "sample.txt" in result.return_value


# ---------------------------------------------------------------------------
# ctx-path regression tests — verify error returns go through tool_output(ctx=ctx)
# ---------------------------------------------------------------------------


def _make_ctx_sized(workspace: Path, tool_name: str, max_result_size: int = 10) -> RunContext:
    """Return a RunContext with tool_name registered at max_result_size in tool_index."""
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
        workspace_root=workspace,
        tool_results_dir=workspace / "tool-results",
        tool_index={tool_name: info},
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage(), tool_name=tool_name)


@pytest.mark.asyncio
async def test_glob_error_uses_ctx_path(tmp_path):
    """Oversized glob 'path not found' error is persisted through the ctx-aware path."""
    ctx = _make_ctx_sized(tmp_path, "file_find")
    result = await file_find(ctx, path="a" * 50)
    assert PERSISTED_OUTPUT_TAG in result.return_value


@pytest.mark.asyncio
async def test_read_file_error_uses_ctx_path(tmp_path):
    """Oversized read_file 'file not found' error is persisted through the ctx-aware path."""
    ctx = _make_ctx_sized(tmp_path, "file_read")
    result = await file_read(ctx, path="nonexistent_" + "a" * 50)
    assert PERSISTED_OUTPUT_TAG in result.return_value


@pytest.mark.asyncio
async def test_grep_error_uses_ctx_path(tmp_path):
    """Oversized grep 'invalid regex' error is persisted through the ctx-aware path."""
    ctx = _make_ctx_sized(tmp_path, "file_search")
    result = await file_search(ctx, pattern="[unclosed")
    assert PERSISTED_OUTPUT_TAG in result.return_value


@pytest.mark.asyncio
async def test_write_file_error_uses_ctx_path(tmp_path):
    """Oversized write_file staleness error is persisted through the ctx-aware path."""
    target = tmp_path / "data.txt"
    target.write_text("initial")
    ctx = _make_ctx_sized(tmp_path, "file_write")
    # Seed a stale mtime (0.0) — real mtime differs, triggering the staleness guard
    ctx.deps.file_read_mtimes[str(target)] = 0.0
    result = await file_write(ctx, path="data.txt", content="new content")
    assert PERSISTED_OUTPUT_TAG in result.return_value


@pytest.mark.asyncio
async def test_patch_error_uses_ctx_path(tmp_path):
    """Oversized patch 'old_string not found' error is persisted through the ctx-aware path."""
    target = tmp_path / "code.txt"
    target.write_text("some content here")
    ctx = _make_ctx_sized(tmp_path, "file_patch")
    # Populate file_read_mtimes directly to satisfy the read-before-patch guard
    ctx.deps.file_read_mtimes[str(target)] = target.stat().st_mtime
    result = await file_patch(ctx, path="code.txt", old_string="absent_string_xyz", new_string="x")
    assert PERSISTED_OUTPUT_TAG in result.return_value
