"""Functional tests for memory lifecycle retention.

save_memory() triggers cut-only retention when memory count hits capacity.
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
from co_cli.config import Settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli._shell_backend import ShellBackend
from co_cli.tools.memory import _classify_certainty, _detect_category, _detect_provenance, save_memory

# Cache agent at module level — get_agent() is expensive; model reference is stable.
_AGENT, _, _, _ = get_agent()


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
        "provenance": "user-told",
        "auto_category": None,
    }
    if decay_protected:
        fm["decay_protected"] = True
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def _make_ctx(max_count: int = 10) -> RunContext:
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            session_id="test-memory-decay",
            memory_max_count=max_count,
        ),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def test_decay_cut_triggers():
    """Seed max_count memories, save one more — cut-only retention fires, no consolidation file created."""
    max_count = 10
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/memory"
            memory_dir.mkdir(parents=True)

            for i in range(1, max_count + 1):
                _seed_memory(memory_dir, i, f"Old memory number {i}", days_ago=max_count - i + 10)

            ctx = _make_ctx(max_count=max_count)
            asyncio.run(save_memory(ctx, "Brand new important memory", tags=["test"]))

            after = list(memory_dir.glob("*.md"))
            assert len(after) <= max_count, f"Total {len(after)} > max {max_count}"
            has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
            assert not has_consolidated, "Consolidated file found (summarize path should be gone)"
        finally:
            os.chdir(orig_cwd)


def test_settings_ignores_removed_decay_fields():
    """Settings silently drops removed decay fields from settings.json on load."""
    s = Settings.model_validate({"memory_decay_strategy": "summarize", "memory_max_count": 100})
    assert s.memory_max_count == 100
    assert not hasattr(s, "memory_decay_strategy")


def test_decay_protected_survives():
    """Protected memories survive decay even when they are the oldest."""
    max_count = 10
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/memory"
            memory_dir.mkdir(parents=True)

            for i in range(1, max_count + 1):
                protected = i <= 2
                _seed_memory(
                    memory_dir, i,
                    f"Memory {i} {'PROTECTED' if protected else 'normal'}",
                    days_ago=max_count - i + 10,
                    decay_protected=protected,
                )

            ctx = _make_ctx(max_count=max_count)
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
            memory_dir = Path(tmpdir) / ".co-cli/memory"
            memory_dir.mkdir(parents=True)

            seed_count = max_count - 5
            for i in range(1, seed_count + 1):
                _seed_memory(memory_dir, i, f"Memory number {i}", days_ago=10 - i)

            ctx = _make_ctx(max_count=max_count)
            asyncio.run(save_memory(ctx, "New memory within budget", tags=["test"]))

            after = list(memory_dir.glob("*.md"))
            assert len(after) == seed_count + 1, (
                f"Expected {seed_count + 1} memories, got {len(after)}"
            )
            has_consolidated = any("_consolidated" in p.read_text(encoding="utf-8") for p in after)
            assert not has_consolidated, "Decay triggered below capacity limit"
        finally:
            os.chdir(orig_cwd)


def test_classify_certainty_hedging_returns_low():
    """_classify_certainty returns 'low' for hedging language."""
    assert _classify_certainty("I think I prefer dark mode") == "low"
    assert _classify_certainty("Maybe I should use pytest") == "low"
    assert _classify_certainty("probably the best approach") == "low"


def test_classify_certainty_certain_returns_high():
    """_classify_certainty returns 'high' for assertive language."""
    assert _classify_certainty("I always use dark mode") == "high"
    assert _classify_certainty("I prefer pytest definitely") == "high"
    assert _classify_certainty("I never use tabs") == "high"


def test_classify_certainty_neutral_returns_medium():
    """_classify_certainty returns 'medium' for neutral language."""
    assert _classify_certainty("User works on a Python project") == "medium"
    assert _classify_certainty("The team uses GitHub for version control") == "medium"


def test_save_memory_writes_certainty_to_frontmatter():
    """save_memory writes certainty field to frontmatter on new save."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/memory"
            memory_dir.mkdir(parents=True)

            ctx = _make_ctx(max_count=50)

            # Hedging content → low
            asyncio.run(save_memory(ctx, "I think I prefer dark mode", tags=["preference"]))
            files = list(memory_dir.glob("*.md"))
            assert len(files) == 1
            fm = yaml.safe_load(files[0].read_text().split("---")[1])
            assert fm.get("certainty") == "low"

            # Remove and test certain content
            files[0].unlink()

            asyncio.run(save_memory(ctx, "I always use dark mode", tags=["preference"]))
            files = list(memory_dir.glob("*.md"))
            assert len(files) == 1
            fm = yaml.safe_load(files[0].read_text().split("---")[1])
            assert fm.get("certainty") == "high"
        finally:
            os.chdir(orig_cwd)


def test_update_existing_memory_reclassifies_certainty():
    """_update_existing_memory updates certainty field on consolidation path."""
    import asyncio
    from datetime import datetime, timezone
    from dataclasses import dataclass
    from co_cli.tools.memory import _update_existing_memory, MemoryEntry

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/memory"
            memory_dir.mkdir(parents=True)

            # Write an existing memory file with certainty: low
            file_path = memory_dir / "001-hedging.md"
            fm: dict = {
                "id": 1,
                "kind": "memory",
                "created": "2026-01-01T00:00:00+00:00",
                "tags": ["preference"],
                "provenance": "user-told",
                "auto_category": "preference",
                "certainty": "low",
            }
            file_path.write_text(
                f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\nI think I prefer dark mode\n",
                encoding="utf-8",
            )

            entry = MemoryEntry(
                id=1,
                path=file_path,
                content="I think I prefer dark mode",
                tags=["preference"],
                created="2026-01-01T00:00:00+00:00",
            )

            # Consolidate with assertive content → certainty should be reclassified to high
            _update_existing_memory(entry, "I always prefer dark mode", ["preference"])

            updated_fm = yaml.safe_load(file_path.read_text().split("---")[1])
            assert updated_fm.get("certainty") == "high"
        finally:
            os.chdir(orig_cwd)


def test_save_memory_normalizes_tag_case():
    """Tags saved with mixed case are lowercased — detection functions see consistent values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/memory"
            memory_dir.mkdir(parents=True)

            ctx = _make_ctx(max_count=50)
            asyncio.run(
                save_memory(ctx, "User prefers dark mode", tags=["Preference", "MyTag"])
            )

            files = list(memory_dir.glob("*.md"))
            assert len(files) == 1

            raw = files[0].read_text(encoding="utf-8")
            fm = yaml.safe_load(raw.split("---")[1])
            saved_tags = fm.get("tags", [])

            # Tags must be lowercased at write time
            assert "preference" in saved_tags
            assert "mytag" in saved_tags
            assert "Preference" not in saved_tags
            assert "MyTag" not in saved_tags

            # Detection functions must see normalized tags correctly
            assert _detect_category(saved_tags) == "preference"
            assert _detect_provenance(saved_tags) == "detected"
        finally:
            os.chdir(orig_cwd)
