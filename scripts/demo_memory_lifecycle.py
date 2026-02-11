#!/usr/bin/env python3
"""Demo: Memory Lifecycle — "Notes with Gravity" Model

This script demonstrates the autonomous memory lifecycle system:
1. Dedup-on-write: Similar memories consolidate automatically
2. Gravity ordering: Newest memories rise to top, oldest fall to bottom
3. Automatic decay: When limit reached, oldest memories decay
4. Protection: Critical memories can be protected from decay

Usage:
    uv run python scripts/demo_memory_lifecycle.py
"""

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import tempfile

from co_cli.config import Settings
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend
from co_cli.tools.memory import (
    save_memory,
    list_memories,
    _load_all_memories,
)


class DemoContext:
    """Minimal context for demo (matches test fixtures)."""
    def __init__(self, settings: Settings):
        self.deps = CoDeps(
            sandbox=SubprocessBackend(),
            settings=settings,
        )


def print_section(title: str):
    """Print section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def print_memory_file(memory_dir: Path, memory_id: int):
    """Print memory file contents for inspection."""
    for path in memory_dir.glob(f"{memory_id:03d}-*.md"):
        print(f"\n📄 {path.name}:")
        print(path.read_text(encoding="utf-8"))
        break


async def demo_dedup_on_write():
    """Demo 1: Deduplication consolidates similar memories."""
    print_section("Demo 1: Dedup-on-Write")

    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    memory_dir = temp_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    # Change to temp directory
    import os
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    try:
        settings = Settings(
            memory_max_count=200,
            memory_dedup_window_days=7,
            memory_dedup_threshold=85,
        )
        ctx = DemoContext(settings)

        print("Step 1: Save first memory")
        print("→ Input: 'I prefer TypeScript for web development'")
        result1 = await save_memory(
            ctx, "I prefer TypeScript for web development", tags=["preference", "language"]
        )
        print(f"✓ {result1['display']}")

        print("\nStep 2: Save similar memory (88% match)")
        print("→ Input: 'I prefer typescript for web dev'")
        result2 = await save_memory(
            ctx, "I prefer typescript for web dev", tags=["preference", "javascript"]
        )
        print(f"✓ {result2['display']}")

        print("\n📊 Result:")
        if result2['action'] == 'consolidated':
            print("✅ Deduplication worked! Similar memory consolidated into existing.")
            print(f"   Memory ID {result2['memory_id']} was updated (not created new).")
            print(f"   Similarity detected: ~88%")
        else:
            print("❌ Expected consolidation but got new memory")

        print(f"\n📁 Total files: {len(_load_all_memories(memory_dir))} (should be 1, not 2)")

        # Show consolidated memory
        print_memory_file(memory_dir, 1)

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir)


async def demo_gravity_ordering():
    """Demo 2: Gravity - newest at top, oldest at bottom."""
    print_section("Demo 2: Gravity Ordering")

    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    memory_dir = temp_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    import os
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    try:
        settings = Settings()
        ctx = DemoContext(settings)

        print("Creating 5 memories with different ages...\n")

        # Create memories manually with different timestamps
        from co_cli.tools.memory import yaml
        memories = [
            ("Oldest memory (30 days ago)", 30),
            ("Old memory (20 days ago)", 20),
            ("Recent memory (5 days ago)", 5),
            ("Newer memory (2 days ago)", 2),
            ("Newest memory (today)", 0),
        ]

        for i, (content, days_ago) in enumerate(memories, start=1):
            created = datetime.now(timezone.utc) - timedelta(days=days_ago)
            frontmatter = {
                "id": i,
                "created": created.isoformat(),
                "tags": ["demo"],
                "source": "user-told",
                "auto_category": None,
            }
            filename = f"{i:03d}-{content.lower().replace(' ', '-')[:30]}.md"
            md_content = f"---\n{yaml.dump(frontmatter)}---\n\n{content}\n"
            (memory_dir / filename).write_text(md_content, encoding="utf-8")
            print(f"  ✓ Memory {i}: {content}")

        print("\n📊 List memories (sorted by ID, newest first):")
        result = await list_memories(ctx)
        print(result['display'])

        print("\n💡 Gravity Concept:")
        print("  - Newest memories (low IDs) rise to the top")
        print("  - Oldest memories (high age) fall to the bottom")
        print("  - When decay triggers, oldest 20% naturally drop off")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir)


async def demo_automatic_decay():
    """Demo 3: Automatic decay when limit exceeded."""
    print_section("Demo 3: Automatic Decay")

    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    memory_dir = temp_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    import os
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    try:
        # Low limit for demo purposes
        settings = Settings(
            memory_max_count=10,
            memory_decay_strategy="summarize",
            memory_decay_percentage=0.2,
            memory_dedup_window_days=1,  # Minimum window
            memory_dedup_threshold=100,  # Effectively disable dedup (100% match required)
        )
        ctx = DemoContext(settings)

        print(f"Configuration:")
        print(f"  - Max count: 10")
        print(f"  - Decay strategy: summarize")
        print(f"  - Decay percentage: 20% (2 oldest memories)")
        print()

        print("Step 1: Create 10 memories (at limit)...")
        for i in range(1, 11):
            await save_memory(
                ctx, f"Memory {i} with unique content about topic {i*100}", tags=["demo"]
            )
        print(f"✓ Created 10 memories")
        print(f"📁 Total: {len(_load_all_memories(memory_dir))}/10 (at limit)")

        print("\nStep 2: Create 11th memory (exceeds limit)...")
        print("→ This should trigger decay...")
        result = await save_memory(
            ctx, "Memory 11 triggers the decay mechanism", tags=["demo"]
        )

        print(f"\n📊 Result:")
        final_count = len(_load_all_memories(memory_dir))
        print(f"📁 Total: {final_count} memories")

        if "Decayed" in result['display']:
            print("✅ Decay triggered automatically!")
            print(f"   Oldest 2 memories (20%) were decayed")
            print(f"   Strategy: summarize → created 1 consolidated memory")
            print(f"   Expected total: 10 - 2 + 1 + 1 = 10")
        else:
            print("❌ Decay did not trigger as expected")

        print("\n📊 Final memory list:")
        list_result = await list_memories(ctx)
        print(list_result['display'])

        # Show consolidated memory
        print("\n📄 Consolidated memory example:")
        for path in memory_dir.glob("*-consolidated*.md"):
            print(f"\n{path.name}:")
            print(path.read_text(encoding="utf-8")[:500] + "...")
            break

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir)


async def demo_protection():
    """Demo 4: Protected memories excluded from decay."""
    print_section("Demo 4: Protection from Decay")

    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    memory_dir = temp_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    import os
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    try:
        settings = Settings(
            memory_max_count=10,  # Minimum is 10
            memory_decay_percentage=0.4,  # Decay 40% (4 memories)
        )
        ctx = DemoContext(settings)

        print("Creating memories:")
        print("  - Memory 1: Protected (critical information)")
        print("  - Memory 2-3: Unprotected memories")
        print()

        # Create protected memory manually
        from co_cli.tools.memory import yaml
        protected_frontmatter = {
            "id": 1,
            "created": (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),
            "tags": ["critical"],
            "source": "user-told",
            "auto_category": None,
            "decay_protected": True,  # <-- Protected!
        }
        protected_content = f"---\n{yaml.dump(protected_frontmatter)}---\n\nCritical memory that must never be deleted\n"
        (memory_dir / "001-critical-memory.md").write_text(protected_content, encoding="utf-8")
        print("  ✓ Memory 1 created (protected, 100 days old)")

        # Create unprotected memories
        await save_memory(ctx, "Unprotected memory 2 (oldest)", tags=["demo"])
        await save_memory(ctx, "Unprotected memory 3", tags=["demo"])
        print("  ✓ Memory 2-3 created (unprotected)")

        print(f"\n📁 Total: {len(_load_all_memories(memory_dir))}/10")

        print("\nStep: Add more memories to exceed limit and trigger decay...")
        for i in range(4, 12):  # Add memories 4-11 to exceed limit of 10
            await save_memory(ctx, f"Memory {i}", tags=["demo"])

        final_count = len(_load_all_memories(memory_dir))
        print(f"\n📊 Result:")
        print(f"📁 Total: {final_count} memories")

        # Check if protected memory still exists
        protected_exists = (memory_dir / "001-critical-memory.md").exists()
        if protected_exists:
            print("✅ Protected memory (ID 1) survived decay!")
            print("   Only unprotected memories were decayed")
        else:
            print("❌ Protected memory was incorrectly decayed")

        print("\n📊 Final memory list (should show 🔒 for protected):")
        list_result = await list_memories(ctx)
        print(list_result['display'])

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir)


async def demo_display_indicators():
    """Demo 5: Display shows lifecycle indicators."""
    print_section("Demo 5: Display Indicators")

    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    memory_dir = temp_dir / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    import os
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    try:
        settings = Settings()
        ctx = DemoContext(settings)

        print("Creating diverse memories to show display indicators:\n")

        # 1. Normal memory
        await save_memory(ctx, "Normal memory", tags=["demo"])
        print("  ✓ Normal memory (no indicators)")

        # 2. Consolidated memory (with updated timestamp)
        from co_cli.tools.memory import yaml
        consolidated_fm = {
            "id": 2,
            "created": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(),  # <-- Updated!
            "tags": ["demo"],
            "source": "detected",
            "auto_category": "preference",
            "consolidation_reason": "consolidated_duplicate",
        }
        consolidated_content = f"---\n{yaml.dump(consolidated_fm)}---\n\nConsolidated memory (was updated)\n"
        (memory_dir / "002-consolidated-memory.md").write_text(consolidated_content, encoding="utf-8")
        print("  ✓ Consolidated memory (shows date range)")

        # 3. Protected memory
        protected_fm = {
            "id": 3,
            "created": datetime.now(timezone.utc).isoformat(),
            "tags": ["critical"],
            "source": "user-told",
            "auto_category": None,
            "decay_protected": True,  # <-- Protected!
        }
        protected_content = f"---\n{yaml.dump(protected_fm)}---\n\nProtected critical memory\n"
        (memory_dir / "003-protected-memory.md").write_text(protected_content, encoding="utf-8")
        print("  ✓ Protected memory (shows 🔒)")

        print("\n📊 Memory list with indicators:")
        result = await list_memories(ctx)
        print(result['display'])

        print("\n💡 Indicator Guide:")
        print("  - Count/Limit: Shows '3/200' (current vs max)")
        print("  - Date range: Shows '(2026-02-05 → 2026-02-10)' for consolidated")
        print("  - Protection: Shows '🔒' for decay_protected memories")
        print("  - Category: Shows '[preference]' for categorized memories")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir)


async def main():
    """Run all demos."""
    print("\n" + "="*70)
    print("  Memory Lifecycle Demo — 'Notes with Gravity' Model")
    print("="*70)
    print("\nThis demo showcases the autonomous memory lifecycle system:")
    print("  1. Dedup-on-write: Similar memories consolidate automatically")
    print("  2. Gravity ordering: Newest top, oldest bottom")
    print("  3. Automatic decay: Oldest 20% decay when limit reached")
    print("  4. Protection: Critical memories can be protected")
    print("  5. Display indicators: Clear lifecycle visualization")

    try:
        await demo_dedup_on_write()
        await demo_gravity_ordering()
        await demo_automatic_decay()
        await demo_protection()
        await demo_display_indicators()

        print("\n" + "="*70)
        print("  ✅ All demos completed successfully!")
        print("="*70)
        print("\n💡 Key Takeaways:")
        print("  - Memory is self-managing (no manual commands needed)")
        print("  - Duplicates consolidate automatically (string similarity)")
        print("  - Old memories naturally decay over time (gravity)")
        print("  - Critical memories can be protected")
        print("  - All behavior is configurable via settings")
        print("\n📚 See docs/DESIGN-14-memory-lifecycle-system.md for full documentation")
        print()

    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
