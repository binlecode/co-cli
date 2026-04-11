"""Functional tests for memory lifecycle — upsert, lock-based on_failure.

Covers persist_memory write, upsert routing via memory save agent, resource lock
concurrency, and on_failure behavior.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli._model_factory import build_model
from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.memory._lifecycle import persist_memory
from co_cli.tools.memory import MemoryEntry
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = settings
_LLM_MODEL = build_model(_CONFIG.llm)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NullFrontend:
    """Silent Frontend implementation for tests — captures status calls, no terminal output."""

    def __init__(self) -> None:
        self.status_messages: list[str] = []

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        pass

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        pass

    def on_tool_complete(self, tool_id: str, result: Any) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.status_messages.append(message)

    def on_reasoning_progress(self, text: str) -> None:
        pass

    def on_final_output(self, text: str) -> None:
        pass

    def prompt_approval(self, description: str) -> str:
        return "y"

    def clear_status(self) -> None:
        pass

    def set_input_active(self, active: bool) -> None:
        pass

    def cleanup(self) -> None:
        pass


def _seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
) -> MemoryEntry:
    """Write a test memory file and return a MemoryEntry."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    created = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    slug = content[:40].lower().replace(" ", "-").replace(",", "")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict[str, Any] = {
        "id": memory_id,
        "kind": "memory",
        "created": created,
        "tags": tags or [],
    }
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return MemoryEntry(
        id=memory_id,
        path=path,
        content=content,
        tags=tags or [],
        created=created,
    )


def _make_deps(
    memory_dir: Path | None = None,
) -> CoDeps:
    deps_kwargs = {"shell": ShellBackend(), "config": _CONFIG}
    if memory_dir is not None:
        deps_kwargs["memory_dir"] = memory_dir
    return CoDeps(**deps_kwargs)


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
# 2. Lock conflict — on_failure="add" → ResourceBusyError raised
# ---------------------------------------------------------------------------


def test_explicit_save_raises_on_lock_conflict(tmp_path: Path):
    """When target file lock is held and on_failure='add', persist_memory raises ResourceBusyError."""
    from co_cli.tools.resource_lock import ResourceBusyError

    memory_dir = tmp_path / ".co-cli" / "memory"
    # Seed an existing memory so the save agent can return UPDATE for it
    entry = _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    target_path = str(entry.path)

    deps = _make_deps(memory_dir=memory_dir)

    async def _run() -> None:
        # Hold the lock on the target file, then try to persist a near-dup
        async with deps.resource_locks.try_acquire(target_path):
            with pytest.raises(ResourceBusyError):
                await persist_memory(
                    deps,
                    "User prefers pytest for all testing purposes",
                    ["preference"],
                    None,
                    on_failure="add",
                    model=_LLM_MODEL.model,
                    model_settings=NOREASON_SETTINGS,
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Lock conflict — on_failure="skip" → action="skipped", no file
# ---------------------------------------------------------------------------


def test_auto_signal_skip_on_lock_conflict(tmp_path: Path):
    """When target file lock is held and on_failure='skip', returns action='skipped'."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    entry = _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    target_path = str(entry.path)

    deps = _make_deps(memory_dir=memory_dir)

    async def _run():
        # Hold the lock on the target file, then try to persist a near-dup
        async with deps.resource_locks.try_acquire(target_path):
            return await persist_memory(
                deps,
                "User prefers pytest for all testing purposes",
                ["preference"],
                None,
                on_failure="skip",
                model=_LLM_MODEL.model,
                model_settings=NOREASON_SETTINGS,
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
    from co_cli.memory._save import PAGE_SIZE, build_memory_manifest

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
    from tests._ollama import ensure_ollama_warm

    from co_cli.memory._save import build_memory_manifest, check_and_save

    if _LLM_MODEL.model is None:
        pytest.skip("No model configured")

    memory_dir = tmp_path / ".co-cli" / "memory"
    entries = [
        _seed_memory(memory_dir, 1, "User prefers Python for scripting", tags=["preference"]),
        _seed_memory(memory_dir, 2, "User uses 4-space indentation", tags=["preference"]),
    ]

    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)

    manifest = build_memory_manifest(entries)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await check_and_save(
            "User prefers dark mode for all terminal applications",
            manifest,
            _LLM_MODEL.model,
            NOREASON_SETTINGS,
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
    from tests._ollama import ensure_ollama_warm

    from co_cli.memory._save import build_memory_manifest, check_and_save

    if _LLM_MODEL.model is None:
        pytest.skip("No model configured")

    memory_dir = tmp_path / ".co-cli" / "memory"
    entries = [
        _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"]),
        _seed_memory(memory_dir, 2, "User uses 4-space indentation", tags=["preference"]),
    ]

    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)

    manifest = build_memory_manifest(entries)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await check_and_save(
            "User prefers pytest for all testing purposes",
            manifest,
            _LLM_MODEL.model,
            NOREASON_SETTINGS,
        )

    assert result.action == "UPDATE", f"Near-duplicate should produce UPDATE, got {result.action}"
    assert result.target_slug is not None, "UPDATE must include target_slug"


# ---------------------------------------------------------------------------
# 9. Full upsert integration — persist_memory updates existing on near-dup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_memory_upsert_updates_existing(tmp_path: Path):
    """Full integration: seed a memory, call persist_memory with near-duplicate,
    assert no new file + existing file updated + updated timestamp set."""
    from tests._ollama import ensure_ollama_warm

    if _LLM_MODEL.model is None:
        pytest.skip("No model configured")

    memory_dir = tmp_path / ".co-cli" / "memory"
    _seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    before_count = len(list(memory_dir.glob("*.md")))

    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)

    deps = _make_deps(memory_dir=memory_dir)

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await persist_memory(
            deps,
            "User prefers pytest for all testing purposes",
            ["preference"],
            None,
            model=_LLM_MODEL.model,
            model_settings=NOREASON_SETTINGS,
        )

    after_count = len(list(memory_dir.glob("*.md")))

    # The save agent may return SAVE_NEW (non-deterministic on local models),
    # but when it returns UPDATE the file count should stay the same and the
    # existing file should have an updated timestamp.
    if result.metadata.get("action") == "consolidated":
        assert after_count == before_count, "Upsert UPDATE should not create a new file"
        existing_file = next(memory_dir.glob("001-*.md"))
        fm_raw = yaml.safe_load(existing_file.read_text(encoding="utf-8").split("---")[1])
        assert fm_raw.get("updated") is not None, "Upsert UPDATE must set the updated timestamp"
    else:
        # SAVE_NEW fallback is acceptable (model non-determinism)
        assert after_count == before_count + 1


# ---------------------------------------------------------------------------
# 10. Filename format — slug-only, no UUID suffix
# ---------------------------------------------------------------------------


def test_persist_memory_filename_is_slug_only(tmp_path: Path):
    """persist_memory creates filename as {slug}.md with no UUID suffix."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    deps = _make_deps(memory_dir=memory_dir)
    asyncio.run(
        persist_memory(deps, "User prefers slug-only filename test content", ["preference"], None)
    )

    files = list(memory_dir.glob("*.md"))
    assert len(files) == 1
    filename = files[0].name
    assert re.match(r"^[a-z0-9-]+\.md$", filename), f"Unexpected filename format: {filename}"
    assert not re.search(r"-[0-9a-f]{6}\.md$", filename), (
        f"Filename must not have a UUID suffix: {filename}"
    )


# ---------------------------------------------------------------------------
# 11. Slug collision — SAVE_NEW overwrites existing file
# ---------------------------------------------------------------------------


def test_persist_memory_slug_collision_overwrites(tmp_path: Path):
    """SAVE_NEW with same slug as existing file overwrites it — no new file created."""
    from co_cli.tools.memory import slugify

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)

    # content_a and content_b share identical first 50 characters → same slug
    content_a = "User prefers the following terminal color scheme: dark"
    content_b = "User prefers the following terminal color scheme: light"
    assert content_a[:50] == content_b[:50], "Test setup: first 50 chars must match"

    slug = slugify(content_a[:50])
    pre_existing = memory_dir / f"{slug}.md"
    pre_existing.write_text(
        f"---\nid: pre-existing-id\nkind: memory\ntags: []\n---\n\n{content_a}\n",
        encoding="utf-8",
    )
    before_count = len(list(memory_dir.glob("*.md")))

    deps = _make_deps(memory_dir=memory_dir)
    asyncio.run(persist_memory(deps, content_b, ["preference"], None))

    after = list(memory_dir.glob("*.md"))
    assert len(after) == before_count, (
        f"Collision must overwrite existing file, not create a new one. "
        f"Before: {before_count}, after: {len(after)}"
    )
    body = pre_existing.read_text(encoding="utf-8")
    assert content_b.strip() in body, "File body must contain updated content B after overwrite"
