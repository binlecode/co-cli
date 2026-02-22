"""Functional tests for memory decay lifecycle.

save_memory() triggers _decay_memories() when memory count hits capacity.
Deterministic — no LLM calls.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend
from co_cli.tools.memory import save_memory


def _seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
    decay_protected: bool = False,
) -> Path:
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    slug = content[:40].lower().replace(" ", "-").replace(",", "")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict = {
        "id": memory_id,
        "created": created,
        "tags": tags or [],
        "source": "user-told",
        "auto_category": None,
    }
    if decay_protected:
        fm["decay_protected"] = True
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def _make_ctx(
    max_count: int = 10,
    decay_strategy: str = "summarize",
    decay_percentage: float = 0.2,
) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="test-memory-decay",
        memory_max_count=max_count,
        memory_decay_strategy=decay_strategy,
        memory_decay_percentage=decay_percentage,
    )
    agent, _, _ = get_agent()
    return RunContext(deps=deps, model=agent.model, usage=RunUsage())


def test_decay_summarize_triggers():
    """Seed max_count memories, save one more — summarize decay fires and total stays bounded."""
    max_count = 10
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            for i in range(1, max_count + 1):
                _seed_memory(memory_dir, i, f"Old memory number {i}", days_ago=max_count - i + 10)

            ctx = _make_ctx(max_count=max_count, decay_strategy="summarize")
            asyncio.run(save_memory(ctx, "Brand new important memory", tags=["test"]))

            after = list(memory_dir.glob("*.md"))
            assert len(after) <= max_count, f"Total {len(after)} > max {max_count}"
            has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
            assert has_consolidated, "No consolidated summary file found after summarize decay"
        finally:
            os.chdir(orig_cwd)


def test_decay_cut_triggers():
    """Seed max_count memories, save one more — cut decay fires and no consolidation file created."""
    max_count = 10
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            for i in range(1, max_count + 1):
                _seed_memory(memory_dir, i, f"Old memory number {i}", days_ago=max_count - i + 10)

            ctx = _make_ctx(max_count=max_count, decay_strategy="cut")
            asyncio.run(save_memory(ctx, "Brand new important memory", tags=["test"]))

            after = list(memory_dir.glob("*.md"))
            assert len(after) <= max_count, f"Total {len(after)} > max {max_count}"
            has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
            assert not has_consolidated, "Consolidated file found in cut strategy (should only delete)"
        finally:
            os.chdir(orig_cwd)


def test_decay_protected_survives():
    """Protected memories survive decay even when they are the oldest."""
    max_count = 10
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            for i in range(1, max_count + 1):
                protected = i <= 2
                _seed_memory(
                    memory_dir, i,
                    f"Memory {i} {'PROTECTED' if protected else 'normal'}",
                    days_ago=max_count - i + 10,
                    decay_protected=protected,
                )

            ctx = _make_ctx(max_count=max_count, decay_strategy="summarize", decay_percentage=0.3)
            asyncio.run(save_memory(ctx, "New memory triggering decay", tags=["test"]))

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
        finally:
            os.chdir(orig_cwd)


def test_decay_below_limit_no_trigger():
    """Below capacity — save completes without triggering decay or creating consolidated file."""
    max_count = 10
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            seed_count = max_count - 5
            for i in range(1, seed_count + 1):
                _seed_memory(memory_dir, i, f"Memory number {i}", days_ago=10 - i)

            ctx = _make_ctx(max_count=max_count, decay_strategy="summarize")
            asyncio.run(save_memory(ctx, "New memory within budget", tags=["test"]))

            after = list(memory_dir.glob("*.md"))
            assert len(after) == seed_count + 1, (
                f"Expected {seed_count + 1} memories, got {len(after)}"
            )
            has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
            assert not has_consolidated, "Decay triggered below capacity limit"
        finally:
            os.chdir(orig_cwd)
