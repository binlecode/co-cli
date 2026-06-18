"""Behavioral tests for file_read, file_search.

No LLM — real filesystem operations only.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.config.tuning import PERSISTED_OUTPUT_TAG
from co_cli.deps import CoDeps, CoSessionState
from co_cli.fileio.spill import spill_if_oversized
from co_cli.tools.agent_tool import AGENT_TOOL_ATTR
from co_cli.tools.files.read import file_read, file_search
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import READ_MAX_LINES


def _make_deps(workspace: Path, tool_results_dir: Path | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        workspace_dir=workspace,
        tool_results_dir=tool_results_dir or workspace / "tool_results",
    )


def _ctx(deps: CoDeps, tool: object | None = None) -> RunContext[CoDeps]:
    """Build a RunContext. Pass a decorated tool to apply its real spill threshold.

    Without it, the tool-name lookup misses and tool_output falls back to the 4000-char
    default — fine for small outputs, but file_read's math.inf (keep reads inline) only
    takes effect when tool_name + tool_catalog reflect the registered ToolInfo.
    """
    if tool is None:
        return RunContext(deps=deps, model=None, usage=RunUsage())
    info = getattr(tool, AGENT_TOOL_ATTR)
    deps.tool_catalog[info.name] = info
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=info.name)


# ---------------------------------------------------------------------------
# file_search — file listing (content omitted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_search_lists_directory_entries(tmp_path: Path) -> None:
    """file_search with content omitted and a flat glob lists file and dir entries."""
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "beta.txt").write_text("b")
    (tmp_path / "sub").mkdir()

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="*")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "alpha.txt" in result.return_value
    assert "beta.txt" in result.return_value
    assert "sub" in result.return_value


@pytest.mark.asyncio
async def test_file_search_recursive_glob_returns_nested_files(tmp_path: Path) -> None:
    """file_search with a **/*.py path glob discovers nested .py files."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("x = 1")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="**/*.py")

    assert "mod.py" in result.return_value, f"nested .py not found: {result.return_value}"


@pytest.mark.asyncio
async def test_file_search_directory_prefix_scopes_listing(tmp_path: Path) -> None:
    """A path with a directory prefix scopes the listing to that subtree."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("x = 1")
    (tmp_path / "top.py").write_text("y = 2")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="pkg/**/*.py")

    assert "mod.py" in result.return_value
    assert "top.py" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """file_search returns tool_error when the path prefix escapes the workspace."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="../../etc/*")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_read_returns_line_numbered_content(tmp_path: Path) -> None:
    """file_read returns content with cat-n line numbers."""
    f = tmp_path / "hello.txt"
    f.write_text("line one\nline two\nline three\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="hello.txt")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "1" in result.return_value
    assert "line one" in result.return_value


@pytest.mark.asyncio
async def test_file_read_partial_range_respects_start_end_line(tmp_path: Path) -> None:
    """file_read with start_line/end_line returns only the requested slice."""
    lines = [f"line {i}\n" for i in range(1, 11)]
    f = tmp_path / "big.txt"
    f.write_text("".join(lines))

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="big.txt", start_line=3, end_line=5)

    assert "line 3" in result.return_value
    assert "line 5" in result.return_value
    assert "line 1" not in result.return_value
    assert "line 6" not in result.return_value


@pytest.mark.asyncio
async def test_file_read_not_found_returns_error_with_similar_names(tmp_path: Path) -> None:
    """file_read for a missing file returns tool_error mentioning similar filenames."""
    (tmp_path / "config.py").write_text("x = 1")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="cofig.py")

    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "config.py" in result.return_value, "error must suggest similar filename"


@pytest.mark.asyncio
async def test_file_read_rejects_directory_path(tmp_path: Path) -> None:
    """file_read on a directory path returns tool_error."""
    sub = tmp_path / "subdir"
    sub.mkdir()

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="subdir")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_file_read_pages_at_read_max_lines(tmp_path: Path) -> None:
    """A read of a file longer than READ_MAX_LINES returns exactly READ_MAX_LINES lines
    plus a continuation hint, for both a no-range and a ranged read.

    Failure mode: an unbounded line count lets one read inject the whole file inline.
    """
    total = READ_MAX_LINES + 50
    f = tmp_path / "long.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, total + 1)))

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps, file_read)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        no_range = await file_read(ctx, path="long.txt")

    assert no_range.metadata is not None
    assert no_range.metadata.get("error") is not True
    body = no_range.return_value
    assert f"line {READ_MAX_LINES}" in body
    assert f"line {READ_MAX_LINES + 1}" not in body
    assert f"start_line={READ_MAX_LINES + 1}" in body, "no-range read must page at READ_MAX_LINES"

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        ranged = await file_read(ctx, path="long.txt", start_line=1, end_line=total)

    ranged_body = ranged.return_value
    assert f"line {READ_MAX_LINES}" in ranged_body
    assert f"line {READ_MAX_LINES + 1}" not in ranged_body
    assert f"start_line={READ_MAX_LINES + 1}" in ranged_body, (
        "ranged read must clamp at READ_MAX_LINES"
    )


@pytest.mark.asyncio
async def test_file_read_normal_read_is_inline(tmp_path: Path) -> None:
    """A normal small read returns content inline — never spilled to a persisted-output ref.

    Failure mode: an emission cap on the read tool would spill a normal read, breaking
    the model's first-sight visibility the read tool exists to provide.
    """
    f = tmp_path / "small.txt"
    f.write_text("alpha\nbeta\ngamma\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps, file_read)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="small.txt")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert PERSISTED_OUTPUT_TAG not in result.return_value, "normal read must stay inline"
    assert "alpha" in result.return_value


@pytest.mark.asyncio
async def test_file_read_clips_pathological_long_line(tmp_path: Path) -> None:
    """A single line longer than 2000 chars is clipped (the retained _READ_MAX_LINE_CHARS guard).

    Failure mode: dropping the per-line clip lets one minified-JS line land inline unbounded.
    """
    tail_marker = "PAST_CHAR_2000_MARKER_jx4z"
    long_line = "A" * 2500 + tail_marker
    f = tmp_path / "minified.txt"
    f.write_text(long_line + "\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps, file_read)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="minified.txt")

    body = result.return_value
    assert "...[truncated]" in body, "a >2000-char line must be clipped"
    assert tail_marker not in body, "content past char 2000 must be absent after clip"


# ---------------------------------------------------------------------------
# file_search — content search (content given)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_search_content_returns_matching_lines(tmp_path: Path) -> None:
    """file_search with content set returns matching lines with file:line: text."""
    (tmp_path / "a.txt").write_text("hello world\ngoodbye\n")
    (tmp_path / "b.txt").write_text("no match here\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="hello")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert result.metadata.get("count", 0) >= 1
    assert "hello world" in result.return_value


@pytest.mark.asyncio
async def test_file_search_files_only_returns_paths_only(tmp_path: Path) -> None:
    """file_search with files_only=True returns only file paths, no line content."""
    (tmp_path / "match.txt").write_text("needle is here\n")
    (tmp_path / "nomatch.txt").write_text("nothing relevant\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="needle", files_only=True)

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "match.txt" in result.return_value
    assert "nomatch.txt" not in result.return_value
    # files_only must not include line-number annotations
    assert ":1:" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_content_scoped_by_path_glob(tmp_path: Path) -> None:
    """content search honors the path glob — only matching files are searched."""
    (tmp_path / "keep.py").write_text("needle here\n")
    (tmp_path / "skip.txt").write_text("needle here too\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="**/*.py", content="needle")

    assert "keep.py" in result.return_value
    assert "skip.txt" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_invalid_regex_returns_error(tmp_path: Path) -> None:
    """file_search with an invalid content regex returns tool_error."""
    (tmp_path / "x.txt").write_text("irrelevant\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="[unclosed")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# Multi-root file_search / file_read (file_search_roots)
# ---------------------------------------------------------------------------


def _make_multiroot_deps(workspace: Path, *extra_roots: Path) -> CoDeps:
    """Deps whose read scope spans workspace + extra roots; write anchor stays workspace."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        workspace_dir=workspace,
        file_search_roots=[workspace.resolve(), *(r.resolve() for r in extra_roots)],
    )


@pytest.mark.asyncio
async def test_file_read_absolute_path_under_extra_root(tmp_path: Path) -> None:
    """file_read accepts an absolute path under a configured non-workspace root (TASK-3a/3)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("vault content here\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(note.resolve()))

    assert result.metadata is not None
    assert result.metadata.get("error") is not True, result.return_value
    assert "vault content here" in result.return_value


@pytest.mark.asyncio
async def test_file_search_content_spans_both_roots(tmp_path: Path) -> None:
    """A broad content search returns hits from workspace AND extra root in one call (TASK-3)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    (workspace / "ws_file.txt").write_text("MARKER_TOKEN in workspace\n")
    (vault / "vault_file.txt").write_text("MARKER_TOKEN in vault\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="MARKER_TOKEN")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True, result.return_value
    assert "ws_file.txt" in result.return_value
    assert "vault_file.txt" in result.return_value
    assert result.metadata.get("count", 0) >= 2


@pytest.mark.asyncio
async def test_multiroot_search_hit_roundtrips_to_file_read(tmp_path: Path) -> None:
    """A multi-root file_search hit (absolute) feeds verbatim to file_read (TASK-4, BC-5)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    (vault / "target.md").write_text("ROUNDTRIP_MARKER body\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        search = await file_search(ctx, content="ROUNDTRIP_MARKER", files_only=True)

    assert search.metadata is not None
    assert search.metadata.get("error") is not True, search.return_value
    # The printed hit is an absolute path under the vault root; feed it back verbatim.
    hit = search.return_value.strip().splitlines()[0].strip()
    assert hit == str((vault / "target.md").resolve()), hit

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        read = await file_read(ctx, path=hit)

    assert read.metadata is not None
    assert read.metadata.get("error") is not True, read.return_value
    assert "ROUNDTRIP_MARKER body" in read.return_value


@pytest.mark.asyncio
async def test_file_read_rejects_path_outside_all_roots(tmp_path: Path) -> None:
    """An absolute path under no configured root raises a boundary error (BC-2)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    workspace.mkdir()
    vault.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("should be unreachable\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(secret.resolve()))

    assert result.metadata is not None
    assert result.metadata.get("error") is True, result.return_value


@pytest.mark.asyncio
async def test_file_read_rejects_in_vault_symlink_escaping_all_roots(tmp_path: Path) -> None:
    """An in-root symlink whose resolved target is under no root is rejected (BC-2)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    workspace.mkdir()
    vault.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("escaped\n")
    link = vault / "escape.txt"
    link.symlink_to(outside / "secret.txt")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(link))

    assert result.metadata is not None
    assert result.metadata.get("error") is True, result.return_value


@pytest.mark.asyncio
async def test_file_read_refetches_spilled_tool_result(tmp_path: Path) -> None:
    """A spilled tool result under tool_results_dir is re-readable via file_read.

    spill_if_oversized writes the full result there and the placeholder instructs
    the model to file_read that path; the read must succeed even though
    tool_results_dir is not a file_search root.
    """
    workspace = tmp_path / "ws"
    tool_results_dir = tmp_path / "tool_results"
    workspace.mkdir()

    content = "spilled-line\n" * 1_000
    placeholder = spill_if_oversized(content, tool_results_dir, "shell_exec")
    assert PERSISTED_OUTPUT_TAG in placeholder
    spilled = next(tool_results_dir.glob("*.txt"))

    deps = _make_deps(workspace, tool_results_dir=tool_results_dir)
    ctx = _ctx(deps, tool=file_read)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(spilled), start_line=1, end_line=5)

    assert result.metadata is not None
    assert result.metadata.get("error") is not True, result.return_value
    assert "spilled-line" in result.return_value
