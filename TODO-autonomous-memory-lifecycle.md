# TODO: Autonomous Memory Lifecycle Management

**Vision:** "co is ai, not crud api" — Memory autonomously managed through cognitive reasoning

**Estimated Time:** 8-11 hours

---

## Phase 1: Foundation (2-3 hours)

### 1.1 Frontmatter Validation Updates

**File:** `co_cli/_frontmatter.py`

- [ ] Add `updated` field validation to `validate_memory_frontmatter()`
  - [ ] Add to optional fields section (around line 140)
  - [ ] Validate type: `str` (ISO8601 timestamp)
  - [ ] Validate format: Same regex as `created` field
  - [ ] Test: `updated: None` is valid (optional field)
  - [ ] Test: `updated: "2026-02-10T15:30:00Z"` is valid
  - [ ] Test: `updated: "invalid"` raises ValueError

### 1.2 CoDeps Model Access

**File:** `co_cli/deps.py`

- [ ] Add `model` field to `CoDeps` dataclass
  - [ ] Type: `Any` (pydantic-ai Model)
  - [ ] Default: `None`
  - [ ] Import: `from typing import Any` (if not already imported)
  - [ ] Documentation comment: `# LLM model for tools that need LLM access (contradiction detection)`

**File:** `co_cli/agent.py`

- [ ] Pass agent's model to CoDeps
  - [ ] Find where `CoDeps` is instantiated (likely in `get_agent()` or main.py)
  - [ ] Add: `deps.model = agent.model`
  - [ ] Verify model is accessible in tools via `ctx.deps.model`

### 1.3 LLM-Based Contradiction Detection

**File:** `co_cli/tools/memory.py`

- [ ] Add `_detect_contradiction()` helper function
  - [ ] Signature: `async def _detect_contradiction(ctx: RunContext[CoDeps], new_content: str, new_tags: list[str], existing_memories: list[dict]) -> tuple[bool, int | None, str | None]`
  - [ ] Implementation steps:
    - [ ] Extract category from new_tags using `_detect_category()`
    - [ ] If no category, return `(False, None, None)` (skip detection)
    - [ ] Filter existing_memories by same category
    - [ ] If no candidates, return `(False, None, None)`
    - [ ] Construct LLM prompt with:
      - [ ] New memory content + tags
      - [ ] Existing candidate memories (id, content, tags)
      - [ ] Question: "Does new memory contradict any existing?"
      - [ ] Definition of contradiction with examples
      - [ ] Requested JSON response format
    - [ ] Call LLM using `ctx.deps.model.request()` or similar
    - [ ] Parse JSON response
    - [ ] Extract: contradicts (bool), memory_id (int), reasoning (str)
    - [ ] If contradicts, find old_content from candidates
    - [ ] Return: `(contradicts, memory_id, old_content)`
  - [ ] Error handling: If LLM call fails, return `(False, None, None)` (fallback to append-only)
  - [ ] Logging: Log LLM reasoning for debugging

### 1.4 Modify save_memory() for Contradiction Detection

**File:** `co_cli/tools/memory.py`

- [ ] Update `save_memory()` function
  - [ ] Before creating new memory, add contradiction detection:
    - [ ] Call `recall_memory()` to get existing memories in same domain
    - [ ] Extract query from content/tags (use first tag or content keywords)
    - [ ] Call `_detect_contradiction(ctx, content, tags, existing["results"])`
    - [ ] Store result: `(contradicts, conflicting_id, old_content)`
  - [ ] Branch on contradiction:
    - [ ] **If contradicts:** Return replacement proposal dict:
      ```python
      {
          "display": f"🔄 Memory {conflicting_id} says '{old_content}'. Replace with '{content}'?\n\n[Diff]\n-{old_content}\n+{content}\n\n(Will update after approval)",
          "action": "replace",
          "memory_id": conflicting_id,
          "old_content": old_content,
          "new_content": content,
          "tags": tags,
      }
      ```
    - [ ] **If not contradicts:** Continue with existing save logic
  - [ ] Handle approval result:
    - [ ] If action="replace" and approved:
      - [ ] Load existing memory file
      - [ ] Update content
      - [ ] Update frontmatter: add `updated` timestamp
      - [ ] Keep same `id` and `created` timestamp
      - [ ] Write back to same file
      - [ ] Return updated display message
  - [ ] Add logging for lifecycle events

### 1.5 Add Updated Timestamp to New Memories

**File:** `co_cli/tools/memory.py`

- [ ] Modify frontmatter in `save_memory()`
  - [ ] For new memories: `updated` field not present initially
  - [ ] For replaced memories: Add `updated: datetime.now(timezone.utc).isoformat()`
  - [ ] Ensure backward compatibility (old memories without `updated` work)

---

## Phase 2: Prompt Engineering (2 hours)

### 2.1 System Prompt Updates

**File:** `co_cli/prompts/system.md`

- [ ] Add new section: "Memory Lifecycle — Autonomous Contradiction Detection"
  - [ ] Location: After existing "Memory & Knowledge Management" section (around line 485)
  - [ ] Content to add:
    - [ ] Section header
    - [ ] Process flow (6 steps)
    - [ ] Decision criteria table (4 scenarios)
    - [ ] Example interaction (preference replacement)
    - [ ] Rejection handling guidance
    - [ ] Learning principle statement
  - [ ] Format: Follow existing section structure (headers, tables, code blocks)

### 2.2 Tool Docstring Updates

**File:** `co_cli/tools/memory.py`

- [ ] Update `save_memory()` docstring
  - [ ] Add section: "Contradiction Detection"
  - [ ] Explain: Tool automatically checks for contradictions
  - [ ] Explain: Returns replacement proposal if found
  - [ ] Explain: Agent presents proposal to user
  - [ ] Add example: Preference replacement flow
  - [ ] Update returns section: Document both return formats (save vs replace)

---

## Phase 3: Display Enhancements (1 hour)

### 3.1 List Memories Revision Indicators

**File:** `co_cli/tools/memory.py`

- [ ] Update `list_memories()` function
  - [ ] Modify `_format_memory_list()` or equivalent
  - [ ] Check for `updated` field in frontmatter
  - [ ] If `updated` present:
    - [ ] Format: `(created → updated)` with dates only (not times)
    - [ ] Example: `**005** (2026-02-10 → 2026-02-10 15:30)`
  - [ ] If `updated` not present:
    - [ ] Keep existing format: `**005** (2026-02-10)`
  - [ ] Ensure alignment and readability

---

## Phase 4: Testing (2-3 hours)

### 4.1 Unit Tests for Contradiction Detection

**File:** `tests/test_autonomous_lifecycle.py` (NEW FILE)

- [ ] Create new test file with imports
- [ ] Add fixtures:
  - [ ] `temp_project_dir` (reuse from test_memory_tools.py)
  - [ ] `mock_ctx_with_model` (mock with model field)
  - [ ] `deps` fixture with model access
- [ ] Test `_detect_contradiction()`:
  - [ ] `test_detect_contradiction_same_domain()`
    - [ ] Input: "Prefer TypeScript", existing: ["Prefer JavaScript"]
    - [ ] Expected: `(True, 1, "Prefer JavaScript")`
  - [ ] `test_no_contradiction_different_domain()`
    - [ ] Input: "Prefer TypeScript", existing: ["Uses pytest"]
    - [ ] Expected: `(False, None, None)`
  - [ ] `test_context_specific_no_conflict()`
    - [ ] Input: "Prefer TypeScript for frontend", existing: ["Prefer Python for backend"]
    - [ ] Expected: `(False, None, None)`
  - [ ] `test_refinement_no_conflict()`
    - [ ] Input: "Uses pytest-asyncio", existing: ["Uses pytest"]
    - [ ] Expected: `(False, None, None)`
  - [ ] `test_correction_signal_contradiction()`
    - [ ] Input: "Actually, we use GraphQL", existing: ["Uses REST API"]
    - [ ] Expected: `(True, 1, "Uses REST API")`

### 4.2 Integration Tests for Replacement Flow

**File:** `tests/test_autonomous_lifecycle.py`

- [ ] Test `save_memory()` with contradictions:
  - [ ] `test_save_returns_replacement_proposal()`
    - [ ] Save: "Prefer JavaScript"
    - [ ] Save: "Prefer TypeScript"
    - [ ] Assert: Second save returns `action="replace"` dict
    - [ ] Assert: Includes memory_id, old_content, new_content
  - [ ] `test_approval_executes_replacement()`
    - [ ] Save memory 1
    - [ ] Save contradictory memory (gets proposal)
    - [ ] Approve replacement
    - [ ] Assert: Memory 1 updated (not memory 2 created)
    - [ ] Assert: `updated` timestamp added to frontmatter
    - [ ] Assert: Content replaced
  - [ ] `test_rejection_creates_new_memory()`
    - [ ] Save memory 1
    - [ ] Save contradictory memory (gets proposal)
    - [ ] Reject replacement
    - [ ] Assert: Memory 2 created (both coexist)
    - [ ] Assert: Memory 1 unchanged
  - [ ] `test_updated_timestamp_added()`
    - [ ] Save memory 1
    - [ ] Replace with approved update
    - [ ] Read frontmatter
    - [ ] Assert: `updated` field present and > `created`

### 4.3 Update Existing Tests

**File:** `tests/test_memory_tools.py`

- [ ] Update tests for new return format:
  - [ ] `test_save_memory()` — May need to handle proposal return
  - [ ] Add: `test_save_memory_with_updated_field()`
    - [ ] Save memory
    - [ ] Check frontmatter has no `updated` initially
    - [ ] Replace memory
    - [ ] Check frontmatter has `updated` after replacement

**File:** `tests/test_agent.py`

- [ ] Verify no changes needed (tool registration should work automatically)
- [ ] Run to confirm no regressions

### 4.4 Manual Testing

- [ ] **Scenario 1: Simple Preference Replacement**
  - [ ] Start: `uv run co chat`
  - [ ] Input: "I prefer JavaScript for scripting"
  - [ ] Verify: Memory 1 saved
  - [ ] Input: "I prefer TypeScript"
  - [ ] Verify: Agent detects contradiction
  - [ ] Verify: Approval prompt shows diff
  - [ ] Approve: Press 'y'
  - [ ] Verify: Memory 1 updated (not new memory 2)
  - [ ] Input: `/list_memories`
  - [ ] Verify: Shows updated timestamp indicator

- [ ] **Scenario 2: Context-Specific No Conflict**
  - [ ] Input: "I prefer Python for backend"
  - [ ] Verify: Memory 1 saved
  - [ ] Input: "I prefer TypeScript for frontend"
  - [ ] Verify: No contradiction detected
  - [ ] Verify: Memory 2 created (both coexist)

- [ ] **Scenario 3: Rejection Handling**
  - [ ] Input: "I prefer Jest for testing"
  - [ ] Verify: Memory 1 saved
  - [ ] Input: "I prefer pytest for testing"
  - [ ] Verify: Contradiction detected
  - [ ] Verify: Approval prompt appears
  - [ ] Reject: Press 'n'
  - [ ] Verify: Memory 2 created (both coexist)
  - [ ] Verify: Agent acknowledges rejection

- [ ] **Scenario 4: Correction Signal**
  - [ ] Input: "We use REST API"
  - [ ] Verify: Memory 1 saved
  - [ ] Input: "Actually, we migrated to GraphQL last month"
  - [ ] Verify: Correction signal detected
  - [ ] Verify: Contradiction detected
  - [ ] Verify: Replacement proposal shown

---

## Verification Checklist

### Automated Tests

- [ ] Run: `uv run pytest tests/test_autonomous_lifecycle.py -v`
  - [ ] All unit tests pass
  - [ ] All integration tests pass
- [ ] Run: `uv run pytest tests/test_memory_tools.py -v`
  - [ ] No regressions
  - [ ] New tests pass
- [ ] Run: `uv run pytest tests/test_agent.py -v`
  - [ ] Tool registration works
- [ ] Run: `uv run pytest -v` (full suite)
  - [ ] No failures in any tests

### Manual Tests

- [ ] All 4 manual testing scenarios pass
- [ ] Edge cases handled:
  - [ ] LLM contradiction detection fails → Falls back to append-only
  - [ ] Malformed memory files → Skipped gracefully
  - [ ] Empty existing memories → No contradiction detected
  - [ ] Multiple candidates → LLM picks most relevant

### Success Criteria

**Functional:**
- [ ] Agent detects contradictions autonomously
- [ ] LLM semantic similarity works correctly
- [ ] Context-specific preferences don't conflict
- [ ] Approval shows clear diff (old vs new)
- [ ] Replacement updates in place (preserves ID)
- [ ] Updated timestamp tracked
- [ ] Rejection creates new memory (both coexist)
- [ ] list_memories shows revision indicators

**Quality:**
- [ ] All unit tests pass (100%)
- [ ] All integration tests pass (100%)
- [ ] Manual testing scenarios work
- [ ] No regression in existing memory features
- [ ] Error handling for edge cases

**User Experience:**
- [ ] Transparent: User sees what's changing
- [ ] Safe: Approval required for replacements
- [ ] Intelligent: LLM handles edge cases naturally
- [ ] Autonomous: No manual commands needed

---

## Implementation Notes

### Key Design Decisions

1. **LLM-based detection** (not rule-based)
   - Handles synonyms, context, nuance
   - Evolves with better models

2. **Proposal-then-execute** pattern
   - Tool returns proposal, agent presents
   - Approval gate controls execution

3. **Update in place** (preserve memory_id)
   - Simpler mental model
   - Supports future revision history

4. **Graceful degradation**
   - If LLM fails → append-only mode
   - If approval rejected → both memories coexist

### Critical Files

- `co_cli/tools/memory.py` — Core logic (400+ lines affected)
- `co_cli/prompts/system.md` — Agent reasoning guidance
- `co_cli/_frontmatter.py` — Validation (10 lines)
- `co_cli/deps.py` — Model access (5 lines)
- `co_cli/agent.py` — Model injection (5 lines)
- `tests/test_autonomous_lifecycle.py` — New test file (200+ lines)

### Estimated Timeline

- **Day 1 (4-5 hours):** Phase 1 (Foundation)
- **Day 2 (2-3 hours):** Phase 2 (Prompts) + Phase 3 (Display)
- **Day 3 (2-3 hours):** Phase 4 (Testing) + Verification

**Total: 8-11 hours over 2-3 days**

---

## Deferred to Phase 2 (Future Enhancements)

- [ ] Consolidation: Merge related memories
- [ ] Confidence scoring: Track certainty levels
- [ ] Temporal deprecation: Auto-expire stale memories
- [ ] Proactive forgetting: Suggest cleanup
- [ ] Memory versioning: Track full revision history
- [ ] Soft delete: Archive instead of delete

---

## Completion Checklist

- [ ] All tasks in Phase 1-4 completed
- [ ] All verification checkboxes checked
- [ ] All tests passing (automated + manual)
- [ ] Success criteria met (functional + quality + UX)
- [ ] Documentation updated (if needed)
- [ ] Plan file archived or updated with completion status

**Status:** Not Started
**Started:** ___________
**Completed:** ___________
