"""Functional tests for memory lifecycle — consolidation, dedup, retention, on_failure.

Covers apply_plan_atomically action invariants, dedup fast-path, overflow cut,
and timeout-driven on_failure behavior (timeout=0 via CoDeps, no mocks).
"""

import asyncio
from dataclasses import replace

from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from co_cli.config import settings, ROLE_REASONING, ROLE_SUMMARIZATION
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.deps import CoDeps, CoConfig
from co_cli.memory._consolidator import ConsolidationPlan, MemoryAction
from co_cli.memory._lifecycle import apply_plan_atomically, persist_memory
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.memory import MemoryEntry

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_RESOLVED = _REGISTRY.get(ROLE_REASONING, ResolvedModel(model=None, settings=None))


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
        
        memory_max_count=max_count,
        memory_consolidation_timeout_seconds=timeout,
    )
    if memory_dir is not None:
        cfg = replace(cfg, memory_dir=memory_dir)
    return CoDeps(shell=ShellBackend(), config=cfg)


# ---------------------------------------------------------------------------
# 1. Contradiction update — UPDATE action applied, file content updated
# ---------------------------------------------------------------------------


def test_update_action_sets_updated_timestamp(tmp_path: Path):
    """apply_plan_atomically UPDATE writes new_content into the target entry."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    entry = _seed_memory(memory_dir, 1, "User prefers dark mode", tags=["preference"])

    plan = ConsolidationPlan(actions=[MemoryAction(action="UPDATE", target_alias="M1")])
    alias_map = {"M1": entry}
    new_content = "User prefers light mode (changed from dark)"

    apply_plan_atomically(plan, alias_map, new_content)

    file_text = entry.path.read_text(encoding="utf-8")
    fm_raw = yaml.safe_load(file_text.split("---")[1])
    assert fm_raw.get("updated") is not None, "UPDATE action must set the updated timestamp"
    assert "light mode" in file_text, "UPDATE action must write new_content into entry"


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

    apply_plan_atomically(plan, alias_map, "irrelevant content")

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

    existing_file = next(memory_dir.glob("001-*.md"))
    fm_raw = yaml.safe_load(existing_file.read_text(encoding="utf-8").split("---")[1])
    assert fm_raw.get("updated") is not None, "Dedup must refresh the updated timestamp"


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
            on_failure="add", resolved=_RESOLVED,
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
            on_failure="skip", resolved=_RESOLVED,
        )
    )

    after_count = len(list(memory_dir.glob("*.md")))
    assert after_count == before_count, (
        "on_failure='skip' must NOT write a file when consolidation times out"
    )
    assert result["action"] == "skipped"


# ---------------------------------------------------------------------------
# LLM timing — single-call consolidation must complete within budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidation_single_call_within_budget(tmp_path: Path):
    """consolidate() returns a valid plan within 20s and obeys schema invariants.

    Timing regression guard: old two-phase approach (extract_facts + resolve) took
    12-30s on a thinking model. New single-call path must complete within 20s.

    Schema invariants verified:
    - ADD action must have target_alias=None
    - UPDATE/DELETE actions must have target_alias set to a valid existing alias
    - Candidate with no match in existing memories must produce at least one ADD
    """
    from co_cli.memory._consolidator import consolidate
    from tests._ollama import ensure_ollama_warm

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    memory_dir = tmp_path / ".co-cli" / "memory"
    existing = [
        _seed_memory(memory_dir, 1, "User prefers Python for scripting", tags=["preference"]),
        _seed_memory(memory_dir, 2, "User uses 4-space indentation", tags=["preference"]),
        _seed_memory(memory_dir, 3, "User avoids global state in tools", tags=["preference"]),
    ]
    valid_aliases = {f"M{i}" for i in range(1, len(existing) + 1)}

    summarization_model_name = _CONFIG.role_models[ROLE_SUMMARIZATION].model
    await ensure_ollama_warm(summarization_model_name, _CONFIG.llm_host)

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        plan = await consolidate(
            "User prefers dark mode for all terminal applications",
            existing,
            resolved,
            timeout_seconds=LLM_NON_REASONING_TIMEOUT_SECS,
        )

    assert len(plan.actions) > 0, "consolidate() must return at least one action"

    # Schema invariants: target_alias rules per action type
    for action in plan.actions:
        if action.action in ("UPDATE", "DELETE"):
            assert action.target_alias is not None, (
                f"{action.action} action must set target_alias; got None"
            )
            assert action.target_alias in valid_aliases, (
                f"{action.action} target_alias {action.target_alias!r} is not a valid alias "
                f"(valid: {sorted(valid_aliases)})"
            )
        elif action.action == "ADD":
            assert action.target_alias is None, (
                f"ADD action must have target_alias=None; got {action.target_alias!r}"
            )

    # Dark mode is genuinely new — none of the existing memories mention it
    actions_taken = {a.action for a in plan.actions}
    assert "ADD" in actions_taken, (
        f"Dark mode preference is new information; expected ADD in plan but got {actions_taken}"
    )
