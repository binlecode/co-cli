# TODO: Memory Dedup Rewrite — Fork-CC Model

Task type: `code-feature`

## Context

Current dedup uses a 2-tier approach: fuzzy token-sort matching (rapidfuzz, `_lifecycle.py:140-186`) as fast-path, then LLM consolidator agent (`_consolidator.py`) as fallback. Peer analysis (fork-cc, mem0) shows this is over-engineered.

**fork-cc approach**: no algorithmic dedup. The model receives a lightweight **manifest** of existing memory filenames + one-line descriptions and is instructed to check for duplicates before writing. co-cli adapts this via a **singleton memory save agent** (same pattern as `_signal_agent`, `_summarizer_agent`) — called inline from `persist_memory()`, zero context debt on the main agent.

**Current-state validation:**
- `_lifecycle.py:140-186`: fuzzy dedup block — confirmed present, uses `_check_duplicate` + `_update_existing_memory`
- `_lifecycle.py:188-209`: LLM consolidation block — confirmed present, uses `_consolidator.consolidate()` + `apply_plan_atomically()`
- `_consolidator.py` + `memory/prompts/memory_consolidator.md`: standalone consolidation agent — confirmed present
- `memory.py:270-305`: `_check_duplicate()` with rapidfuzz — confirmed present, sole consumer of `rapidfuzz` in production code
- Config fields `memory_dedup_window_days`, `memory_dedup_threshold`, `memory_consolidation_top_k`, `memory_consolidation_timeout_seconds`: confirmed in `config.py:252-259`, `deps.py:139-143`
- `persist_memory()` has no resource lock — confirmed; `update_memory` and `append_memory` use `resource_locks.try_acquire()` but `persist_memory` does not
- **`_update_existing_memory` callers** (corrected per CD-M-1): `_lifecycle.py:161` (dedup path) and `_lifecycle.py:52` (apply_plan_atomically) are the only call sites. `update_memory` and `append_memory` tools implement their own in-place edit logic — they do NOT call `_update_existing_memory`. After removing lifecycle callers, `_update_existing_memory` has **zero callers** and is dead code.
- `Settings` in `config.py` has no explicit `model_config = ConfigDict(extra="ignore")` — relies on Pydantic v2 default (corrected per CD-M-2)
- Test impact: `test_memory_lifecycle.py` tests 1-3 and test 8 (`test_consolidation_single_call_within_budget`) directly test removed code; `test_memory.py:141` (`test_fts_freshness_after_consolidation`) tests the dedup→FTS path

## Problem & Outcome

**Problem:** The 2-tier dedup (fuzzy + LLM consolidator) is complexity without proportional value. The 7-day × 10-candidate window misses older duplicates. The consolidation agent adds ~2-20s latency per save. The `persist_memory` write path has no concurrency protection, allowing duplicate writes from concurrent save_memory + auto-signal calls.

**Failure cost:** Users accumulate duplicate memories that waste context window budget during recall. Concurrent saves silently create duplicates that the dedup window never catches (self-healing is delayed and unreliable).

**Outcome:** `save_memory` becomes an **upsert** — it always creates or updates transparently. A singleton memory save agent (same pattern as `_signal_agent`, `_summarizer_agent`) receives a manifest of all memories and returns SAVE_NEW or UPDATE(slug). The caller never decides create-vs-update; `persist_memory()` handles it. Remove the old consolidator, rapidfuzz, and 4 config fields. Add resource lock to `persist_memory`. Zero context debt on the main agent. This eliminates the FTS out-of-sync gap (Gap 4) as a side effect.

## Scope

**In scope:**
- Remove `_check_duplicate()`, `_update_existing_memory()`, `apply_plan_atomically()`, `_consolidator.py`, consolidation prompt
- Remove dedup + consolidation blocks from `_persist_memory_inner()`
- Remove 4 config fields and `rapidfuzz` dependency
- Add explicit `model_config = ConfigDict(extra="ignore")` to `Settings`
- Add `_build_memory_manifest()` — scan all memories, return one-line-per-entry manifest
- Add singleton `_memory_save_agent` — receives new content + manifest, returns SAVE_NEW or UPDATE(slug)
- Call from `persist_memory()` inline — zero context debt on main agent
- Add resource lock to `persist_memory()` write path
- Update/remove affected tests

**Out of scope:**
- Dream/batch consolidation (fork-cc's background consolidation phase) — future work
- Embedding-based similarity (mem0 approach) — decided against, manifest is simpler
- Changes to `update_memory`, `append_memory`, `recall_memory`, `search_memories` tools
- Signal detector changes (it calls `persist_memory` — benefits from lock fix automatically)

## Behavioral Constraints

1. **Zero context debt on main agent** — the manifest is never injected into the main agent's system prompt or dynamic instructions. It is only built and consumed inside `persist_memory()` by the memory save agent. The main agent pays zero tokens for save awareness.
2. **Memory save agent is fire-and-forget** — single LLM call per page, structured output (`SAVE_NEW` or `UPDATE(slug)`), same `ROLE_SUMMARIZATION` model as the old consolidator. Timeout + fallback: on timeout or error, fall through to `SAVE_NEW` (same as current `on_failure="add"` behavior).
3. **`persist_memory` must be atomic w.r.t. concurrent callers** — the resource lock must cover collision check → file write → FTS index. Retention (Step 4) runs outside the lock.
4. **`on_failure="skip"` must still skip cleanly** — when the auto-signal path hits a busy lock (`ResourceBusyError`), it must return `action="skipped"` without writing. The explicit save path (`on_failure="add"`) surfaces `ResourceBusyError` to the model as a tool error; the model retries on the next turn.
5. **`save_memory` is an upsert** — callers (main agent, signal detector) always call `save_memory`. They never need to choose between create and update. `persist_memory()` routes internally via the memory save agent. `update_memory` remains for surgical find-and-replace edits on a specific slug — it is not part of the upsert flow.
6. **Backward-compatible config removal** — `Settings` must have explicit `model_config = ConfigDict(extra="ignore")` so existing `settings.json` files with removed fields don't error on load.
7. **UPDATE overwrites body, not merge** — when the memory save agent returns UPDATE(slug), the new content replaces the old body entirely. The agent does not produce merged content — it only decides the routing. Frontmatter fields (tags, provenance, category, certainty) are re-derived from the new content; `updated` timestamp is set to now; `created` is preserved.

## High-Level Design

```text
BEFORE:
  save_memory / auto-signal → persist_memory()
    → load recent 10 memories (7-day window)
    → fuzzy match (rapidfuzz token_sort_ratio ≥ 85%)
      → hit: _update_existing_memory + FTS reindex
    → LLM consolidator (top-5, 20s timeout)
      → plan: apply_plan_atomically (UPDATE/DELETE/ADD)
    → write new file + FTS index
    → retention cap

AFTER (save_memory = upsert):
  save_memory / auto-signal → persist_memory()
    → acquire resource lock "memory:persist"
    → build manifest (all memories, one line each)
    → _memory_save_agent.run(new_content + manifest) → SAVE_NEW | UPDATE(slug)
      → SAVE_NEW: write new file + FTS index
      → UPDATE(slug): overwrite matched file + FTS reindex
    → release lock
    → retention cap (outside lock)

  Callers never decide create-vs-update. persist_memory() is an upsert.
  update_memory(slug, old, new) remains for surgical find-and-replace edits.
```

**Existing singleton agent pattern** (co-cli already uses this for signal detection and summarization):

| Agent | Module | Pattern |
|-------|--------|---------|
| `_signal_agent` | `memory/_signal_detector.py` | Module-level `Agent[None, SignalResult]`, single LLM call |
| `_summarizer_agent` | `context/_summarization.py` | Module-level `Agent[None, str]`, single LLM call |
| `_memory_save_agent` (new) | `memory/_save.py` | Module-level `Agent[None, SaveResult]`, single LLM call per page |

**Design decision — save_memory as upsert:** The main agent and signal detector always call `save_memory` / `persist_memory()`. They never need to decide between create and update — `persist_memory()` is an upsert that routes internally via the memory save agent. `update_memory(slug, old_content, new_content)` remains as a separate tool for **surgical find-and-replace edits** within a specific memory body. This simplifies the caller contract: "save this fact" is always the right call.

**Design decision — singleton memory save agent, not dynamic instruction injection:** fork-cc injects the manifest into the system prompt (every request). This adds 600-10K tokens of context debt even when the model isn't saving. co-cli uses singleton helper agents for this pattern — the memory save agent runs only inside `persist_memory()`, receives the manifest + new content as a user prompt, and returns a structured decision. Zero cost on non-save turns. Same LLM call cost as the old consolidator, but simpler output schema (SAVE_NEW vs UPDATE(slug)) and no multi-action plan resolution.

**Design decision — no algorithmic fallback:** The resource lock (TASK-3) prevents concurrent-write duplicates. The memory save agent handles semantic duplicates. The rare miss is caught by `/forget` or the next save cycle. Keeping rapidfuzz adds a dependency for a tier the memory save agent makes redundant.

## Implementation Plan

### ✓ DONE — TASK-1: Strip dedup + consolidation from lifecycle

Remove the algorithmic dedup fast-path, LLM consolidation blocks, and all dead code from `_persist_memory_inner()`.

**What to remove:**
- `_lifecycle.py:28-68` — `apply_plan_atomically()` function
- `_lifecycle.py:120-130` — imports of `_check_duplicate`, `_update_existing_memory`, `_parse_created` (keep `slugify`, `_classify_certainty`, `_detect_provenance`, `_detect_category`)
- `_lifecycle.py:136` — `memories = load_memories(...)` (no longer needed for dedup; retention at line 292 does its own load)
- `_lifecycle.py:140-186` — entire dedup block (Step 1 + Step 2)
- `_lifecycle.py:188-218` — entire LLM consolidation block (Step 2b)
- `_consolidator.py` — delete entire file
- `memory/prompts/memory_consolidator.md` — delete prompt file
- `memory.py:270-305` — `_check_duplicate()` function
- `memory.py:313-365` — `_update_existing_memory()` function (zero callers after lifecycle removal — `update_memory` and `append_memory` tools have their own in-place edit logic)

**What stays in `_persist_memory_inner()`:**
- artifact_type validation (line 220-226)
- new memory write (line 228-280)
- retention cap (line 290-318)

files: `co_cli/memory/_lifecycle.py`, `co_cli/tools/memory.py`, `co_cli/memory/_consolidator.py`, `co_cli/memory/prompts/memory_consolidator.md`
done_when: `grep -r "_check_duplicate\|apply_plan_atomically\|_consolidator\|_update_existing_memory" co_cli/` returns zero matches; `_consolidator.py` and `memory_consolidator.md` do not exist; `uv run python -c "from co_cli.memory._lifecycle import persist_memory; print('ok')"` succeeds
success_signal: N/A (internal refactor, no user-visible change yet)

### ✓ DONE — TASK-2: Remove dedup config fields, add extra="ignore", remove rapidfuzz

Remove the 4 config fields that only served the deleted dedup/consolidation logic. Remove `rapidfuzz` from dependencies. Add explicit `extra="ignore"` to `Settings`.

**Config fields to remove:**
- `config.py`: `memory_dedup_window_days`, `memory_dedup_threshold`, `memory_consolidation_top_k`, `memory_consolidation_timeout_seconds` (fields + defaults + env var mappings)
- `deps.py`: corresponding fields in `CoConfig` and `CoConfig.from_settings()` mapping

**Dependency to remove:**
- `pyproject.toml`: remove `rapidfuzz` from `[project.dependencies]`
- `co_cli/tools/memory.py`: remove `from rapidfuzz import fuzz` import

**Backward compatibility:**
- Add `model_config = ConfigDict(extra="ignore")` to `Settings` class in `config.py` so existing `settings.json` files with removed fields are silently ignored.

files: `co_cli/config.py`, `co_cli/deps.py`, `pyproject.toml`, `co_cli/tools/memory.py`
prerequisites: [TASK-1]
done_when: `grep -r "memory_dedup_window\|memory_dedup_threshold\|memory_consolidation_top_k\|memory_consolidation_timeout\|rapidfuzz" co_cli/ pyproject.toml` returns zero matches; `grep -n "extra.*ignore" co_cli/config.py` returns a match; `uv run python -c "from co_cli.config import settings; print('ok')"` succeeds
success_signal: N/A (config cleanup)

### ✓ DONE — TASK-3: Add resource lock to persist_memory

Wrap the write-critical section of `persist_memory` with `deps.resource_locks.try_acquire("memory:persist")`. Handle `ResourceBusyError` gracefully for the auto-signal path.

**Lock scope:**
```python
from co_cli.tools._resource_lock import ResourceBusyError

try:
    async with deps.resource_locks.try_acquire("memory:persist"):
        # artifact_type validation
        # write new file
        # FTS index
except ResourceBusyError:
    if on_failure == "skip":
        return tool_output("⚠ Memory save skipped (concurrent write)", action="skipped")
    raise  # explicit save path — surface error to model, which retries next turn
```

**Retention cap** stays outside the lock — it only deletes old files and has its own idempotent logic.

**Non-blocking semantics:** `try_acquire` is non-blocking (raises immediately if held). For `on_failure="skip"` (auto-signal), this is correct — fail-fast, skip. For `on_failure="add"` (explicit save), `ResourceBusyError` propagates to the model as a tool error; the model retries on the next turn. This matches the existing pattern where `update_memory` and `append_memory` use `try_acquire` with the same fail-fast semantics.

files: `co_cli/memory/_lifecycle.py`
prerequisites: [TASK-1]
done_when: `grep -n "try_acquire.*memory:persist" co_cli/memory/_lifecycle.py` returns a match; `grep -n "ResourceBusyError" co_cli/memory/_lifecycle.py` returns a match
success_signal: N/A (concurrency fix, not user-visible)

### ✓ DONE — TASK-4: Add singleton memory save agent + manifest builder

Add `_memory_save_agent` singleton and `_build_memory_manifest()`, called inline from `persist_memory()`. Replaces the old `_consolidator.py` with a simpler agent following the existing `_signal_agent` / `_summarizer_agent` pattern. The memory save agent is tasked by the main agent or signal detector to persist a fact — it loads existing memories, checks for collision, and decides create or update.

**New file: `co_cli/memory/_save.py`**

Module-level singleton agent with structured output:

```python
class SaveResult(BaseModel):
    """Structured output from the memory save agent."""
    action: Literal["SAVE_NEW", "UPDATE"]
    target_slug: str | None = None  # populated when action == "UPDATE"

_memory_save_agent: Agent[None, SaveResult] = Agent(
    output_type=SaveResult,
    instructions=_SAVE_PROMPT_PATH.read_text(encoding="utf-8"),
    retries=0,
    output_retries=0,
)
```

**Dedup agent prompt** (`co_cli/memory/prompts/memory_save.md`) — adapted from fork-cc's behavioral rules:

```markdown
You are a memory save agent. Given a candidate memory and a manifest of
existing memories, decide whether to create a new entry or update an existing one.

Rules (adapted from fork-cc + mem0):
- Do not create duplicate memories. Check the existing list first.
- If the candidate covers the same topic as an existing memory (same or
  updated information), return UPDATE with the matching slug.
- If the candidate contradicts an existing memory (correction, changed
  preference), return UPDATE with the slug to overwrite.
- If the candidate is genuinely new information not covered by any existing
  memory, return SAVE_NEW.
- "Likes cheese pizza" vs "Loves cheese pizza" = UPDATE (same meaning,
  richer phrasing). From mem0's consolidation rules.
- When in doubt, prefer SAVE_NEW — false negatives (extra memory) are
  cheaper than false positives (lost information).
```

**Manifest builder** in `co_cli/tools/memory.py`:

```python
def _build_memory_manifest(memories: list[MemoryEntry]) -> str:
```
- Takes a pre-sliced list of `MemoryEntry` objects (caller controls page size)
- Format per entry: `- {stem} ({updated or created}): {content[:80]}  [{tags}]`
- `stem` is the filename without `.md` — this is the slug for `update_memory`

**Collision check** — single page, 100 most recent memories:

```python
PAGE_SIZE = 100  # conservative for local models; relax later if needed
memories = load_memories(memory_dir, kind="memory")
memories.sort(by recency)
manifest = format_manifest(memories[:PAGE_SIZE])

result = await check_and_save(content, manifest, resolved, timeout)
if result.action == "UPDATE" and result.target_slug:
    _overwrite_memory(memory_dir, result.target_slug, content, tags, auto_save_tags)
    # FTS reindex
    return tool_output("✓ Updated memory ...", action="consolidated")
# no match → SAVE_NEW (write new file)
```

Hardcoded single-page for MVP. Most duplicates are recent — 100 entries covers the common case. Pagination (`full` mode) can be added later if users report missed old duplicates.

**UPDATE write mechanism** — `_overwrite_memory()` helper in `_save.py`:

Extracted from the deleted `_update_existing_memory()` (`memory.py:313-365`). Performs full-body overwrite with frontmatter refresh:
1. Read existing file's frontmatter
2. Merge tags (union of existing + new)
3. Set `updated` timestamp to now
4. Re-derive `provenance`, `auto_category`, `certainty` from new content
5. Write full body (new content replaces old content entirely)
6. FTS reindex via `knowledge_store.index()` if available

This is the same logic as the deleted function, just extracted into the save module where it belongs.

**Integration in `persist_memory()`** (`_lifecycle.py`):

```python
# Inside the resource lock, before writing:
if resolved is not None and resolved.model is not None:
    manifest = _build_memory_manifest(memory_dir)
    if manifest:  # has existing memories to check against
        save_result = await check_and_save(content, manifest, resolved, timeout)
        if save_result.action == "UPDATE" and save_result.target_slug:
            _overwrite_memory(memory_dir, save_result.target_slug, content, tags, auto_save_tags)
            # FTS reindex
            return tool_output("✓ Updated memory ...", action="consolidated")
# fall through to SAVE_NEW (write new file)
```

Timeout + fallback: on timeout, model error, or `on_failure="skip"`, falls through to SAVE_NEW (explicit path) or skips (auto-signal path) — same contract as old consolidator.

**`save_memory` docstring update** — simplify to reflect upsert semantics: "Saves a memory. If a near-duplicate exists, the existing memory is updated instead of creating a new file. Always use save_memory to persist facts — never call update_memory for dedup purposes. update_memory is for surgical find-and-replace edits only."

files: `co_cli/memory/_save.py`, `co_cli/memory/prompts/memory_save.md`, `co_cli/tools/memory.py`, `co_cli/memory/_lifecycle.py`
prerequisites: [TASK-1, TASK-3]
done_when: `grep -n "_memory_save_agent" co_cli/memory/_save.py` returns a match; `grep -n "check_and_save" co_cli/memory/_lifecycle.py` returns a match; `uv run pytest tests/test_memory.py::test_build_memory_manifest_format tests/test_memory_lifecycle.py::test_persist_memory_upsert_updates_existing -x` passes
success_signal: When saving a memory that near-duplicates an existing one, persist_memory updates the existing entry instead of creating a new file.

### ✓ DONE — TASK-5: Update tests

Remove tests that exercise deleted code. Rewrite on_failure branching tests for lock-based behavior. Update helper that depends on removed config fields. Add new tests.

**Tests to remove:**
- `test_memory_lifecycle.py::test_update_action_sets_updated_timestamp` — tests `apply_plan_atomically` (deleted)
- `test_memory_lifecycle.py::test_delete_action_removes_non_protected_keeps_protected` — tests `apply_plan_atomically` (deleted)
- `test_memory_lifecycle.py::test_dedup_refreshes_timestamp_no_new_file` — tests rapidfuzz dedup fast-path (deleted)
- `test_memory_lifecycle.py::test_consolidation_single_call_within_budget` — tests consolidator agent (deleted)
- `test_memory.py::test_fts_freshness_after_consolidation` — tests dedup→FTS path (deleted)

**Tests to rewrite** (not delete — the on_failure contract still applies, trigger changes from timeout to lock):
- `test_memory_lifecycle.py::test_explicit_save_fallback_writes_on_timeout` → `test_explicit_save_raises_on_lock_conflict`: when lock is held and `on_failure="add"`, `persist_memory` raises `ResourceBusyError`
- `test_memory_lifecycle.py::test_auto_signal_skip_no_file_on_timeout` → `test_auto_signal_skip_on_lock_conflict`: when lock is held and `on_failure="skip"`, returns `action="skipped"` with no file written

**Tests to update:**
- `test_memory_lifecycle.py::_make_deps` — remove `timeout` parameter and `memory_consolidation_timeout_seconds=timeout` assignment from `replace()` call

**Tests to add:**
- `test_persist_memory_writes_new_file` — basic persist_memory creates a file (replaces dedup test as the simple lifecycle smoke test)
- `test_build_memory_manifest_format` — manifest builder returns expected one-line-per-entry format, respects max_entries cap, sorts by recency
- `test_memory_save_agent_returns_update_on_near_duplicate` — memory save agent returns UPDATE(slug) when candidate near-duplicates an existing memory (real LLM call, `ROLE_SUMMARIZATION` model)
- `test_memory_save_agent_returns_save_new_on_novel` — memory save agent returns SAVE_NEW when candidate is genuinely new
- `test_persist_memory_upsert_updates_existing` — full integration: seed a memory, call `persist_memory` with near-duplicate content, assert no new file created + existing file body updated + `updated` timestamp set

files: `tests/test_memory_lifecycle.py`, `tests/test_memory.py`
prerequisites: [TASK-1, TASK-2, TASK-3, TASK-4]
done_when: `uv run pytest tests/test_memory_lifecycle.py tests/test_memory.py -x` passes with zero failures
success_signal: N/A (test maintenance)

### ✓ DONE — TASK-6: Verify Gap 4 elimination + dead code cleanup

Confirm that `_update_existing_memory` has zero callers anywhere in `co_cli/`. Confirm `_lifecycle.py` no longer imports or references it.

files: (verification only — no code changes beyond TASK-1)
prerequisites: [TASK-1]
done_when: `grep -rn "_update_existing_memory" co_cli/` returns zero matches
success_signal: N/A (verification)

### ✓ DONE — TASK-7: Broaden signal detector to general memory extraction

Replace the narrow correction/preference signal detector with a general-purpose memory extractor following the fork-cc extraction agent pattern. The extractor scans recent conversation turns for any memory-worthy content across all 4 memory types (user, feedback, project, reference), not just corrections and preferences.

**What changes:**
- `SignalResult` schema: expand `tag` from `Literal["correction", "preference"]` to `Literal["user", "feedback", "project", "reference"]` — aligns with memory type taxonomy
- `signal_analyzer.md` prompt: rewrite to scan for all 4 memory types with examples. Keep the confidence gate (high=auto-save, low=ask). Keep the sensitivity guardrail.
- `handle_signal`: remove `memory_auto_save_tags` gate — all 4 types are auto-saveable. The confidence gate is the admission control, not the tag filter.
- `_build_window`: increase cap from 10 lines (~5 turns) to 20 lines (~10 turns) for broader extraction context.

**What stays the same:**
- Singleton `_signal_agent` pattern — single LLM call, structured output
- `ROLE_ANALYSIS` model for extraction
- Post-turn hook in `_finalize_turn` — fires on clean turns only
- `on_failure="skip"` for auto-saves, `on_failure="add"` for user-confirmed saves
- `inject` field for `personality-context` tag
- `persist_memory` as the write path (upsert via save agent)

**Behavioral constraints:**
1. No new LLM calls — same single-call pattern, same model role
2. confidence="high" still auto-saves without prompting; confidence="low" still asks
3. Sensitive content guardrail preserved verbatim
4. `memory_auto_save_tags` config field retained but reinterpreted: controls which types auto-save at high confidence (default: all 4). Empty list = all signals require user confirmation regardless of confidence.

files: `co_cli/memory/_signal_detector.py`, `co_cli/memory/prompts/signal_analyzer.md`
prerequisites: [TASK-4]
done_when: `grep -n "user.*feedback.*project.*reference" co_cli/memory/_signal_detector.py` returns a match for the expanded tag type; `grep -n "user\|feedback\|project\|reference" co_cli/memory/prompts/signal_analyzer.md` returns matches for all 4 types; `uv run python -c "from co_cli.memory._signal_detector import SignalResult; r = SignalResult(found=True, candidate='test', tag='project', confidence='high'); print(r.tag)"` prints "project"
success_signal: Signal detector extracts project/reference/user facts from conversation, not just corrections and preferences.

### ✓ DONE — TASK-8: Multi-memory extraction — N candidates per turn

Replace the single-result `SignalResult` with a list output so the extractor can capture multiple memory-worthy signals from a single conversation window.

**What changes:**
- New output schema: `ExtractionResult` with `memories: list[MemoryCandidate]` where `MemoryCandidate` has the same fields as current `SignalResult` (candidate, tag, confidence, inject). Cap at 3 candidates per extraction.
- `analyze_for_signals` returns `ExtractionResult` instead of `SignalResult`
- `handle_signal` iterates over the list, applying the same admission policy per candidate
- Prompt updated to instruct: "Extract up to 3 distinct memories. Each must be a separate fact — do not combine unrelated signals into one entry."

**What stays the same:**
- Single LLM call — structured output with a list, not multiple calls
- Same `_signal_agent` singleton, same `ROLE_ANALYSIS` model
- Same confidence/admission gate per candidate
- Same `persist_memory` write path per candidate (upsert via save agent)

**Behavioral constraints:**
1. Dedup within a single extraction: the prompt instructs "no two candidates should cover the same fact." The save agent handles cross-extraction dedup via manifest.
2. Each candidate gets its own `persist_memory` call (sequentially, inside the same turn). The resource lock serializes them.
3. `on_failure="skip"` for all auto-saves — if the lock is busy from a prior candidate in the same batch, subsequent candidates skip silently.

files: `co_cli/memory/_signal_detector.py`, `co_cli/memory/prompts/signal_analyzer.md`
prerequisites: [TASK-7]
done_when: `uv run python -c "from co_cli.memory._signal_detector import ExtractionResult, MemoryCandidate; r = ExtractionResult(memories=[MemoryCandidate(candidate='a', tag='user', confidence='high'), MemoryCandidate(candidate='b', tag='project', confidence='low')]); print(len(r.memories))"` prints "2"; `grep -n "list\[MemoryCandidate\]" co_cli/memory/_signal_detector.py` returns a match
success_signal: A single turn containing a user fact, a project decision, and a reference extracts all 3 as separate memory candidates.

### ✓ DONE — TASK-9: Fire-and-forget async extraction

Move the extraction-save process from blocking `_finalize_turn` to fire-and-forget async execution, matching fork-cc's `runForkedAgent` pattern. The main REPL loop should not wait for memory extraction to complete before showing the next prompt.

**What changes:**
- `_finalize_turn` launches extraction as a background task instead of awaiting it
- New `_run_extraction_async()` wrapper in `_signal_detector.py` that:
  1. Calls `analyze_for_signals()`
  2. Calls `handle_signal()` for each candidate
  3. Catches all exceptions (never crashes the loop)
  4. Logs completion/failure
- Overlap guard: if an extraction is already running, skip (same as fork-cc's `inProgress` flag + stash pattern). Simple skip — no stash queue for MVP.
- Drain hook: `drain_pending_extraction(timeout_ms)` awaits the in-flight task with a timeout. Called before session exit so memories aren't lost on quick quit.

**What stays the same:**
- Only fires on clean, non-interrupted turns
- Same `ROLE_ANALYSIS` model, same structured output
- Same `persist_memory` write path (resource lock handles concurrency with explicit `save_memory` calls)
- `handle_signal` still prompts for low-confidence signals — but since extraction is async, prompting happens in the background. Low-confidence candidates that would need user input are logged and skipped in async mode; only high-confidence auto-saves fire.

**Behavioral constraints:**
1. Async extraction must never block the REPL prompt return
2. Low-confidence signals are skipped in async mode (no way to prompt user from background). Logged as "deferred: {candidate}" for visibility.
3. Overlap guard prevents concurrent extractions — at most one in-flight at a time
4. `drain_pending_extraction()` called on `/quit`, `/exit`, and session end

files: `co_cli/memory/_signal_detector.py`, `co_cli/main.py`
prerequisites: [TASK-8]
done_when: `grep -n "create_task\|fire_and_forget\|drain_pending" co_cli/memory/_signal_detector.py` returns matches; `grep -n "drain_pending" co_cli/main.py` returns a match; the REPL prompt returns immediately after a turn without waiting for extraction
success_signal: Memory extraction runs in the background — user sees the next prompt before "Learned: ..." appears.

## Testing

- **TASK-5** covers test changes for TASK-1 through TASK-6
- Key regression: `persist_memory` still writes files, still triggers retention, still indexes in FTS
- Key new behavior: resource lock prevents concurrent writes; memory save agent detects collisions and routes to create or update
- Existing `test_overflow_cut_oldest_unprotected` and `test_retention_cap_excludes_articles` remain unchanged — they test retention, not dedup
- The two on_failure branching tests are rewritten (not deleted) to test lock-based skip/raise behavior

## Open Questions

None — all design decisions are resolved by peer analysis and reviewer feedback.

## Final — Team Lead

Plan approved after final review (F1). All blockers resolved: UPDATE write mechanism specified (`_overwrite_memory` helper), scan mode simplified (hardcoded single-page 100), behavioral constraint added (UPDATE = full body overwrite), integration test added.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-dedup-rewrite`

---

# Audit Log (Final Review)

## Cycle F1 — Team Lead
Plan substantially revised post-C2: TASK-4 changed from dynamic instruction injection to singleton memory save agent, upsert semantics, paginated scan modes, renamed from dedup to save. Submitting for final Core Dev + PO review.

## Cycle F1 — PO

**Assessment: revise** — one blocking concern, two non-blocking recommendations.

### Blocking

**PO-M-1: `memory_save_scan_mode` config + paginated `full` mode is premature scope.**
The stated problem is "2-tier dedup is complexity without proportional value." The solution should be simpler, not differently complex. Adding a new config field (`memory_save_scan_mode`) with two modes and a pagination loop (1-N LLM calls in `full` mode) reintroduces the same category of complexity the plan removes — configurable multi-pass scanning — just with an LLM instead of rapidfuzz. The `latest` mode (single page of 100) already covers the stated failure cost ("most duplicates are recent"). The `full` mode is speculative future-proofing for a problem that has no evidence in the current system: old duplicates caught by exhaustive scan but missed by recency-100. Ship with `latest` behavior hardcoded (no config field, no pagination loop). If users report missed old duplicates post-ship, add `full` mode then with real data justifying it. **Recommendation:** Remove `memory_save_scan_mode` from TASK-4. Remove the `full` pagination loop. Hardcode single-page (100 most recent) behavior. Remove the config field from `Settings`/`CoConfig`/env var. This eliminates ~30% of TASK-4's implementation surface.

### Non-blocking

**PO-R-1: Behavioral constraint gap — UPDATE content merging policy is unspecified.**
Constraint 5 says "persist_memory() routes internally via the memory save agent" but does not specify what happens to content on UPDATE. When the agent returns `UPDATE(slug)`, does the new content fully replace the old body? Is it merged? Does the agent produce merged content? The current dedup path (`_update_existing_memory`) replaces the body and refreshes timestamps. The plan's pseudocode shows "overwrite matched file" but the behavioral constraints do not codify this. The prompt example ("Likes cheese pizza" vs "Loves cheese pizza" = UPDATE) implies full replacement, but the constraint should say so explicitly. **Recommendation:** Add to Behavioral Constraints: "UPDATE overwrites the existing memory body with the new content verbatim. The memory save agent does not produce merged content — it only decides the routing. Frontmatter (tags, provenance, category) is re-derived from the new content."

**PO-R-2: Effectiveness risk — local 35B model reliability on 100-entry manifests.**
The plan targets `ROLE_SUMMARIZATION` (typically a local model with `reasoning_effort: none`). A 100-entry manifest at ~100 chars/entry is ~10K tokens of input. The task requires the model to: (a) parse all 100 entries, (b) identify semantic overlap with the candidate, (c) return a specific slug string verbatim. This is a retrieval-in-context task, not summarization. Local 35B models are adequate for summarization but less reliable for precise string extraction from long lists. The fallback-to-SAVE_NEW design mitigates false negatives (model fails to find match = new file created = duplicate survives). This is acceptable for MVP given the fail-safe, but the plan should acknowledge this as a known limitation and set an expectation that the match rate will be lower than the old rapidfuzz path for near-verbatim duplicates. No plan change needed — just awareness for the delivery summary.

## Cycle F1 — Core Dev

**Verdict: CONDITIONAL PASS — 1 blocking, 2 minor issues must be resolved before implementation.**

### Blocking

**CD-F1-B1: TASK-4 UPDATE path has no write mechanism after `_update_existing_memory` deletion.**
The pseudocode says "delegate to update_memory's in-place edit logic" when the save agent returns `UPDATE(slug)`. But `update_memory` (`memory.py:936`) is a find-and-replace tool — it takes `(slug, old_content, new_content)` and requires `old_content` to appear exactly once in the body. That is fundamentally different from a full-body overwrite. The deleted `_update_existing_memory` (`memory.py:313-365`) was the full-body overwrite: it reads frontmatter, merges tags, sets `updated` timestamp, and writes the entire new body. After TASK-1 deletes it, TASK-4 has no mechanism to perform the UPDATE.

**Fix required:** TASK-4 must either (a) extract the overwrite logic from `_update_existing_memory` into a private helper in `_save.py` or `_lifecycle.py` (read frontmatter, merge tags, set `updated`, write full body + FTS reindex), or (b) explicitly inline equivalent logic in `persist_memory()`'s UPDATE branch. The pseudocode `...` ellipsis hides this gap — it must be spelled out before implementation starts.

### Minor

**CD-F1-M1: TASK-4 config field addition needs full plumbing list (assuming PO-M-1 is rejected).**
If `memory_save_scan_mode` survives PO-M-1, TASK-4's `files:` list is incomplete. The new field must be added to: `config.py` (Settings field + `fill_from_env` env var mapping), `deps.py` (CoConfig field + `from_settings` mapping). Currently TASK-4 mentions "`Settings` / `CoConfig`" in prose but `deps.py` is not in TASK-4's `files:` list, and `config.py` is not either. If PO-M-1 is accepted (hardcode `latest`, no config), this issue is moot.

**CD-F1-M2: TASK-4 `done_when` lacks behavioral integration assertion.**
`done_when` for TASK-4 includes `grep -n "check_and_save"` and a manifest format unit test, but the `success_signal` describes upsert behavior ("persist_memory updates the existing entry instead of creating a new file"). There is no `done_when` entry that exercises the full integration path — e.g., calling `persist_memory` with a near-duplicate and asserting no new file is created + existing file is updated. TASK-5 adds `test_memory_save_agent_returns_update_on_near_duplicate` but that tests the agent in isolation, not the `persist_memory` wiring. Per checklist: user-facing tasks with non-N/A `success_signal` need a behavioral assertion in `done_when`. Add something like `uv run pytest tests/test_memory_lifecycle.py::test_persist_memory_upsert_updates_existing -x`.

### Verified (no issues)

- **Prerequisite DAG**: valid. TASK-2,3,6 depend on TASK-1. TASK-4 depends on TASK-1,3. TASK-5 depends on all. No cycles.
- **No DESIGN docs in `files:` lists**: confirmed across all 6 tasks.
- **`done_when` criteria**: all machine-verifiable (grep, test runs, Python -c snippets). TASK-6 is verification-only with grep — appropriate.
- **Behavioral Constraints**: 6 constraints, all specific and testable (zero context debt, fire-and-forget semantics, atomicity scope, on_failure branching, upsert contract, backward-compatible config).
- **Current-state validation**: line numbers and caller analysis match actual source. `_update_existing_memory` callers at `_lifecycle.py:45,52,124,161` and definition at `memory.py:313` confirmed via grep. `Settings` has no `extra="ignore"` confirmed — only occurrence of "extra" in `config.py` is an unrelated comment on line 258.
- **Resource lock semantics**: `ResourceLockStore.try_acquire` is non-blocking (raises `ResourceBusyError` immediately if held) — matches TASK-3 pseudocode. Access path `deps.resource_locks.try_acquire(...)` correct per `CoDeps` at `deps.py:390`.
- **Signal detector**: calls `persist_memory` with `on_failure="skip"` (`_signal_detector.py:153-156`) — benefits from lock protection (TASK-3) automatically, no code changes needed. Confirmed out-of-scope claim is accurate.
- **Test compliance**: no mocks proposed. New tests use real LLM calls with `ROLE_SUMMARIZATION`. Tests to remove correctly identified (tests 1-3 import `ConsolidationPlan`/`MemoryAction`/`apply_plan_atomically` from `_consolidator` — all deleted). `_make_deps` timeout parameter removal correct (field deleted in TASK-2). Rewrite targets (tests 5-6) correctly map from timeout-based to lock-based trigger.
- **`_parse_created` import**: listed for removal — confirmed only used in dedup cutoff computation (`_lifecycle.py:146`) which is within the removal block.
- **Concurrency safety**: lock key `"memory:persist"` is a single global key — serializes all persist_memory calls. Appropriate since the manifest + write must be atomic. Retention cap runs outside lock — confirmed idempotent (only deletes oldest, no read-modify-write race).

## Cycle F1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-F1-B1 | adopt | Correct — UPDATE path needs a write mechanism after `_update_existing_memory` deletion. | Added `_overwrite_memory()` helper spec to TASK-4. Extracted from deleted function — same logic (frontmatter merge, tag union, timestamp, full body replace), housed in `_save.py`. Integration pseudocode now calls `_overwrite_memory()` explicitly instead of `...` ellipsis. |
| PO-M-1 | adopt | Correct — scan mode config is premature. Hardcode single-page (100 most recent) for MVP. | Removed `memory_save_scan_mode` config, env var, and pagination loop from TASK-4. Simplified to single `memories[:PAGE_SIZE]` slice. |
| CD-F1-M1 | moot | Config field removed per PO-M-1. | — |
| CD-F1-M2 | adopt | User-facing task needs behavioral integration assertion. | Added `test_persist_memory_upsert_updates_existing` to TASK-5 and TASK-4 done_when. |
| PO-R-1 | adopt | UPDATE body policy should be explicit. | Added Behavioral Constraint 7: "UPDATE overwrites body entirely, agent only decides routing, frontmatter re-derived from new content." |
| PO-R-2 | noted | Acknowledged — local model match rate may be lower than rapidfuzz for near-verbatim. SAVE_NEW fallback makes this acceptable for MVP. | No plan change — noted for delivery summary. |

## Delivery Summary — 2026-04-06

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep zero matches, files deleted, import succeeds | ✓ pass |
| TASK-2 | grep zero matches, extra="ignore" present, settings loads | ✓ pass |
| TASK-3 | grep matches for try_acquire + ResourceBusyError | ✓ pass |
| TASK-4 | _memory_save_agent in _save.py, check_and_save in _lifecycle.py, tests pass | ✓ pass |
| TASK-5 | pytest tests/test_memory_lifecycle.py tests/test_memory.py passes | ✓ pass |
| TASK-6 | grep _update_existing_memory returns zero source matches | ✓ pass |
| TASK-7 | expanded tag type, all 4 types in prompt, SignalResult(tag='project') accepted | ✓ pass |
| TASK-8 | ExtractionResult with list[MemoryCandidate], multi-candidate schema | ✓ pass |
| TASK-9 | fire_and_forget_extraction + drain_pending in main.py | ✓ pass |

**Tests:** full suite — 339 passed, 0 failed
**Independent Review:** 4 blocking fixed (return type annotation, path traversal, silent data loss, rapidfuzz in eval), 4 minor (2 fixed: dead import, stale eval dep; 2 noted: callable params as Any, hardcoded timeout)
**Doc Sync:** fixed (DESIGN-context.md: §2.4 lifecycle rewritten, 4 config entries removed, Files table updated)

**Overall: DELIVERED**
Replaced 2-tier dedup (rapidfuzz + LLM consolidator) with a singleton memory save agent following the fork-cc manifest pattern. persist_memory is now an atomic upsert protected by resource lock. Save agent correctly returns SAVE_NEW for novel content and UPDATE(slug) for near-duplicates (~2.5s per call on local qwen3.5:35b). Removed rapidfuzz dependency, 4 config fields, and ~200 lines of dead code. TASK-7 broadened the signal detector from narrow correction/preference extraction to general-purpose memory extraction across all 4 types (user, feedback, project, reference). TASK-8 added multi-candidate extraction (up to 3 per turn). TASK-9 moved extraction to fire-and-forget async with overlap guard and drain-on-exit — REPL prompt returns immediately, matching the fork-cc runForkedAgent pattern. PO-R-2 acknowledged: local model match rate may be lower than rapidfuzz for near-verbatim; SAVE_NEW fallback makes this acceptable for MVP.

## Implementation Review — 2026-04-06

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | grep zero matches, files deleted, import succeeds | ✓ pass | `_consolidator.py` deleted, `memory_consolidator.md` deleted, `grep -r "_check_duplicate\|apply_plan_atomically\|_consolidator\|_update_existing_memory" co_cli/` returns 0 matches, `from co_cli.memory._lifecycle import persist_memory` succeeds |
| TASK-2 | grep zero matches, extra="ignore", settings loads | ✓ pass | `config.py:220` — `model_config = ConfigDict(extra="ignore")`, grep for removed fields returns 0, `from co_cli.config import settings` succeeds |
| TASK-3 | try_acquire + ResourceBusyError in lifecycle | ✓ pass | `_lifecycle.py:174` — `deps.resource_locks.try_acquire(target_path)` (UPDATE path), `_lifecycle.py:222` — `deps.resource_locks.try_acquire(str(file_path))` (SAVE_NEW path), `ResourceBusyError` handled at lines 181, 244 |
| TASK-4 | save agent + check_and_save + tests pass | ✓ pass | `_save.py:37` — `_memory_save_agent` singleton, `_save.py:64` — `check_and_save()`, `_save.py:103` — `overwrite_memory()`, `_lifecycle.py:163,170` — import and call path, `_save.py:134-137` — path traversal guard |
| TASK-5 | pytest test_memory_lifecycle + test_memory passes | ✓ pass | 339 passed, 0 failed. Old tests removed, new tests added (manifest format, save agent SAVE_NEW/UPDATE, upsert integration, lock conflict tests) |
| TASK-6 | grep _update_existing_memory returns zero | ✓ pass | `grep -rn "_update_existing_memory" co_cli/` returns 0 matches |
| TASK-7 | expanded tag type, all 4 types in prompt | ✓ pass | `_extractor.py:40` — `Literal["user", "feedback", "project", "reference"]`, `memory_extractor.md` has all 4 type sections with examples, `config.py:193` — `DEFAULT_MEMORY_AUTO_SAVE_TAGS` includes all 4 |
| TASK-8 | ExtractionResult with list[MemoryCandidate] | ✓ pass | `_extractor.py:48` — `memories: list[MemoryCandidate]`, `_extractor.py:28` — `_MAX_CANDIDATES = 3`, schema import test prints "2" |
| TASK-9 | fire_and_forget + drain_pending in main.py | ✓ pass | `_extractor.py:238` — `fire_and_forget_extraction()`, `_extractor.py:256` — `drain_pending_extraction()`, `main.py:86,92` — fire-and-forget on clean turns, `main.py:241-242` — drain on exit, `_extractor.py:245-247` — overlap guard |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale docstring: "consolidation, signal analysis" | `co_cli/memory/__init__.py:1` | minor | Updated to "retention, extraction, and save agent" |
| Stale tool description: "consolidates via lifecycle dedup" | `docs/DESIGN-tools.md:278` | minor | Updated to "Upsert via memory save agent" |

### Tests
- Command: `uv run pytest -v`
- Result: 339 passed, 0 failed
- Log: `.pytest-logs/20260406-*-review-impl.log`

### Doc Sync
- Scope: narrow — only two stale descriptions, no API or schema changes
- Result: fixed: `__init__.py` docstring, DESIGN-tools.md save_memory description

### Behavioral Verification
- `uv run co config`: ✓ healthy (LLM Online, all components active)
- `success_signal` TASK-4: verified via `test_persist_memory_upsert_updates_existing` (passed — upsert routes to UPDATE on near-duplicate)
- `success_signal` TASK-9: verified via `main.py:92` fire-and-forget wiring + `main.py:242` drain-on-exit

### Overall: PASS
All 9 tasks implemented to spec. 339 tests green. Two minor doc stale references fixed. Ship-ready.
