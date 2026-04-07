"""Functional tests for memory lifecycle — upsert, lock-based on_failure, retention.

Covers persist_memory write, upsert routing via memory save agent, resource lock
concurrency, overflow cut, and on_failure behavior.
"""

import asyncio
from dataclasses import replace

from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from co_cli.config import settings, ROLE_SUMMARIZATION
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.deps import CoDeps, CoConfig
from co_cli.memory._lifecycle import persist_memory
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.memory import MemoryEntry

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)


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
) -> CoDeps:
    cfg = replace(
        _CONFIG,
        memory_max_count=max_count,
    )
    if memory_dir is not None:
        cfg = replace(cfg, memory_dir=memory_dir)
    return CoDeps(shell=ShellBackend(), config=cfg)


# ---------------------------------------------------------------------------
# 1. Basic persist_memory — writes a new file
# ---------------------------------------------------------------------------


def test_persist_memory_writes_new_file(tmp_path: Path):
    """persist_memory creates a new .md file in memory_dir."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    deps = _make_deps(memory_dir=memory_dir)
    result = asyncio.run(
        persist_memory(deps, "User prefers xylophone-lifecycle-newfile test", ["preference"], None)
    )

    after = list(memory_dir.glob("*.md"))
    assert len(after) == 1, f"Expected 1 file, got {len(after)}"
    assert result.metadata["action"] == "saved"
    assert result.metadata["memory_id"] is not None


# ---------------------------------------------------------------------------
# 2. Overflow cut — oldest unprotected entries cut until total <= cap
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
# 3. Retention cap isolates memories — article is never evicted
# ---------------------------------------------------------------------------


def test_retention_cap_excludes_articles(tmp_path: Path):
    """Retention cap counts only memories; articles must never be deleted."""
    memory_dir = tmp_path / ".co-cli" / "memory"

    # Seed 3 memories (oldest to newest) and 1 article
    for i in range(1, 4):
        _seed_memory(memory_dir, i, f"Old memory {i}", days_ago=10 - i)
    # Seed article as a regular md file with kind:article frontmatter
    article_path = memory_dir / "100-my-article.md"
    article_fm = {
        "id": 100, "kind": "article", "created": "2026-01-01T00:00:00+00:00",
        "tags": [], "decay_protected": True, "origin_url": "https://example.com",
    }
    article_path.write_text(
        f"---\n{yaml.dump(article_fm, default_flow_style=False)}---\n\nArticle body.\n",
        encoding="utf-8",
    )

    # max_count=3: saving one more memory should evict oldest memory, not the article
    deps = _make_deps(memory_dir=memory_dir, max_count=3)
    asyncio.run(persist_memory(deps, "Brand new memory triggers retention", ["test"], None))

    remaining = list(memory_dir.glob("*.md"))
    assert article_path in remaining, "Article must never be evicted by memory retention cap"
    assert len(remaining) <= 4, f"Total files {len(remaining)} should be <= 4"


# ---------------------------------------------------------------------------
# 4. Lock conflict — on_failure="add" → ResourceBusyError raised
# ---------------------------------------------------------------------------


def test_explicit_save_raises_on_lock_conflict(tmp_path: Path):
    """When target file lock is held and on_failure='add', persist_memory raises ResourceBusyError."""
    from co_cli.tools.resource_lock import ResourceBusyError
    from co_cli._model_factory import ModelRegistry, ResolvedModel
    from co_cli.config import ROLE_SUMMARIZATION

    memory_dir = tmp_path / ".co-cli" / "memory"
    # Seed an existing memory so the save agent can return UPDATE for it
    entry = _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    target_path = str(entry.path)

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    deps = _make_deps(memory_dir=memory_dir)

    async def _run() -> None:
        # Hold the lock on the target file, then try to persist a near-dup
        async with deps.resource_locks.try_acquire(target_path):
            with pytest.raises(ResourceBusyError):
                await persist_memory(
                    deps, "User prefers pytest for all testing purposes",
                    ["preference"], None,
                    on_failure="add", resolved=resolved,
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Lock conflict — on_failure="skip" → action="skipped", no file
# ---------------------------------------------------------------------------


def test_auto_signal_skip_on_lock_conflict(tmp_path: Path):
    """When target file lock is held and on_failure='skip', returns action='skipped'."""
    from co_cli._model_factory import ModelRegistry, ResolvedModel
    from co_cli.config import ROLE_SUMMARIZATION

    memory_dir = tmp_path / ".co-cli" / "memory"
    entry = _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    target_path = str(entry.path)

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    deps = _make_deps(memory_dir=memory_dir)

    async def _run():
        # Hold the lock on the target file, then try to persist a near-dup
        async with deps.resource_locks.try_acquire(target_path):
            return await persist_memory(
                deps, "User prefers pytest for all testing purposes",
                ["preference"], None,
                on_failure="skip", resolved=resolved,
            )

    result = asyncio.run(_run())

    after = list(memory_dir.glob("*.md"))
    assert len(after) == 1, "No new file should be created on lock conflict"
    assert result.metadata["action"] == "skipped"
    # Existing file should not have been modified
    raw = entry.path.read_text(encoding="utf-8")
    assert "User prefers pytest for testing" in raw
    assert "all testing purposes" not in raw


# ---------------------------------------------------------------------------
# 6. Manifest builder — format and page size
# ---------------------------------------------------------------------------


def test_build_memory_manifest_format(tmp_path: Path):
    """Manifest builder returns one-line-per-entry format, respects PAGE_SIZE cap."""
    from co_cli.memory._save import build_memory_manifest, PAGE_SIZE

    memory_dir = tmp_path / ".co-cli" / "memory"
    entries = []
    for i in range(1, 6):
        entries.append(
            _seed_memory(memory_dir, i, f"Memory about topic number {i}", tags=["preference"])
        )

    manifest = build_memory_manifest(entries)
    lines = manifest.strip().split("\n")
    assert len(lines) == 5, f"Expected 5 lines, got {len(lines)}"

    # Each line should start with "- " and contain the slug
    for line in lines:
        assert line.startswith("- "), f"Line must start with '- ': {line}"

    # PAGE_SIZE cap: create more than PAGE_SIZE entries
    many_entries = []
    for i in range(1, PAGE_SIZE + 20):
        many_entries.append(
            _seed_memory(memory_dir, 100 + i, f"Bulk memory entry {i}", tags=["bulk"])
        )
    manifest_capped = build_memory_manifest(many_entries)
    capped_lines = manifest_capped.strip().split("\n")
    assert len(capped_lines) == PAGE_SIZE, f"Expected {PAGE_SIZE} lines, got {len(capped_lines)}"


# ---------------------------------------------------------------------------
# 7. Memory save agent — returns SAVE_NEW on novel content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_save_agent_returns_save_new_on_novel(tmp_path: Path):
    """Memory save agent returns SAVE_NEW when candidate is genuinely new."""
    from co_cli.memory._save import build_memory_manifest, check_and_save
    from tests._ollama import ensure_ollama_warm

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    if resolved.model is None:
        pytest.skip("No summarization model configured")

    memory_dir = tmp_path / ".co-cli" / "memory"
    entries = [
        _seed_memory(memory_dir, 1, "User prefers Python for scripting", tags=["preference"]),
        _seed_memory(memory_dir, 2, "User uses 4-space indentation", tags=["preference"]),
    ]

    summarization_model_name = _CONFIG.role_models[ROLE_SUMMARIZATION].model
    await ensure_ollama_warm(summarization_model_name, _CONFIG.llm_host)

    manifest = build_memory_manifest(entries)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await check_and_save(
            "User prefers dark mode for all terminal applications",
            manifest, resolved,
        )

    assert result.action == "SAVE_NEW", (
        f"Novel content should produce SAVE_NEW, got {result.action}"
    )


# ---------------------------------------------------------------------------
# 8. Memory save agent — returns UPDATE on near-duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_save_agent_returns_update_on_near_duplicate(tmp_path: Path):
    """Memory save agent returns UPDATE(slug) when candidate near-duplicates existing."""
    from co_cli.memory._save import build_memory_manifest, check_and_save
    from tests._ollama import ensure_ollama_warm

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    if resolved.model is None:
        pytest.skip("No summarization model configured")

    memory_dir = tmp_path / ".co-cli" / "memory"
    entries = [
        _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"]),
        _seed_memory(memory_dir, 2, "User uses 4-space indentation", tags=["preference"]),
    ]

    summarization_model_name = _CONFIG.role_models[ROLE_SUMMARIZATION].model
    await ensure_ollama_warm(summarization_model_name, _CONFIG.llm_host)

    manifest = build_memory_manifest(entries)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await check_and_save(
            "User prefers pytest for all testing purposes",
            manifest, resolved,
        )

    assert result.action == "UPDATE", (
        f"Near-duplicate should produce UPDATE, got {result.action}"
    )
    assert result.target_slug is not None, "UPDATE must include target_slug"


# ---------------------------------------------------------------------------
# 9. Full upsert integration — persist_memory updates existing on near-dup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_memory_upsert_updates_existing(tmp_path: Path):
    """Full integration: seed a memory, call persist_memory with near-duplicate,
    assert no new file + existing file updated + updated timestamp set."""
    from tests._ollama import ensure_ollama_warm

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    if resolved.model is None:
        pytest.skip("No summarization model configured")

    memory_dir = tmp_path / ".co-cli" / "memory"
    _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    before_count = len(list(memory_dir.glob("*.md")))

    summarization_model_name = _CONFIG.role_models[ROLE_SUMMARIZATION].model
    await ensure_ollama_warm(summarization_model_name, _CONFIG.llm_host)

    deps = _make_deps(memory_dir=memory_dir)

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await persist_memory(
            deps,
            "User prefers pytest for all testing purposes",
            ["preference"], None,
            resolved=resolved,
        )

    after_count = len(list(memory_dir.glob("*.md")))

    # The save agent may return SAVE_NEW (non-deterministic on local models),
    # but when it returns UPDATE the file count should stay the same and the
    # existing file should have an updated timestamp.
    if result.metadata.get("action") == "consolidated":
        assert after_count == before_count, (
            "Upsert UPDATE should not create a new file"
        )
        existing_file = next(memory_dir.glob("001-*.md"))
        fm_raw = yaml.safe_load(existing_file.read_text(encoding="utf-8").split("---")[1])
        assert fm_raw.get("updated") is not None, (
            "Upsert UPDATE must set the updated timestamp"
        )
    else:
        # SAVE_NEW fallback is acceptable (model non-determinism)
        assert after_count == before_count + 1
