"""Functional tests for memory lifecycle — consolidation, dedup, retention, on_failure.

Covers apply_plan_atomically action invariants, dedup fast-path, overflow cut,
and timeout-driven on_failure behavior (timeout=0 via CoDeps, no mocks).
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli._memory_consolidator import ConsolidationPlan, MemoryAction, build_alias_map
from co_cli._memory_lifecycle import apply_plan_atomically, persist_memory
from co_cli._shell_backend import ShellBackend
from co_cli._signal_analyzer import SignalResult
from co_cli.main import _handle_signal
from co_cli.tools.memory import MemoryEntry, _load_memories

# Cache agent at module level — get_agent() is expensive; model reference is stable.
_AGENT, _, _, _ = get_agent()

_CONFIG = CoConfig.from_settings(settings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
    decay_protected: bool = False,
) -> MemoryEntry:
    """Write a test memory file and return a MemoryEntry."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    slug = content[:40].lower().replace(" ", "-").replace(",", "")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict[str, Any] = {
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
    return MemoryEntry(
        id=memory_id,
        path=path,
        content=content,
        tags=tags or [],
        created=created,
        decay_protected=decay_protected,
    )


def _make_deps(
    memory_dir: Path | None = None,
    max_count: int = 200,
    timeout: int = 20,
) -> CoDeps:
    cfg = replace(
        _CONFIG,
        session_id="test-lifecycle",
        memory_max_count=max_count,
        memory_consolidation_timeout_seconds=timeout,
    )
    if memory_dir is not None:
        cfg = replace(cfg, memory_dir=memory_dir)
    return CoDeps(services=CoServices(shell=ShellBackend()), config=cfg)


# ---------------------------------------------------------------------------
# 1. Contradiction update — UPDATE action applied, file content updated
# ---------------------------------------------------------------------------


def test_update_action_sets_updated_timestamp(tmp_path: Path):
    """apply_plan_atomically UPDATE refreshes the entry's updated timestamp."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    entry = _seed_memory(memory_dir, 1, "User prefers dark mode", tags=["preference"])

    plan = ConsolidationPlan(actions=[MemoryAction(action="UPDATE", target_alias="M1")])
    alias_map = {"M1": entry}
    deps = _make_deps(memory_dir=memory_dir)

    apply_plan_atomically(plan, alias_map, deps)

    reloaded = _load_memories(memory_dir)
    m1 = [e for e in reloaded if e.id == 1][0]
    assert m1.updated is not None, "UPDATE action must set the updated timestamp"


# ---------------------------------------------------------------------------
# 2. Contradiction delete — non-protected removed, protected survives
# ---------------------------------------------------------------------------


def test_delete_action_removes_non_protected_keeps_protected(tmp_path: Path):
    """DELETE removes non-protected entry; silently skips protected entry."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    non_protected = _seed_memory(memory_dir, 1, "Obsolete preference", tags=["preference"])
    protected = _seed_memory(memory_dir, 2, "Core architecture decision",
                             tags=["decision"], decay_protected=True)

    plan = ConsolidationPlan(actions=[
        MemoryAction(action="DELETE", target_alias="M1"),
        MemoryAction(action="DELETE", target_alias="M2"),
    ])
    alias_map = {"M1": non_protected, "M2": protected}
    deps = _make_deps(memory_dir=memory_dir)

    apply_plan_atomically(plan, alias_map, deps)

    assert not non_protected.path.exists(), "Non-protected memory should be deleted"
    assert protected.path.exists(), "Protected memory must survive DELETE action"


# ---------------------------------------------------------------------------
# 3. Dedup/no-op — rapidfuzz similarity >= threshold, updated refreshed
# ---------------------------------------------------------------------------


def test_dedup_refreshes_timestamp_no_new_file(tmp_path: Path):
    """Candidate matching existing memory via rapidfuzz updates timestamp, creates no new file."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])

    before_count = len(list(memory_dir.glob("*.md")))
    deps = _make_deps(memory_dir=memory_dir)

    # Near-duplicate content (high similarity)
    asyncio.run(
        persist_memory(deps, "User prefers pytest for testing purposes", ["preference"], None)
    )

    after_count = len(list(memory_dir.glob("*.md")))
    assert after_count == before_count, "No new file should be created for near-duplicate"

    reloaded = _load_memories(memory_dir)
    m1 = [e for e in reloaded if e.id == 1][0]
    assert m1.updated is not None, "Dedup must refresh the updated timestamp"


# ---------------------------------------------------------------------------
# 4. Overflow cut — oldest unprotected entries cut until total <= cap
# ---------------------------------------------------------------------------


def test_overflow_cut_oldest_unprotected(tmp_path: Path):
    """After persist_memory, total > max_count triggers cut of oldest unprotected."""
    memory_dir = tmp_path / ".co-cli" / "memory"

    max_count = 5
    for i in range(1, max_count + 1):
        _seed_memory(memory_dir, i, f"Old memory number {i}", days_ago=max_count - i + 10,
                     decay_protected=(i == 1))

    deps = _make_deps(memory_dir=memory_dir, max_count=max_count)
    asyncio.run(
        persist_memory(deps, "Brand new important memory", ["test"], None)
    )

    after = list(memory_dir.glob("*.md"))
    assert len(after) <= max_count, f"Total {len(after)} should be <= {max_count}"

    # Protected memory (id=1) must survive
    remaining_ids: set[int] = set()
    for p in after:
        raw = p.read_text(encoding="utf-8")
        if "---" in raw:
            fm = yaml.safe_load(raw.split("---")[1])
            if isinstance(fm, dict) and "id" in fm:
                remaining_ids.add(fm["id"])
    assert 1 in remaining_ids, "Protected memory (id=1) must survive overflow cut"


# ---------------------------------------------------------------------------
# 4b. Retention cap isolates memories — article is never evicted
# ---------------------------------------------------------------------------


def test_retention_cap_excludes_articles(tmp_path: Path):
    """Retention cap counts only memories; articles must never be deleted."""
    memory_dir = tmp_path / ".co-cli" / "memory"

    # Seed 3 memories (oldest to newest) and 1 article
    for i in range(1, 4):
        _seed_memory(memory_dir, i, f"Old memory {i}", days_ago=10 - i)
    # Seed article as a regular md file with kind:article frontmatter
    article_path = memory_dir / "100-my-article.md"
    import yaml as _yaml
    article_fm = {
        "id": 100, "kind": "article", "created": "2026-01-01T00:00:00+00:00",
        "tags": [], "decay_protected": True, "origin_url": "https://example.com",
    }
    article_path.write_text(
        f"---\n{_yaml.dump(article_fm, default_flow_style=False)}---\n\nArticle body.\n",
        encoding="utf-8",
    )

    # max_count=3: saving one more memory should evict oldest memory, not the article
    deps = _make_deps(memory_dir=memory_dir, max_count=3)
    asyncio.run(persist_memory(deps, "Brand new memory triggers retention", ["test"], None))

    remaining = list(memory_dir.glob("*.md"))
    assert article_path in remaining, "Article must never be evicted by memory retention cap"
    assert len(remaining) <= 4, f"Total files {len(remaining)} should be <= 4"


# ---------------------------------------------------------------------------
# 5. Explicit save fallback — timeout=0, on_failure="add" → file written
# ---------------------------------------------------------------------------


def test_explicit_save_fallback_writes_on_timeout(tmp_path: Path):
    """With timeout=0, consolidation times out but on_failure='add' writes a file."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    deps = _make_deps(memory_dir=memory_dir, timeout=0)

    before_count = len(list(memory_dir.glob("*.md")))

    asyncio.run(
        persist_memory(
            deps, "Unique xylophone-fallback-test memory",
            ["preference"], None,
            on_failure="add", model=_AGENT.model,
        )
    )

    after_count = len(list(memory_dir.glob("*.md")))
    assert after_count == before_count + 1, (
        "on_failure='add' must write a new file even when consolidation times out"
    )


# ---------------------------------------------------------------------------
# 6. Auto-signal save failure — timeout=0, on_failure="skip" → no file
# ---------------------------------------------------------------------------


def test_auto_signal_skip_no_file_on_timeout(tmp_path: Path):
    """With timeout=0, consolidation times out and on_failure='skip' writes nothing."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    deps = _make_deps(memory_dir=memory_dir, timeout=0)

    before_count = len(list(memory_dir.glob("*.md")))

    result = asyncio.run(
        persist_memory(
            deps, "Signal candidate xylophone-skip-test memory",
            None, None,
            on_failure="skip", model=_AGENT.model,
        )
    )

    after_count = len(list(memory_dir.glob("*.md")))
    assert after_count == before_count, (
        "on_failure='skip' must NOT write a file when consolidation times out"
    )
    assert result["action"] == "skipped"


# ---------------------------------------------------------------------------
# Admission policy tests
# ---------------------------------------------------------------------------


class _NoOpFrontend:
    def on_status(self, msg: str) -> None:
        pass

    def prompt_approval(self, msg: str) -> str:
        return "n"


def test_admission_policy(tmp_path: Path) -> None:
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(memory_auto_save_tags=["preference"], memory_dir=mem_dir),
    )
    frontend = _NoOpFrontend()

    # correction tag — should be suppressed (not in allowlist)
    asyncio.run(_handle_signal(
        SignalResult(found=True, candidate="user corrected X", tag="correction", confidence="high"),
        deps, frontend, None,
    ))
    assert len(list(mem_dir.glob("*.md"))) == 0

    # preference tag — should save (in allowlist)
    asyncio.run(_handle_signal(
        SignalResult(found=True, candidate="user prefers Y", tag="preference", confidence="high"),
        deps, frontend, None,
    ))
    assert len(list(mem_dir.glob("*.md"))) == 1
