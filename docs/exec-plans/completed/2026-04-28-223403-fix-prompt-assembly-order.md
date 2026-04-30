# Plan: Fix Prompt Assembly Order

**Task type: refactor**
**Slug:** fix-prompt-assembly-order

---

## Context

Deep source-code scan comparing co-cli and hermes-agent system prompt assembly revealed two ordering bugs in co's Block 0 assembly. Both bugs exist in the live `main` branch.

No prior plan found for this slug. No related DESIGN or TODO docs.

**Current-state validation:** Source verified against code (assembly.py:86-167, core.py:118-164). No phantom features or stale names found. No workflow hygiene issues.

**Design update (post-plan):** `personality-context` tag and the associated `load_personality_memories` / `_personality_cache` mechanism are removed. The static prompt carries only the soul scaffold (seed, mindsets, examples, critique). All personality memory is reached via the canon channel (`memory_search`). This collapses Bug 1 from "move personality memories to tail" to "remove entirely". Terminology: tier framing is retired — the architecture uses static personality content + three recall channels (artifacts, sessions, canon).

---

## Problem & Outcome

**Problem:** Block 0 of co's system prompt assembles two sections in the wrong positions:

1. **Personality memories injected before behavioral rules** (`assembly.py:137-142`). The `personality-context` tag mechanism is now removed entirely — personality memory is served by the canon channel via `memory_search`, not injected into the static prompt. The 3b block and all related imports are dead.

2. **Critique is not last in Block 0** (`assembly.py:158-160` vs `core.py:131-137`). `build_static_instructions` guarantees critique is "always last" within its output. But `core.py` appends `toolset_guidance` and `category_hint` after it. Critique ends up in the middle of Block 0, with operational guidance following it. The review/self-assessment lens must wrap the complete prompt, not precede operational sections.

**Failure cost:** The model reads toolset guidance and category awareness after its self-assessment lens — any behavioral calibration from the critique has no visibility into those sections.

**Outcome:** Block 0 assembles in this order (from top to tail):

```
soul seed → mindsets → rules → recency advisory
→ toolset guidance (conditional)
→ category awareness (conditional)
→ critique (always last behavioral lens, conditional on personality)
```

Note: character memories are absent because the character-memory-to-search-channel plan runs first and removes the static `## Character` block. Examples were already removed in commit `95fd0c1`.

Static prompt carries only the stable soul scaffold. Critique wraps everything including operational guidance.

---

## Scope

- Refactor `co_cli/context/assembly.py` and `co_cli/agent/core.py` to fix the assembly order.
- Update `tests/prompts/test_static_instructions.py` to cover the new ordering contract.
- Remove `personality-context` injection entirely: `load_personality_memories`, `_personality_cache`, and the `knowledge_dir` parameter are dead code after this change.
- No public API change: `build_agent()` signature unchanged.

**Out of scope:**
- Block 1 ordering (already correct: safety_prompt → current_time_prompt).
- Adding new prompt sections.
- Changes to any personality asset files.
- Pruning dead loader functions from `loader.py`: `load_personality_memories` / `_personality_cache` (dead after this plan) and `load_character_memories` (dead after character-memory plan). Both should be pruned together in a single `/sync-doc` pass after this plan ships. Also update the `loader.py` module docstring and Callers section, which will be stale after both plans run.

---

## Behavioral Constraints

- `build_static_instructions` must never include personality memories or critique after this change.
- `core.py` Block 0 must produce the section order: stable_core → toolset_guidance → category_hint → critique. Deviation from this order is a test failure.
- Critique must appear after toolset_guidance in all assembled Block 0 output when a personality is configured. If no personality is configured, critique is absent and this constraint is vacuous.
- Sessions with no personality configured must produce identical output to the current behavior (only stable_core + toolset_guidance + category_hint, no critique).

---

## High-Level Design

`build_static_instructions` currently owns too much: it assembles both stable-forever sections (seed, rules) and the conditional critique. The fix is to narrow its responsibility to stable sections only, and promote critique to the `core.py` call site where it can be appended after operational guidance.

Personality memories (the `personality-context` tag injection) are removed entirely — not moved, deleted.

**Two structural changes:**

1. **`assembly.py`**: Remove personality_memories (3b block) and critique (section 5 — currently labelled `# 5. Critique` in code; was section 6 in the original plan numbering which counted examples as section 5, but examples were removed in `95fd0c1`) from `build_static_instructions`. Remove `load_personality_memories`, `load_soul_critique`, and the `knowledge_dir` parameter — all dead. Update the function docstring to remove section 5 (critique); section 2 (character memories) is already absent after character-memory plan runs first. Update the module-level docstring (`assembly.py:3–4`) to remove stale references to 'character memories', 'personality-context knowledge artifacts', and 'examples'.

2. **`core.py`**: After assembling `[stable_instructions, toolset_guidance, category_hint]`, append critique (via `load_soul_critique`) when a personality is configured. One import, four lines.

No new abstractions. No new modules. Callers of `build_static_instructions` outside tests only exist in `core.py` — one call site update.

---

## Implementation Plan

### ✓ DONE — TASK-1: Narrow `build_static_instructions` to stable sections only

**files:**
- `co_cli/context/assembly.py`

Remove the `# 3b. Personality memories` inline block and the `# 5. Critique` block from `build_static_instructions`. Remove `load_personality_memories`, `load_soul_critique`, and the `knowledge_dir` parameter from the function signature and its use. (By the time this plan runs, the character-memory plan has already removed `load_character_memories` from the imports.) Update the function docstring: remove section 5 (critique); section 2 (character memories) is already absent. Update the module-level docstring (`assembly.py:3–4`) to remove stale references to 'character memories', 'personality-context knowledge artifacts', and 'examples'.

**done_when:** `grep -n "load_personality_memories\|load_soul_critique\|knowledge_dir" co_cli/context/assembly.py` returns no matches. `uv run pytest tests/prompts/ -x` passes.

**success_signal:** N/A (internal refactor, no user-visible change)

---

### ✓ DONE — TASK-2: Reassemble Block 0 in correct order in `core.py`

**files:**
- `co_cli/agent/core.py`

**prerequisites:** [TASK-1]

After the existing three-part assembly (`stable_instructions`, `toolset_guidance`, `category_hint`), add only critique — no personality memories:

```python
if config.personality:
    from co_cli.personality.prompts.loader import load_soul_critique
    crit = load_soul_critique(config.personality)
    if crit:
        static_parts.append(f"## Review lens\n\n{crit}")
```

This replaces the critique formatting that was previously inline in `assembly.py:158-160` (`f"## Review lens\n\n{critique}"`). No `load_personality_memories` call — that mechanism is removed.

**done_when:** `uv run pytest tests/prompts/ -x` passes with the new ordering tests from TASK-3.

**success_signal:** N/A (internal refactor, no user-visible change)

---

### ✓ DONE — TASK-3: Update and extend tests for ordering contract

**files:**
- `tests/prompts/test_static_instructions.py`

**prerequisites:** [TASK-2]

Two test changes:

1. **Delete `test_static_instructions_includes_personality_memories`**: Personality memory injection is removed entirely; this test has no valid post-change assertion. Delete it rather than convert to a negative guard — a negative guard for a removed feature is noise.

2. **Add `test_block0_critique_is_last`**: Build Block 0 by replicating the `core.py` static_parts assembly inline (with `memory_search` in tool_index for MEMORY_GUIDANCE, and a personality that has a critique). Assert `combined.endswith("## Review lens\n\n" + critique_text)` — no content follows the `## Review lens` section.

**done_when:** `uv run pytest tests/prompts/test_static_instructions.py -x -v` passes. Output includes `PASSED test_block0_critique_is_last`.

**success_signal:** N/A (refactor test coverage, no user-visible change)

---

## Testing

All changes are pure refactors — behavior is identical for sessions without personality. The test suite in `tests/prompts/` is the verification surface. Full regression: `uv run pytest tests/ -x`.

No evals needed (no model-output or behavioral change; this is prompt structure only).

---

## Open Questions

None. All questions answerable by inspection:
- Where is `load_soul_critique` called? Only inside `build_static_instructions` — moves to `core.py`.
- Is `knowledge_dir` used by any caller other than tests? No — `core.py` always calls `build_static_instructions(config)` without it; safe to remove.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev fix-prompt-assembly-order`

---

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep for `load_personality_memories\|load_soul_critique\|knowledge_dir` in assembly.py returns no matches; `tests/prompts/ -x` passes | ✓ pass |
| TASK-2 | `uv run pytest tests/prompts/ -x` passes with new ordering tests | ✓ pass |
| TASK-3 | `uv run pytest tests/prompts/test_static_instructions.py -x -v` passes; output includes `PASSED test_block0_critique_is_last` | ✓ pass |

**Tests:** scoped (`tests/prompts/`, `tests/approvals/`) — 40 passed, 0 failed
**Doc Sync:** fixed — `prompt-assembly.md`, `memory.md`, `personality.md` updated; `loader.py` docstring corrected; pre-existing lint/format issues in coworker files also fixed

**Overall: DELIVERED**
All three tasks passed. Block 0 now assembles in correct order: soul scaffold → toolset guidance → category hint → critique. `build_static_instructions` is narrowed to stable-forever content only.
