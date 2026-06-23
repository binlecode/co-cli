"""Banner Memory row rendering — pure string-building logic tests."""

from __future__ import annotations

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.bootstrap import banner as banner_mod
from co_cli.bootstrap.banner import build_memory_line, display_welcome_banner
from co_cli.commands.status_report import build_status_counts
from co_cli.deps import CoDeps
from co_cli.display.core import console
from co_cli.tools.shell_backend import ShellBackend


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
    assert backend_label in result
    assert "[yellow](hybrid → fts5)[/yellow]" in result
    assert "10 mem" in result
    assert "3 sess" in result


def test_grep_backend_omits_counts() -> None:
    """grep backend renders label only — no knowledge/session counts."""
    result = build_memory_line(
        backend="grep",
        backend_label="grep (no index)",
        memory_degradation=None,
        memory_count=99,
        session_count=5,
    )
    assert "grep (no index)" in result
    assert " mem" not in result
    assert " sess" not in result


def test_memory_count_over_tripwire_renders_yellow_warning() -> None:
    """Active count past MEMORY_ITEM_COUNT_WARN flags the count yellow (warn-only)."""
    from co_cli.config.memory import MEMORY_ITEM_COUNT_WARN

    result = build_memory_line(
        backend="hybrid",
        backend_label="hybrid",
        memory_degradation=None,
        memory_count=MEMORY_ITEM_COUNT_WARN + 1,
        session_count=3,
    )
    assert f"[yellow]⚠ {MEMORY_ITEM_COUNT_WARN + 1} mem (over count tripwire)[/yellow]" in result
    assert "3 sess" in result


def _render(deps: CoDeps) -> str:
    """Render the banner at a forced-wide width (so a long path never wraps) and
    flatten the rich Panel (drop borders, collapse whitespace) so assertions are
    stable across environments."""
    original_width = console._width
    console._width = 400
    try:
        with console.capture() as cap:
            display_welcome_banner(deps, memory_count=0, session_count=0)
        text = cap.get()
    finally:
        console._width = original_width
    for box_char in "│╭╮╰╯─":
        text = text.replace(box_char, " ")
    return " ".join(text.split())


def test_banner_dir_line_uses_full_path_when_workspace_configured(
    tmp_path: Path, monkeypatch
) -> None:
    """The Dir line shows the full workspace path + branch when workspace_path is set."""
    monkeypatch.setattr(banner_mod, "project_info", lambda: _FixedInfo())
    workspace = tmp_path / "proj"
    workspace.mkdir()
    config = SETTINGS.model_copy(update={"workspace_path": str(workspace)})
    deps = CoDeps(shell=ShellBackend(), config=config, workspace_dir=workspace)

    text = _render(deps)

    counts = build_status_counts(deps)
    assert (
        f"Tools {counts.tools} · {counts.skills} skills · "
        f"{counts.mcp} mcp · {counts.commands} cmds" in text
    )
    # workspace_path set -> full path, plus the fixed branch.
    assert f"Dir {workspace} · test-branch" in text


def test_banner_dir_line_uses_bare_name_when_workspace_unconfigured(
    tmp_path: Path, monkeypatch
) -> None:
    """The Dir line shows the bare directory name when workspace_path is unset."""
    monkeypatch.setattr(banner_mod, "project_info", lambda: _FixedInfo())
    workspace = tmp_path / "bare-name"
    workspace.mkdir()
    config = SETTINGS.model_copy(update={"workspace_path": None})
    deps = CoDeps(shell=ShellBackend(), config=config, workspace_dir=workspace)

    text = _render(deps)

    # workspace_path unset -> bare name only, not the full path.
    assert "Dir bare-name · test-branch" in text
    assert str(workspace) not in text


class _FixedInfo:
    """Deterministic ProjectInfo stand-in so rendered output is environment-stable."""

    version = "9.9.9"
    git_branch = "test-branch"
