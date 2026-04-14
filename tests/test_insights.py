"""Tests for save_insight tool — real deps, real filesystem, no mocks."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.insights import save_insight
from co_cli.tools.shell_backend import ShellBackend

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx(memory_dir: Path) -> RunContext:
    """Return a real RunContext with real CoDeps for insight tool tests."""
    deps_kwargs: dict[str, Any] = {
        "shell": ShellBackend(),
        "config": make_settings(),
        "memory_dir": memory_dir,
    }
    deps = CoDeps(**deps_kwargs)
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_save_insight_writes_md_file(tmp_path: Path) -> None:
    """save_insight writes a .md file to memory_dir."""
    ctx = _make_ctx(memory_dir=tmp_path)
    await save_insight(ctx, content="This is a test insight.")
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    assert md_files[0].suffix == ".md"


@pytest.mark.asyncio
async def test_save_insight_two_identical_calls_produce_two_files(tmp_path: Path) -> None:
    """Two calls with identical content produce two distinct files (UUID suffix)."""
    ctx = _make_ctx(memory_dir=tmp_path)
    await save_insight(ctx, content="Identical content here.")
    await save_insight(ctx, content="Identical content here.")
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 2


@pytest.mark.asyncio
async def test_save_insight_frontmatter_has_required_fields(tmp_path: Path) -> None:
    """Saved file frontmatter contains id, kind, created, and tags fields."""
    ctx = _make_ctx(memory_dir=tmp_path)
    await save_insight(ctx, content="Insight with required frontmatter fields.", tags=["Alpha"])
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    raw = md_files[0].read_text(encoding="utf-8")
    # Extract YAML block between --- delimiters
    parts = raw.split("---")
    fm = yaml.safe_load(parts[1])
    assert "id" in fm
    assert fm["kind"] == "memory"
    assert "created" in fm
    assert fm["tags"] == ["alpha"]


@pytest.mark.asyncio
async def test_save_insight_always_on_writes_frontmatter_flag(tmp_path: Path) -> None:
    """always_on=True writes always_on: true in frontmatter."""
    ctx = _make_ctx(memory_dir=tmp_path)
    await save_insight(ctx, content="Always-on insight.", always_on=True)
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    raw = md_files[0].read_text(encoding="utf-8")
    parts = raw.split("---")
    fm = yaml.safe_load(parts[1])
    assert fm.get("always_on") is True


@pytest.mark.asyncio
async def test_save_insight_type_written_to_frontmatter(tmp_path: Path) -> None:
    """type_='user' is written as type: user in frontmatter."""
    ctx = _make_ctx(memory_dir=tmp_path)
    await save_insight(ctx, content="Typed insight.", type_="user")
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1
    raw = md_files[0].read_text(encoding="utf-8")
    parts = raw.split("---")
    fm = yaml.safe_load(parts[1])
    assert fm.get("type") == "user"
