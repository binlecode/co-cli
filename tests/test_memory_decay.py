"""Functional tests for memory decay mechanics. Requires a running LLM provider."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import test_settings
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.memory import save_memory
from co_cli.tools.shell_backend import ShellBackend

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
    decay_protected: bool = False,
) -> Path:
    created = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    slug = content[:40].lower().replace(" ", "-").replace(",", "")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict = {
        "id": memory_id,
        "kind": "memory",
        "created": created,
        "tags": tags or [],
        "provenance": "user-told",
        "auto_category": None,
    }
    if decay_protected:
        fm["decay_protected"] = True
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def _make_ctx(memory_dir: Path, max_count: int = 10) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(
            memory=test_settings().memory.model_copy(update={"max_count": max_count}),
        ),
        memory_dir=memory_dir,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def test_decay_cut_triggers(tmp_path: Path):
    """Seed max_count memories, save one more — cut-only retention fires, no consolidation file created."""
    max_count = 10
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    for i in range(1, max_count + 1):
        _seed_memory(memory_dir, i, f"Old memory number {i}", days_ago=max_count - i + 10)

    ctx = _make_ctx(memory_dir, max_count=max_count)

    async def _run() -> None:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await save_memory(ctx, "Brand new important memory", tags=["test"])

    asyncio.run(_run())

    after = list(memory_dir.glob("*.md"))
    assert len(after) <= max_count, f"Total {len(after)} > max {max_count}"
    has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
    assert not has_consolidated, "Consolidated file found (summarize path should be gone)"


def test_decay_protected_survives(tmp_path: Path):
    """Protected memories survive decay even when they are the oldest."""
    max_count = 10
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    for i in range(1, max_count + 1):
        protected = i <= 2
        _seed_memory(
            memory_dir,
            i,
            f"Memory {i} {'PROTECTED' if protected else 'normal'}",
            days_ago=max_count - i + 10,
            decay_protected=protected,
        )

    ctx = _make_ctx(memory_dir, max_count=max_count)

    async def _run() -> None:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await save_memory(ctx, "New memory triggering decay", tags=["test"])

    asyncio.run(_run())

    after = list(memory_dir.glob("*.md"))
    remaining_ids: set[int] = set()
    for p in after:
        content = p.read_text(encoding="utf-8")
        if "---" in content:
            fm = yaml.safe_load(content.split("---")[1])
            if isinstance(fm, dict) and "id" in fm:
                remaining_ids.add(fm["id"])

    missing = {1, 2} - remaining_ids
    assert not missing, f"Protected memories {missing} were incorrectly decayed"


def test_decay_below_limit_no_trigger(tmp_path: Path):
    """Below capacity — save completes without triggering decay or creating consolidated file."""
    max_count = 10
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    seed_count = max_count - 5
    for i in range(1, seed_count + 1):
        _seed_memory(memory_dir, i, f"Memory number {i}", days_ago=10 - i)

    ctx = _make_ctx(memory_dir, max_count=max_count)

    async def _run() -> None:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await save_memory(ctx, "New memory within budget", tags=["test"])

    asyncio.run(_run())

    after = list(memory_dir.glob("*.md"))
    assert len(after) == seed_count + 1, f"Expected {seed_count + 1} memories, got {len(after)}"
    has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
    assert not has_consolidated, "Decay triggered below capacity limit"
