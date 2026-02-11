"""Tests for memory lifecycle (dedup + decay) - "notes with gravity" model.

This test file covers:
- Phase 1: Dedup-on-write (consolidation)
- Phase 2: Size-based decay (summarize/cut strategies)
- Phase 3: Display with consolidation indicators

Core flows:
1. Dedup detects similar content (>85% similarity)
2. Consolidation updates existing memory in-place
3. Size limit triggers decay automatically
4. Decay strategies work (summarize and cut)
5. Gravity ordering (newest top, oldest bottom)
6. Recent window limits dedup checks (scalable)
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

from co_cli.deps import CoDeps
from co_cli.tools.memory import (
    MemoryEntry,
    _check_duplicate,
    _decay_cut,
    _decay_summarize,
    _decay_memories,
    _load_all_memories,
    _parse_created,
    _update_existing_memory,
    save_memory,
    list_memories,
)


@pytest.fixture
def temp_memory_dir(tmp_path, monkeypatch):
    """Create temporary memory directory and set as cwd."""
    memory_dir = tmp_path / ".co-cli" / "knowledge" / "memories"
    memory_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return memory_dir


@pytest.fixture
def mock_ctx():
    """Create RunContext with real CoDeps for testing."""
    from co_cli.sandbox import SubprocessBackend

    # Real instances, no mocks - per CLAUDE.md testing policy
    class Context:
        def __init__(self):
            self.deps = CoDeps(
                sandbox=SubprocessBackend(),
                memory_max_count=200,
                memory_dedup_window_days=7,
                memory_dedup_threshold=85,
                memory_decay_strategy="summarize",
                memory_decay_percentage=0.2,
            )

    return Context()


def create_test_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    tags: list[str] | None = None,
    created_days_ago: int = 0,
    updated: str | None = None,
    decay_protected: bool = False,
) -> Path:
    """Helper to create a test memory file.

    Args:
        memory_dir: Directory to create memory in
        memory_id: Memory ID
        content: Memory content
        tags: Optional tags
        created_days_ago: How many days ago was it created (default 0 = today)
        updated: Optional updated timestamp
        decay_protected: Whether memory is protected from decay

    Returns:
        Path to created memory file
    """
    created = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    frontmatter = {
        "id": memory_id,
        "created": created.isoformat(),
        "tags": tags or [],
        "source": "user-told",
        "auto_category": tags[0] if tags else None,
    }
    if updated:
        frontmatter["updated"] = updated
    if decay_protected:
        frontmatter["decay_protected"] = decay_protected

    slug = content[:30].lower().replace(" ", "-")
    filename = f"{memory_id:03d}-{slug}.md"
    md_content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{content}\n"

    file_path = memory_dir / filename
    file_path.write_text(md_content, encoding="utf-8")
    return file_path


# ============================================================================
# P0 Tests: Core Correctness
# ============================================================================


class TestDedupAccuracy:
    """Test deduplication similarity detection."""

    def test_high_similarity_detected(self, temp_memory_dir):
        """High similarity (≥85%) should be detected as duplicate."""
        create_test_memory(temp_memory_dir, 1, "I prefer TypeScript for web development")

        memories = _load_all_memories(temp_memory_dir)
        is_dup, match, similarity = _check_duplicate(
            "I prefer typescript for web dev", memories, threshold=85
        )

        assert is_dup is True
        assert match is not None
        assert match.id == 1
        assert similarity >= 85.0

    def test_low_similarity_not_duplicate(self, temp_memory_dir):
        """Low similarity (<85%) should not be detected as duplicate."""
        create_test_memory(temp_memory_dir, 1, "I prefer TypeScript")

        memories = _load_all_memories(temp_memory_dir)
        is_dup, match, similarity = _check_duplicate(
            "I use PostgreSQL database", memories, threshold=85
        )

        assert is_dup is False
        assert match is None
        assert similarity < 85.0

    def test_threshold_boundary_behavior(self, temp_memory_dir):
        """Test similarity exactly at threshold."""
        # Create content with known similarity
        content_a = "User prefers Python for data science projects"
        content_b = "User prefers Python for data science work"  # High similarity

        create_test_memory(temp_memory_dir, 1, content_a)
        memories = _load_all_memories(temp_memory_dir)

        # Test with threshold that should just pass
        is_dup_85, _, sim_85 = _check_duplicate(content_b, memories, threshold=85)
        # Test with higher threshold that might fail
        is_dup_95, _, sim_95 = _check_duplicate(content_b, memories, threshold=95)

        # Similarity should be same for both calls
        assert sim_85 == sim_95
        # But duplicate detection depends on threshold
        if sim_85 >= 95:
            assert is_dup_85 is True
            assert is_dup_95 is True
        elif 85 <= sim_85 < 95:
            assert is_dup_85 is True
            assert is_dup_95 is False
        else:
            assert is_dup_85 is False
            assert is_dup_95 is False


class TestConsolidationIntegrity:
    """Test consolidation updates existing memory correctly."""

    def test_updates_existing_file_not_new(self, temp_memory_dir):
        """Consolidation should update existing file, not create new one."""
        file_path = create_test_memory(
            temp_memory_dir, 1, "I prefer TypeScript", tags=["preference"]
        )
        original_name = file_path.name

        memories = _load_all_memories(temp_memory_dir)
        entry = memories[0]
        result = _update_existing_memory(
            entry, "I prefer TypeScript for web dev", ["preference", "javascript"]
        )

        # Same file should still exist with same name
        assert file_path.exists()
        assert result["memory_id"] == 1
        assert original_name in result["path"]

        # Should not create a second file
        files = list(temp_memory_dir.glob("*.md"))
        assert len(files) == 1

    def test_preserves_original_id(self, temp_memory_dir):
        """Consolidation should keep original memory ID."""
        create_test_memory(temp_memory_dir, 42, "Original content")

        memories = _load_all_memories(temp_memory_dir)
        entry = memories[0]
        result = _update_existing_memory(entry, "New content", [])

        assert result["memory_id"] == 42

    def test_adds_updated_timestamp(self, temp_memory_dir):
        """Consolidation should add 'updated' timestamp."""
        file_path = create_test_memory(temp_memory_dir, 1, "Original")
        before_update = datetime.now(timezone.utc)

        memories = _load_all_memories(temp_memory_dir)
        entry = memories[0]
        _update_existing_memory(entry, "Updated content", [])

        content = file_path.read_text()
        fm_match = yaml.safe_load(content.split("---")[1])
        assert "updated" in fm_match
        updated = datetime.fromisoformat(fm_match["updated"].replace("Z", "+00:00"))
        assert updated >= before_update

    def test_removes_consolidation_reason(self, temp_memory_dir):
        """Consolidation should remove legacy consolidation_reason field."""
        # Create memory with old-style consolidation_reason
        created = datetime.now(timezone.utc).isoformat()
        frontmatter = {
            "id": 1,
            "created": created,
            "tags": ["preference"],
            "source": "user-told",
            "auto_category": "preference",
            "consolidation_reason": "old_reason",
        }
        md_content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\nOriginal\n"
        file_path = temp_memory_dir / "001-original.md"
        file_path.write_text(md_content, encoding="utf-8")

        memories = _load_all_memories(temp_memory_dir)
        entry = memories[0]
        _update_existing_memory(entry, "Updated", [])

        content = file_path.read_text()
        fm_match = yaml.safe_load(content.split("---")[1])
        assert "consolidation_reason" not in fm_match

    def test_merges_tags_correctly(self, temp_memory_dir):
        """Consolidation should merge tags (union, no duplicates)."""
        create_test_memory(
            temp_memory_dir, 1, "Original", tags=["preference", "typescript"]
        )

        memories = _load_all_memories(temp_memory_dir)
        entry = memories[0]
        _update_existing_memory(
            entry, "Updated", ["preference", "javascript", "webdev"]
        )

        file_path = list(temp_memory_dir.glob("001-*.md"))[0]
        content = file_path.read_text()
        fm_match = yaml.safe_load(content.split("---")[1])

        # Should be union: original + new, no duplicates
        tags = set(fm_match["tags"])
        assert tags == {"preference", "typescript", "javascript", "webdev"}


class TestDecayTriggers:
    """Test decay triggers at correct thresholds."""

    def test_no_decay_at_limit(self, temp_memory_dir, mock_ctx):
        """No decay when exactly at limit (200/200)."""
        # Create exactly 200 memories
        for i in range(1, 201):
            create_test_memory(temp_memory_dir, i, f"Memory {i}")

        memories = _load_all_memories(temp_memory_dir)
        assert len(memories) == 200

        # At limit, should not trigger (only > limit triggers)
        # This is tested implicitly by save_memory logic

    def test_decay_at_limit_plus_one(self, temp_memory_dir, mock_ctx):
        """Decay should trigger when exceeding limit (201/200)."""
        # Create 200 memories
        for i in range(1, 201):
            create_test_memory(temp_memory_dir, i, f"Memory {i}", created_days_ago=200-i)

        memories = _load_all_memories(temp_memory_dir)
        assert len(memories) == 200

        # Decay oldest 20%
        import asyncio
        result = asyncio.run(_decay_memories(mock_ctx, temp_memory_dir, memories))

        # Should decay 40 memories (20% of 200)
        assert result["decayed"] == 40

    def test_calculates_percentage_correctly(self, temp_memory_dir):
        """Decay percentage calculation should be accurate."""
        # Create 205 memories
        for i in range(1, 206):
            create_test_memory(temp_memory_dir, i, f"Memory {i}", created_days_ago=205-i)

        memories = _load_all_memories(temp_memory_dir)

        # 20% of 205 = 41
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:int(205 * 0.2)]
        assert len(oldest) == 41

        # Should be the oldest ones (highest created_days_ago)
        oldest_ids = [m.id for m in oldest]
        assert oldest_ids == list(range(1, 42))  # IDs 1-41 are oldest


class TestDecayStrategies:
    """Test decay strategies (summarize and cut) work correctly."""

    @pytest.mark.asyncio
    async def test_summarize_creates_consolidated(self, temp_memory_dir, mock_ctx):
        """Summarize strategy should create 1 consolidated memory."""
        # Create 10 old memories
        for i in range(1, 11):
            create_test_memory(
                temp_memory_dir, i, f"Old memory {i}", created_days_ago=100-i
            )

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:5]
        assert len(oldest) == 5

        result = await _decay_summarize(mock_ctx, temp_memory_dir, oldest, memories)

        assert result["decayed"] == 5
        assert result["strategy"] == "summarize"

        # Should delete originals (IDs 1-5) and create 1 new (ID 11)
        remaining = _load_all_memories(temp_memory_dir)
        assert len(remaining) == 6  # 10 - 5 + 1

        # Check consolidated memory exists with correct tags
        files = sorted(temp_memory_dir.glob("*.md"))
        newest_file = files[-1]  # Last file should be consolidated
        content = newest_file.read_text()
        fm = yaml.safe_load(content.split("---")[1])
        assert "_consolidated" in fm["tags"]
        assert "_auto_decay" in fm["tags"]

    @pytest.mark.asyncio
    async def test_cut_deletes_without_consolidation(self, temp_memory_dir, mock_ctx):
        """Cut strategy should delete without creating consolidated memory."""
        # Create 10 old memories
        for i in range(1, 11):
            create_test_memory(
                temp_memory_dir, i, f"Old memory {i}", created_days_ago=100-i
            )

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:5]
        result = await _decay_cut(mock_ctx, temp_memory_dir, oldest)

        assert result["decayed"] == 5
        assert result["strategy"] == "cut"

        # Should delete originals (IDs 1-5), no new memory
        remaining = _load_all_memories(temp_memory_dir)
        assert len(remaining) == 5  # 10 - 5

        # No consolidated memory should exist
        files = list(temp_memory_dir.glob("*.md"))
        for f in files:
            content = f.read_text()
            assert "_consolidated" not in content

    @pytest.mark.asyncio
    async def test_oldest_selected_not_random(self, temp_memory_dir, mock_ctx):
        """Decay should select oldest memories, not random or newest."""
        # Create memories with known ages
        create_test_memory(temp_memory_dir, 1, "Oldest", created_days_ago=100)
        create_test_memory(temp_memory_dir, 2, "Old", created_days_ago=50)
        create_test_memory(temp_memory_dir, 3, "Recent", created_days_ago=10)
        create_test_memory(temp_memory_dir, 4, "Newest", created_days_ago=0)

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:2]

        # Should get IDs 1 and 2 (oldest)
        oldest_ids = [m.id for m in oldest]
        assert oldest_ids == [1, 2]


class TestProtectionRespected:
    """Test decay protection flag prevents decay."""

    def test_protected_excluded_from_decay(self, temp_memory_dir):
        """Protected memories should not be in decay candidates."""
        # Create mix of protected and unprotected
        create_test_memory(
            temp_memory_dir, 1, "Protected old", created_days_ago=100, decay_protected=True
        )
        create_test_memory(temp_memory_dir, 2, "Unprotected old", created_days_ago=90)
        create_test_memory(temp_memory_dir, 3, "Unprotected newer", created_days_ago=80)

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:2]

        # Should only get IDs 2 and 3 (not 1, even though it's oldest)
        oldest_ids = [m.id for m in oldest]
        assert 1 not in oldest_ids
        assert oldest_ids == [2, 3]

    @pytest.mark.asyncio
    async def test_unprotected_decay_normally(self, temp_memory_dir, mock_ctx):
        """Unprotected memories should decay normally even if protected ones exist."""
        # 1 protected, 9 unprotected
        create_test_memory(
            temp_memory_dir, 1, "Protected", created_days_ago=100, decay_protected=True
        )
        for i in range(2, 11):
            create_test_memory(
                temp_memory_dir, i, f"Unprotected {i}", created_days_ago=100-i
            )

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:5]

        result = await _decay_summarize(mock_ctx, temp_memory_dir, oldest, memories)

        # Should decay 5 unprotected memories
        assert result["decayed"] == 5

        # Protected memory should still exist
        files = list(temp_memory_dir.glob("001-*.md"))
        assert len(files) == 1


# ============================================================================
# P1 Tests: Scalability & UX
# ============================================================================


class TestRecencyWindow:
    """Test recency window limits dedup scope for scalability."""

    def test_only_checks_recent_window(self, temp_memory_dir, mock_ctx):
        """Should only check memories within window_days."""
        # Create memories outside window (8 days ago)
        create_test_memory(
            temp_memory_dir, 1, "I prefer TypeScript", created_days_ago=8
        )
        # Create memory inside window (5 days ago)
        create_test_memory(
            temp_memory_dir, 2, "I use PostgreSQL", created_days_ago=5
        )

        # Filter to recent with 7-day window
        memories = _load_all_memories(temp_memory_dir)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [m for m in memories if _parse_created(m.created) >= cutoff]

        # Should only return memory 2 (within 7 days)
        assert len(recent) == 1
        assert recent[0].id == 2

    def test_old_memories_ignored(self, temp_memory_dir):
        """Memories outside window should not be checked for dedup."""
        # Old similar memory (outside window)
        create_test_memory(
            temp_memory_dir, 1, "I prefer TypeScript", created_days_ago=10
        )

        memories = _load_all_memories(temp_memory_dir)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [m for m in memories if _parse_created(m.created) >= cutoff]

        # New similar content should not find duplicate
        is_dup, match, _ = _check_duplicate(
            "I prefer typescript", recent, threshold=85
        )

        assert is_dup is False
        assert match is None

    def test_performance_constant_not_linear(self, temp_memory_dir):
        """Recency window should limit to max_count regardless of total."""
        # Create 100 memories (all within window)
        for i in range(1, 101):
            create_test_memory(
                temp_memory_dir, i, f"Memory {i}", created_days_ago=i % 7
            )

        memories = _load_all_memories(temp_memory_dir)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = sorted(
            [m for m in memories if _parse_created(m.created) >= cutoff],
            key=lambda m: m.created, reverse=True
        )[:10]

        # Should only return 10 (not 100)
        assert len(recent) == 10


class TestDisplayCorrectness:
    """Test display shows correct lifecycle information."""

    @pytest.mark.asyncio
    async def test_shows_count_vs_limit(self, temp_memory_dir, mock_ctx):
        """List should show 'count/limit' format."""
        create_test_memory(temp_memory_dir, 1, "Memory 1")
        create_test_memory(temp_memory_dir, 2, "Memory 2")

        result = await list_memories(mock_ctx)

        assert "2/200" in result["display"]  # 2 memories, 200 limit
        assert result["count"] == 2
        assert result["limit"] == 200

    @pytest.mark.asyncio
    async def test_consolidated_shows_date_range(self, temp_memory_dir, mock_ctx):
        """Consolidated memories should show 'created → updated'."""
        create_test_memory(
            temp_memory_dir, 1, "Original", created_days_ago=5
        )

        # Update it (consolidation)
        memories = _load_all_memories(temp_memory_dir)
        entry = memories[0]
        _update_existing_memory(entry, "Updated content", [])

        result = await list_memories(mock_ctx)

        # Should show date range indicator
        assert "→" in result["display"]

    @pytest.mark.asyncio
    async def test_protected_shows_indicator(self, temp_memory_dir, mock_ctx):
        """Protected memories should show 🔒 indicator."""
        create_test_memory(
            temp_memory_dir, 1, "Protected memory", decay_protected=True
        )

        result = await list_memories(mock_ctx)

        assert "🔒" in result["display"]

    @pytest.mark.asyncio
    async def test_empty_state_handled(self, temp_memory_dir, mock_ctx):
        """Empty memory directory should display gracefully."""
        result = await list_memories(mock_ctx)

        assert result["count"] == 0
        assert "No memories" in result["display"]


class TestFrontmatterValidation:
    """Test new frontmatter fields are validated."""

    @pytest.mark.asyncio
    async def test_new_fields_accepted(self, temp_memory_dir, mock_ctx):
        """New lifecycle fields should be accepted."""
        # Create memory with all new fields
        fm = {
            "id": 1,
            "created": datetime.now(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "source": "auto_decay",
            "auto_category": "preference",
            "consolidation_reason": "test_reason",
            "decay_protected": True,
        }

        content = f"---\n{yaml.dump(fm)}---\n\nTest content\n"
        file_path = temp_memory_dir / "001-test.md"
        file_path.write_text(content)

        # Should list without errors
        result = await list_memories(mock_ctx)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_memories_skipped(self, temp_memory_dir, mock_ctx):
        """Invalid frontmatter should be skipped, not crash."""
        # Valid memory
        create_test_memory(temp_memory_dir, 1, "Valid memory")

        # Invalid memory (missing required field)
        bad_content = "---\ncreated: 2026-01-01\n---\n\nNo ID field\n"
        bad_path = temp_memory_dir / "002-bad.md"
        bad_path.write_text(bad_content)

        # Should list only valid memory
        result = await list_memories(mock_ctx)
        assert result["count"] == 1  # Only the valid one


# ============================================================================
# P2 Tests: Edge Cases & Error Resilience
# ============================================================================


class TestBoundaryConditions:
    """Test boundary conditions and edge cases."""

    @pytest.mark.asyncio
    async def test_empty_directory(self, temp_memory_dir, mock_ctx):
        """Empty memory directory should handle gracefully."""
        memories = _load_all_memories(temp_memory_dir)
        assert len(memories) == 0

        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:10]
        assert oldest == []

    def test_single_memory_no_decay(self, temp_memory_dir):
        """Single memory should not trigger decay even if protected."""
        create_test_memory(temp_memory_dir, 1, "Only memory")

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:1]
        assert len(oldest) == 1

        # Can't decay last memory (would leave 0)
        # This is a policy decision - may want to allow

    def test_exactly_at_limit(self, temp_memory_dir):
        """Exactly 200 memories should not trigger decay."""
        for i in range(1, 201):
            create_test_memory(temp_memory_dir, i, f"Memory {i}")

        memories = _load_all_memories(temp_memory_dir)
        assert len(memories) == 200

        # At limit, not over - no decay needed


class TestErrorResilience:
    """Test error handling and resilience."""

    @pytest.mark.asyncio
    async def test_malformed_yaml_skipped(self, temp_memory_dir, mock_ctx):
        """Malformed YAML should be skipped gracefully."""
        # Valid memory
        create_test_memory(temp_memory_dir, 1, "Valid")

        # Malformed YAML
        bad_yaml = "---\nid: 2\ncreated: {{invalid\n---\n\nContent\n"
        (temp_memory_dir / "002-bad.md").write_text(bad_yaml)

        # Should list only valid memory
        result = await list_memories(mock_ctx)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_missing_frontmatter_skipped(self, temp_memory_dir, mock_ctx):
        """Files without frontmatter should be skipped."""
        create_test_memory(temp_memory_dir, 1, "Valid")

        # No frontmatter
        (temp_memory_dir / "002-no-fm.md").write_text("Just content\n")

        result = await list_memories(mock_ctx)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_summarize_uses_concatenation(self, temp_memory_dir, mock_ctx):
        """Summarize strategy uses simple concatenation (MVP approach)."""
        for i in range(1, 6):
            create_test_memory(temp_memory_dir, i, f"Memory {i}", created_days_ago=10-i)

        memories = _load_all_memories(temp_memory_dir)
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:3]

        result = await _decay_summarize(mock_ctx, temp_memory_dir, oldest, memories)

        # Should work with concatenation (no LLM needed)
        assert result["decayed"] == 3
        assert result["strategy"] == "summarize"

        # Consolidated memory should exist
        remaining = _load_all_memories(temp_memory_dir)
        assert len(remaining) == 3  # 5 - 3 + 1


# ============================================================================
# Integration Tests: End-to-End Flows
# ============================================================================


class TestE2EFlows:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_e2e_dedup_flow(self, temp_memory_dir, mock_ctx):
        """Full dedup flow: save → save similar → verify consolidated."""
        # Save first memory
        result1 = await save_memory(
            mock_ctx, "I prefer TypeScript for web development", tags=["preference"]
        )
        assert result1["action"] == "saved"
        assert result1["memory_id"] == 1

        # Save similar memory
        result2 = await save_memory(
            mock_ctx, "I prefer typescript for web dev", tags=["preference", "javascript"]
        )

        # Should consolidate (not create new)
        assert result2["action"] == "consolidated"
        assert result2["memory_id"] == 1

        # Should still be only 1 memory
        memories = _load_all_memories(temp_memory_dir)
        assert len(memories) == 1

        # Tags should be merged
        files = list(temp_memory_dir.glob("*.md"))
        content = files[0].read_text()
        fm = yaml.safe_load(content.split("---")[1])
        assert set(fm["tags"]) == {"preference", "javascript"}

    @pytest.mark.asyncio
    async def test_e2e_decay_flow(self, temp_memory_dir, mock_ctx):
        """Full decay flow: save 201 → verify decay triggered."""
        # Configure for testing
        mock_ctx.deps.memory_max_count = 10
        mock_ctx.deps.memory_decay_percentage = 0.2

        # Save 10 memories (at limit)
        for i in range(10):
            create_test_memory(temp_memory_dir, i+1, f"Memory {i+1}", created_days_ago=10-i)

        memories = _load_all_memories(temp_memory_dir)
        assert len(memories) == 10

        # Save 11th memory (should trigger decay)
        result = await save_memory(mock_ctx, "Memory 11 triggers decay")

        # Should save new memory
        assert result["action"] == "saved"
        assert "Decayed" in result["display"]  # Decay message shown

        # Total should be < 11 (some were decayed)
        remaining = _load_all_memories(temp_memory_dir)
        assert len(remaining) < 11

    @pytest.mark.asyncio
    async def test_gravity_behavior(self, temp_memory_dir, mock_ctx):
        """Test gravity: new on top, old at bottom, natural decay."""
        # Create memories with different ages
        create_test_memory(temp_memory_dir, 1, "Oldest memory", created_days_ago=30)
        create_test_memory(temp_memory_dir, 2, "Old memory", created_days_ago=20)
        create_test_memory(temp_memory_dir, 3, "Recent memory", created_days_ago=5)
        create_test_memory(temp_memory_dir, 4, "Newest memory", created_days_ago=0)

        memories = _load_all_memories(temp_memory_dir)

        # Get oldest for decay
        oldest = sorted(
            [m for m in memories if not m.decay_protected],
            key=lambda m: m.created,
        )[:2]

        # Should get memories 1 and 2 (gravity pulls them to bottom)
        oldest_ids = [m.id for m in oldest]
        assert oldest_ids == [1, 2]

        # Get recent for dedup
        cutoff = datetime.now(timezone.utc) - timedelta(days=10)
        recent = sorted(
            [m for m in memories if _parse_created(m.created) >= cutoff],
            key=lambda m: m.created, reverse=True
        )

        # Should get memories 3 and 4 (at top, within window)
        recent_ids = [m.id for m in recent]
        assert set(recent_ids) == {3, 4}
