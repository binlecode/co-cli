# Plan: Fix Prompt Assembly Order

**Task type: refactor**
**Slug:** fix-prompt-assembly-order

---

## Context

Deep source-code scan comparing co-cli and hermes-agent system prompt assembly revealed two ordering bugs in co's Block 0 assembly. Both bugs exist in the live `main` branch.

No prior plan found for this slug. No related DESIGN or TODO docs.

**Current-state validation:** Source verified against code (assembly.py:86-167, core.py:118-164). No phantom features or stale names found. No workflow hygiene issues.

---

## Problem & Outcome

**Problem:** Block 0 of co's system prompt assembles two sections in the wrong positions:

1. **Personality memories injected before behavioral rules** (`assembly.py:137-142`). Personality memories (T2 knowledge artifacts tagged `personality-context`) are the most cache-volatile element in Block 0 — they change between sessions when the user adds or modifies artifacts. Placing them at position 3b (between mindsets and behavioral rules) means every session where artifacts change must re-cache rules, recency advisory, examples, critique, toolset guidance, and category awareness — all stable sections that should stay warm.

2. **Critique is not last in Block 0** (`assembly.py:158-160` vs `core.py:131-137`). `build_static_instructions` guarantees critique is "always last" within its output. But `core.py` appends `toolset_guidance` and `category_hint` after it. Critique ends up in the middle of Block 0, with operational guidance following it. The review/self-assessment lens must wrap the complete prompt, not precede operational sections.

**Failure cost:** The model reads toolset guidance and category awareness after its self-assessment lens — any behavioral calibration from the critique has no visibility into those sections. Personality memories bloat the cache invalidation footprint with every artifact write.

**Outcome:** Block 0 assembles in this order (from top to tail):

```
soul seed → char memories → mindsets → rules → recency advisory → examples
→ toolset guidance (conditional)
→ category awareness (conditional)
→ personality memories (conditional, volatile suffix)
→ critique (always last behavioral lens)
```

Personality memories are the narrowest volatile slice at the tail — only they re-cache on artifact changes. Critique wraps everything including operational guidance.

---

## Scope

- Refactor `co_cli/context/assembly.py` and `co_cli/agent/core.py` to fix the assembly order.
- Update `tests/prompts/test_static_instructions.py` to cover the new ordering contract.
- No behavior change for sessions without personality configured (personality memories and critique are conditional).
- No public API change: `build_agent()` signature unchanged. `build_static_instructions` loses its `knowledge_dir` parameter (was only used to test personality memory injection, which moves out of this function).

**Out of scope:**
- Block 1 ordering (already correct: safety_prompt → current_time_prompt).
- Adding new prompt sections.
- Changes to any personality asset files.

---

## Behavioral Constraints

- `build_static_instructions` must never include personality memories or critique after this change.
- `core.py` Block 0 must produce the section order: stable_core → toolset_guidance → category_hint → personality_memories → critique. Deviation from this order is a test failure.
- Critique must appear after toolset_guidance in all assembled Block 0 output when a personality is configured. If no personality is configured, critique is absent and this constraint is vacuous.
- Personality memories must appear after category_hint and before critique when both are present.
- Sessions with no personality configured must produce identical output to the current behavior (only stable_core + toolset_guidance + category_hint, no personality memories, no critique).
- The `_personality_cache` module-level cache in `loader.py` must remain intact — no changes to cache invalidation semantics.

---

## High-Level Design

`build_static_instructions` currently owns too much: it assembles both stable-forever sections (seed, rules, examples) and volatile/conditional sections (personality memories, critique). The fix is to narrow its responsibility to stable sections only, and promote personality memories and critique to the `core.py` call site where they can be appended in the correct relative position.

**Two structural changes:**

1. **`assembly.py`**: Remove personality_memories and critique from `build_static_instructions`. Remove the `knowledge_dir` parameter (it was only needed for personality_memories injection and is now dead). Update the docstring section list.

2. **`core.py`**: After assembling `[stable_instructions, toolset_guidance, category_hint]`, load and append personality_memories (via `load_personality_memories`) and critique (via `load_soul_critique`) when a personality is configured. These are already public functions in `loader.py` — no new functions needed.

No new abstractions. No new modules. Callers of `build_static_instructions` outside tests only exist in `core.py` — one call site update.

---

## Implementation Plan

### TASK-1: Narrow `build_static_instructions` to stable sections only

**files:**
- `co_cli/context/assembly.py`

Remove the `# 3b. Personality memories` block (lines 137-142) and the `# 6. Critique` block (lines 154-160) from `build_static_instructions`. Remove `load_personality_memories` and `load_soul_critique` from the conditional import. Remove the `knowledge_dir` parameter from the function signature and its use. Update the docstring section list to match (remove 3b and 6).

**done_when:** `grep -n "load_personality_memories\|load_soul_critique\|knowledge_dir" co_cli/context/assembly.py` returns no matches. `uv run pytest tests/prompts/ -x` passes (tests updated in TASK-3 but the function's own assertions should still hold without personality_memories and critique).

**success_signal:** N/A (internal refactor, no user-visible change)

---

### TASK-2: Reassemble Block 0 in correct order in `core.py`

**files:**
- `co_cli/agent/core.py`
- `co_cli/personality/prompts/loader.py` (docstring update: `load_personality_memories` caller is now `build_agent` / `core.py`, not `build_static_instructions`)

**prerequisites:** [TASK-1]

After the existing three-part assembly (`stable_instructions`, `toolset_guidance`, `category_hint`), add:

```python
if config.personality:
    from co_cli.personality.prompts.loader import load_personality_memories, load_soul_critique
    pm = load_personality_memories()
    if pm:
        static_parts.append(pm)
    crit = load_soul_critique(config.personality)
    if crit:
        static_parts.append(f"## Review lens\n\n{crit}")
```

This replaces the critique formatting that was previously inline in `assembly.py:159-160` (`f"## Review lens\n\n{critique}"`).

**done_when:** `uv run pytest tests/prompts/ -x` passes with the new ordering tests from TASK-3.

**success_signal:** N/A (internal refactor, no user-visible change)

---

### TASK-3: Update and extend tests for ordering contract

**files:**
- `tests/prompts/test_static_instructions.py`

**prerequisites:** [TASK-2]

Three test changes:

1. **Update `test_static_instructions_includes_personality_memories`**: The function `build_static_instructions` no longer injects personality memories. Change the test to assert the sentinel is **absent** from `build_static_instructions` output (confirming extraction). Keep it as a negative guard.

2. **Add `test_block0_personality_memories_position`**: Build Block 0 by replicating the `core.py` static_parts assembly inline (keep in sync with `core.py` — update this test if Block 0 wiring changes). Pass `knowledge_dir=tmp_path` explicitly to `load_personality_memories` to bypass the process-scoped `_personality_cache` global (the kwarg bypasses the cache per `loader.py:52`). Include a sentinel artifact in `tmp_path` and `memory_search` in tool_index. Assert:
   ```python
   assert combined.index(MEMORY_GUIDANCE_SENTINEL) < combined.index("personality-static-sentinel-XYZ789")
   assert combined.index("personality-static-sentinel-XYZ789") < combined.index("## Review lens")
   ```
   where `MEMORY_GUIDANCE_SENTINEL = "at most one broader retry"` (unique string from `MEMORY_GUIDANCE`).

3. **Add `test_block0_critique_is_last`**: Build Block 0 with a personality that has a critique. Assert `combined.endswith("## Review lens\n\n" + critique_text)` or that no content follows the `## Review lens` section.

The temp-knowledge-dir setup can be shared with a fixture. Use `CO_HOME` monkeypatching pattern consistent with existing tests, or pass `knowledge_dir` directly to `load_personality_memories` — check which pattern existing tests use and follow it.

**done_when:** `uv run pytest tests/prompts/test_static_instructions.py -x -v` passes with all three new/updated tests green. Output includes `PASSED test_block0_personality_memories_position` and `PASSED test_block0_critique_is_last`.

**success_signal:** N/A (refactor test coverage, no user-visible change)

---

## Testing

All changes are pure refactors — behavior is identical for sessions without personality. The test suite in `tests/prompts/` is the verification surface. Full regression: `uv run pytest tests/ -x`.

No evals needed (no model-output or behavioral change; this is prompt structure only).

---

## Open Questions

None. All questions answerable by inspection:
- Where is `load_soul_critique` called? Only inside `build_static_instructions` (confirmed by grep).
- Is `knowledge_dir` used by any caller other than tests? No — `core.py` always calls `build_static_instructions(config)` without it.
- Does `load_personality_memories()` with no `knowledge_dir` arg use `KNOWLEDGE_DIR` from config? Yes (`loader.py:55`).

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev fix-prompt-assembly-order`
