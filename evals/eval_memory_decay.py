#!/usr/bin/env python3
"""Eval: memory-decay — verify decay triggers correctly at capacity limit.

Deterministic (no LLM). Pre-seeds N memory files at exactly memory_max_count,
then calls save_memory() directly via RunContext to trigger decay. Inspects
the file system for correct results.

Target flow:   tools/memory.py:save_memory() → _decay_memories() →
               _decay_summarize() or _decay_cut()
Critical impact: unbounded memory growth degrades recall quality. Decay that
                 deletes the wrong memories destroys learned knowledge.

Prerequisites: None (deterministic, no LLM needed).

Usage:
    uv run python evals/eval_memory_decay.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

import yaml  # noqa: E402
from pydantic_ai._run_context import RunContext  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from co_cli.agent import get_agent  # noqa: E402
from co_cli.tools.memory import save_memory, _load_all_memories  # noqa: E402

from evals._common import make_eval_deps  # noqa: E402


# ---------------------------------------------------------------------------
# Memory seeding helpers
# ---------------------------------------------------------------------------


def _seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
    decay_protected: bool = False,
) -> Path:
    """Write a memory markdown file with valid frontmatter."""
    created = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
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
    """Build a RunContext with memory settings for decay testing."""
    deps = make_eval_deps(
        session_id="eval-memory-decay",
        memory_max_count=max_count,
        memory_decay_strategy=decay_strategy,
        memory_decay_percentage=decay_percentage,
    )
    agent, _, _ = get_agent()
    return RunContext(deps=deps, model=agent.model, usage=RunUsage())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_decay_summarize_triggers() -> bool:
    """Seed max_count memories, save one more — summarize decay fires."""
    print("  Test: summarize decay triggers at limit...", end=" ")
    max_count = 10

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            # Seed exactly max_count memories (oldest first)
            for i in range(1, max_count + 1):
                _seed_memory(
                    memory_dir, i, f"Old memory number {i}",
                    days_ago=max_count - i + 10,
                )

            # Verify seeded count
            before = list(memory_dir.glob("*.md"))
            assert len(before) == max_count, f"Seeded {len(before)}, expected {max_count}"

            # Save one more to trigger decay
            ctx = _make_ctx(max_count=max_count, decay_strategy="summarize")
            result = asyncio.run(
                save_memory(ctx, "Brand new important memory", tags=["test"])
            )

            after = list(memory_dir.glob("*.md"))
            total_after = len(after)

            # Check: total should be below or at max_count (decay removed some)
            # Summarize creates 1 consolidated file, so net removal = decayed - 1
            if total_after > max_count:
                print(f"FAIL (total {total_after} > max {max_count})")
                return False

            # Check: a consolidated summary file exists
            has_consolidated = any(
                "_consolidated" in p.read_text(encoding="utf-8")
                for p in after
            )
            if not has_consolidated:
                print("FAIL (no consolidated summary file)")
                return False

            print("PASS")
            return True
        finally:
            os.chdir(orig_cwd)


def test_decay_cut_triggers() -> bool:
    """Seed max_count memories, save one more — cut decay fires."""
    print("  Test: cut decay triggers at limit...", end=" ")
    max_count = 10

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            for i in range(1, max_count + 1):
                _seed_memory(
                    memory_dir, i, f"Old memory number {i}",
                    days_ago=max_count - i + 10,
                )

            ctx = _make_ctx(max_count=max_count, decay_strategy="cut")
            result = asyncio.run(
                save_memory(ctx, "Brand new important memory", tags=["test"])
            )

            after = list(memory_dir.glob("*.md"))
            total_after = len(after)

            # Check: total should be at or below max_count
            if total_after > max_count:
                print(f"FAIL (total {total_after} > max {max_count})")
                return False

            # Check: NO consolidated summary file (cut strategy just deletes)
            has_consolidated = any(
                "_consolidated" in p.read_text(encoding="utf-8")
                for p in after
            )
            if has_consolidated:
                print("FAIL (consolidated file found in cut strategy)")
                return False

            print("PASS")
            return True
        finally:
            os.chdir(orig_cwd)


def test_decay_protected_survives() -> bool:
    """Protected memories survive decay even at capacity."""
    print("  Test: protected memories survive decay...", end=" ")
    max_count = 10

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            # Seed max_count memories, 2 oldest are protected
            for i in range(1, max_count + 1):
                protected = i <= 2  # IDs 1 and 2 are protected
                _seed_memory(
                    memory_dir, i, f"Memory {i} {'PROTECTED' if protected else 'normal'}",
                    days_ago=max_count - i + 10,
                    decay_protected=protected,
                )

            # Save one more to trigger decay
            ctx = _make_ctx(
                max_count=max_count, decay_strategy="summarize",
                decay_percentage=0.3,
            )
            result = asyncio.run(
                save_memory(ctx, "New memory triggering decay", tags=["test"])
            )

            after = list(memory_dir.glob("*.md"))
            remaining_ids = set()
            for p in after:
                content = p.read_text(encoding="utf-8")
                fm_match = yaml.safe_load(
                    content.split("---")[1]
                ) if "---" in content else {}
                if isinstance(fm_match, dict) and "id" in fm_match:
                    remaining_ids.add(fm_match["id"])

            # Protected memories (IDs 1, 2) must still exist
            protected_survived = {1, 2}.issubset(remaining_ids)
            if not protected_survived:
                missing = {1, 2} - remaining_ids
                print(f"FAIL (protected memories {missing} were decayed)")
                return False

            print("PASS")
            return True
        finally:
            os.chdir(orig_cwd)


def test_decay_below_limit() -> bool:
    """Below capacity — no decay triggered."""
    print("  Test: no decay below limit...", end=" ")
    max_count = 10

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge/memories"
            memory_dir.mkdir(parents=True)

            # Seed max_count - 5 memories (well below limit)
            seed_count = max_count - 5
            for i in range(1, seed_count + 1):
                _seed_memory(
                    memory_dir, i, f"Memory number {i}",
                    days_ago=10 - i,
                )

            ctx = _make_ctx(max_count=max_count, decay_strategy="summarize")
            result = asyncio.run(
                save_memory(ctx, "New memory within budget", tags=["test"])
            )

            after = list(memory_dir.glob("*.md"))
            expected = seed_count + 1  # original + 1 new, no decay

            if len(after) != expected:
                print(f"FAIL (expected {expected}, got {len(after)})")
                return False

            # No consolidated file should exist
            has_consolidated = any(
                "_consolidated" in p.read_text(encoding="utf-8")
                for p in after
            )
            if has_consolidated:
                print("FAIL (decay triggered below limit)")
                return False

            print("PASS")
            return True
        finally:
            os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Decay Lifecycle (W7, deterministic)")
    print("=" * 60)
    print()

    results = [
        test_decay_summarize_triggers(),
        test_decay_cut_triggers(),
        test_decay_protected_survives(),
        test_decay_below_limit(),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {passed}/{total} passed")
    print(f"{'=' * 60}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
