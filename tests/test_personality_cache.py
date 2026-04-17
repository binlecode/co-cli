"""Tests for _load_personality_memories cache in personalities/_injector.py."""

import co_cli.prompts.personalities._injector as _injector_module
from co_cli.prompts.personalities._injector import (
    _load_personality_memories,
    invalidate_personality_cache,
)


def test_personality_memories_caches_result_after_first_call() -> None:
    """Second call must return the identical cached object — no second filesystem scan.

    Verifies the three-step cache lifecycle:
    1. First call sets _personality_cache to a non-None value.
    2. Second call returns the exact same object (identity, not just equality).
    3. invalidate_personality_cache() resets the cache to None.
    """
    invalidate_personality_cache()
    try:
        result1 = _load_personality_memories()
        assert _injector_module._personality_cache is not None
        result2 = _load_personality_memories()
        assert result2 is result1
        invalidate_personality_cache()
        assert _injector_module._personality_cache is None
    finally:
        invalidate_personality_cache()
