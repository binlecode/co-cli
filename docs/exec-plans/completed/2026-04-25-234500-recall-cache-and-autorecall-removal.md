# Plan: Recall Cache Fix + Auto-Injection Removal (D1)

Task type: code-refactor

Part 1 of 2 — paired with `docs/exec-plans/active/2026-04-25-234530-memory-search-summarization.md` (D2). D2 ships only after D1 is delivered. Originally drafted as a single plan at `docs/exec-plans/active/2026-04-25-220616-recall-redesign-cache-and-search.md`; split for delivery quality.

---

## Context

Identified via cross-peer review of co-cli vs. hermes-agent recall mechanisms.

This is the first of two deliveries that together implement the recall redesign:
- **D1 (this plan):** cache hygiene + architectural cleanup. Move personality memories into the cache-stable static prompt; delete the always-3 knowledge auto-injection; rewire per-artifact recall telemetry. Net deletes + one function move + one rewire.
- **D2 (paired plan):** `memory_search` summarization upgrade + tool description tuning + disambiguation coaching. Net-new LLM call paths.

**Why split:** D2 introduces parallel noreason calls with retry/timeout/tracing — net-new behavior that benefits from landing on a stabilized base. D1 is fully reversible via single revert and lands the headline cache win without LLM-call risk.

Related but separate: `2026-04-19-211834-prompt-cache-two-gaps.md` (message normalization + Gemini context caching) — that plan addresses bit-exact prefix matching and Gemini cache wiring; this plan addresses *what content* sits in the cache-stable vs. cache-volatile regions of the prompt.

**Current-state validation:**

- `co_cli/context/prompt_text.py:55-88` — `recall_prompt_text()` builds the dynamic-instructions string from three parts in this order:
  1. `f"Today is {date.today().isoformat()}."` (volatile, daily)
  2. `_load_personality_memories()` from `co_cli/prompts/personalities/_injector.py:25` (stable per session, but emitted every turn)
  3. Top-3 knowledge recall via `_recall_for_context()` from `co_cli/tools/knowledge/read.py:68` (volatile, changes per query)
- `co_cli/agent/_core.py:131-156` — `build_agent()` already has a static-instructions path: `build_static_instructions(config)` is passed as `instructions=` at Agent construction. Personality *base* (soul mindsets) is in there per `personalities/_loader.py:19`. Personality *memories* (curated `personality-context`-tagged knowledge artifacts) currently land in the dynamic block instead.
- `co_cli/tools/knowledge/helpers.py:27` — `_touch_recalled()` increments per-artifact `recall_count` and stamps `last_recalled` in frontmatter. Verified sole caller is `_recall_for_context` (`co_cli/tools/knowledge/read.py:117`); after deletion the function becomes orphaned unless rewired.
- `co_cli/memory/state.py` — `MemoryRecallState` exists only to debounce auto-recall (`recall_count`, `last_recall_user_turn`). Used by `recall_prompt_text()` and a field on `CoSessionState` (`co_cli/deps.py:106`).
- `co_cli/config/memory.py:22` — `injection_max_chars` is consumed only by the deleted auto-injection path (`prompt_text.py:81`). No other consumers found.

---

## Problem & Outcome

**Problem 1 — Cache poisoning by volatile prefix.** `recall_prompt_text()` places today's date first in the dynamic-instructions block. When the date changes (or the recall content changes per query), the divergence point is at byte 0 of the dynamic block, invalidating every byte downstream — including the stable personality memories. Personality is paying a cache penalty for the date.

**Problem 2 — Personality memories in the wrong tier.** Personality memories are session-stable but currently re-emitted in dynamic instructions every turn. They belong in the static system prompt (`build_static_instructions`), which pydantic-ai sets once at Agent construction.

**Problem 3 — Always-3 knowledge auto-injection.** Top-3 knowledge artifacts injected per turn:
- pays tokens whether or not the user's turn benefits from recall
- changes content per query, breaking prefix cache stability across turns
- removes the agent's judgment about *when* recall is actually needed
- duplicates the existing on-demand `knowledge_search` tool

(Problem 4 — `memory_search` snippet shape — is addressed in D2.)

**Outcome (post-D1):**
- Static system prompt holds personality base + personality memories + base instructions → fully cacheable for the entire session.
- Dynamic instructions reduced to today's date only (and per-turn safety warnings, which are already conditional and small).
- Knowledge auto-recall removed; agent uses existing `knowledge_search` and `memory_search` (still snippet-form) proactively. Existing schema descriptions already coach proactive use.
- Per-artifact recall telemetry (`recall_count`/`last_recalled`) preserved by rewiring `_touch_recalled` from the deleted auto-injection path into `knowledge_search` after FTS5 hits.
- `MemoryRecallState`, `_recall_for_context`, and (likely) `injection_max_chars` deleted with grep verification.

After D1 ships, `memory_search` still returns BM25 snippets — D2 upgrades that to summary form and adds disambiguation coaching.

---

## Scope

**In scope:**
- Restructure `recall_prompt_text()` to date-only dynamic suffix; rename `recall_prompt` → `date_prompt`.
- Move personality memories injection into `build_static_instructions()` path.
- Merge `personalities/_injector.py` into `personalities/_loader.py`; drop leading underscore on the function.
- Delete `_recall_for_context()` and the auto-injection pipeline.
- Delete `MemoryRecallState` + `CoSessionState.memory_recall_state` field.
- Rewire `_touch_recalled` into `knowledge_search` to preserve per-artifact recall telemetry.
- Audit and likely drop `MemorySettings.injection_max_chars`.
- Sync `docs/specs/memory-knowledge.md` Sections 2.4 (cache rationale + on-demand recall) and 4 (Files).
- Test churn: rewrite `tests/context/test_history.py` recall tests; add personality-static test.
- Eval churn: delete obsolete `evals/eval_memory_recall.py`; strip recall instrumentation from compaction/extraction evals.

**Out of scope (handled in D2):**
- `memory_search` summarization pipeline (`_summary.py`, `session_summarizer.md`, retry, timeout, tracing, lineage walk).
- Tool description tuning for new return shape.
- Disambiguation coaching (tie-breaker rule + concrete prompt→tool examples).
- Empty-query browse mode.
- Summarizer test + proactive-trigger eval.

**Out of scope entirely:**
- Per-turn extraction (`fire_and_forget_extraction`) — separate concern, untouched.
- Knowledge artifact schema, storage, or indexing backends — untouched.
- Today's-date relocation strategies (e.g., put it in static and reload daily) — keep it dynamic; it's tiny and one-day-stable.
- Promoting `add_shell_guidance` and `add_category_awareness_prompt` from dynamic to static — separate follow-up.

---

## Phases

### ✓ DONE — Phase 1 — Static-prompt promotion of personality memories

**Goal:** Personality memories move out of the volatile dynamic block into the cache-stable static system prompt.

Tasks:

1. **Locate static-instructions assembly.** Read `co_cli/prompts/_assembly.py::build_static_instructions(config)` and identify the insertion point for personality memories. Personality base (soul mindsets) is already loaded there; memories should follow it as a sibling block.
2. **Move loader call.** Invoke `_load_personality_memories()` from inside `build_static_instructions()` instead of from `recall_prompt_text()`. The loader is already process-cached, so cost is unchanged.
3. **Merge `_injector.py` into `_loader.py` and rename function.** Move `_load_personality_memories()` and the module-level `_personality_cache` into `co_cli/prompts/personalities/_loader.py`. Rename function to `load_personality_memories()` (drop leading underscore — called from sibling package `prompts/_assembly.py`). Delete `co_cli/prompts/personalities/_injector.py` after moving its contents. Update the import in `_assembly.py` accordingly.
4. **Cache invalidation hook.** `invalidate_personality_cache()` (currently in `_injector.py:14`) moves to `_loader.py` along with the cache. After the move, invalidation only matters across `build_agent()` calls (session boundaries). Verify with `grep -rn "invalidate_personality_cache" co_cli/ tests/` — if no callers remain, delete the function. Otherwise keep it and document in its docstring that runtime mutations require a session restart to take effect.
5. **Remove personality block from `recall_prompt_text()`.** Delete the personality-loading branch entirely.

**Acceptance:** With personality memories present, `recall_prompt_text()` returns only the date string. The personality memories appear once in the static system prompt at session start.

### ✓ DONE — Phase 2 — Knowledge auto-injection removal

**Goal:** Eliminate the always-3 knowledge auto-recall path; preserve per-artifact recall telemetry.

Tasks:

1. **Delete the recall block from `recall_prompt_text()`.** Remove the `if user_msg and user_turn_count > state.last_recall_user_turn:` branch and its dependencies.
2. **Delete `_recall_for_context()`.** Lives in `co_cli/tools/knowledge/read.py:68`. Verify no other callers via `grep -rn "_recall_for_context" co_cli/ tests/`. After removal, verify the file still compiles and `knowledge_search` is intact.
3. **Delete `MemoryRecallState`.** Lives in `co_cli/memory/state.py`. Used only by `recall_prompt_text()` for debouncing the auto-recall. After deletion, remove the field from `CoSessionState` (`co_cli/deps.py:106`) and the import in `co_cli/deps.py:21`.
4. **Helper purge in `prompt_text.py`.** `_count_user_turns()` and `_get_last_user_message()` were only used to drive the recall debounce. If unused after Phase 2, delete them.
5. **Rewire `_touch_recalled` into `knowledge_search`.** `_recall_for_context` was the sole caller of `_touch_recalled` (`co_cli/tools/knowledge/helpers.py:27`), which increments per-artifact `recall_count` and stamps `last_recalled` in frontmatter. Preserve the telemetry by calling `_touch_recalled` from `knowledge_search` after an FTS5 hit returns a non-empty `results` list. Wire it the same way `_recall_for_context` does (`asyncio.create_task(_touch_recalled(hit_paths, ctx))` with `add_done_callback(lambda _t: None)`) so the recall return path is not blocked. Apply only on the unified-search path that hits real knowledge artifacts (skip for `kind="article"` index-mode, since article reads have their own continuation flow). Confirm `compute_confidence` / half-life decay logic in `co_cli/knowledge/_ranking.py` still reads the artifact-level `recall_count`/`last_recalled` fields; if not, document the telemetry as informational-only.
6. **Rename `recall_prompt` → `date_prompt`.** In `co_cli/agent/_instructions.py:8` and the registration call at `co_cli/agent/_core.py:156`. The wrapper now carries date only — name should reveal current role.
7. **Audit `injection_max_chars`.** Field is at `co_cli/config/memory.py:22`, env var `CO_MEMORY_INJECTION_MAX_CHARS`. Sole runtime consumer is the deleted auto-injection path. If no other consumer is found in `co_cli/`, remove the field, the default constant, and the env-var entry from the partition map.

**Acceptance:** `recall_prompt_text()` returns `f"Today is {date.today().isoformat()}."` and nothing else (when there are no safety warnings to inject — those live in `safety_prompt_text()`, untouched). No reference to `_recall_for_context` or `MemoryRecallState` remains anywhere. `_touch_recalled` is reachable from `knowledge_search`. Existing `memory_search` (snippet form) and `knowledge_search` tools work unchanged from the agent's perspective.

### ✓ DONE — Phase 3 — Spec sync (D1 slice)

**Goal:** `docs/specs/memory-knowledge.md` reflects the cache fix and the on-demand recall framing. The D2 spec sync (new pipeline description) happens in D2.

Tasks:

1. **Section 2.4 — Knowledge Layer / retrieval paths.** Replace the "Turn-time recall" bullet (which describes the now-deleted auto-injection) with:
   - "Recall is on-demand. Two tools surface persistent context: `knowledge_search` over curated artifacts, `memory_search` over past session transcripts. The agent decides when to call them; existing tool descriptions coach proactive use."
2. **Section 2.4 — Cache rationale callout.** Add a short paragraph: "Personality memories live in the static system prompt to preserve prefix-cache stability across turns. The dynamic-instructions block is intentionally kept to a small volatile suffix (today's date plus conditional safety warnings)."
3. **Section 4 — Files.**
   - Drop `co_cli/memory/state.py` (deleted).
   - Drop `co_cli/prompts/personalities/_injector.py` (merged into `_loader.py`).
   - Update `co_cli/agent/_instructions.py` line: function renamed `recall_prompt()` → `date_prompt()`; description "wraps the per-turn date string".
   - Drop `co_cli/context/prompt_text.py` from the recall-injection role; update its description to "date dynamic instruction".
   - If `injection_max_chars` is removed in Phase 2 task 7, sync Section 3 (Config) accordingly.

**Acceptance:** `/sync-doc memory-knowledge` reports no further D1-relevant inaccuracies. Sections 2.4 and 4 reflect the on-demand recall framing and the cache rationale. (The new `memory_search` summarization pipeline description is deliberately deferred to D2.)

### ✓ DONE — Phase 4 — Test churn (D1 slice)

**Goal:** Remove tests that assert deleted auto-injection behavior; add coverage for the personality-static promotion.

Verified stale references in `tests/`:
- `tests/context/test_history.py:47, 1252-1300` — imports `recall_prompt_text`, has `test_recall_prompt_text_includes_personality_memories` and `test_recall_prompt_text_always_includes_date`.
- `tests/_timeouts.py:84` — comment-only mention of `recall_prompt_text` in a docstring; trivial.

Tasks:

1. **Rewrite `tests/context/test_history.py` recall tests.**
   - `test_recall_prompt_text_includes_personality_memories` → delete; behavior moves to a new `tests/prompts/test_static_instructions.py::test_static_instructions_includes_personality_memories` that builds the agent and asserts personality-memory content appears in the assembled static instruction string (substring, not format).
   - `test_recall_prompt_text_always_includes_date` → rename to `test_dynamic_instruction_is_date_only` and tighten the assertion: the dynamic-instruction string equals the date line (no other content) when no safety warnings are active.
   - Update the `_timeouts.py` docstring comment for accuracy.
2. **Personality static-injection test.** Build agent with personality memories present, assert the resulting static instructions string contains the memory content. Substring presence only, no format assertions.
3. **Drop tests of `injection_max_chars` default value** if Phase 2 task 7 removes the field.

(Summarizer test and proactive-trigger eval are deferred to D2.)

**Acceptance:** Full test suite passes. No test references `MemoryRecallState`, `_recall_for_context`, `last_recall_user_turn`, `recall_count` (session-level), or deleted helpers. New personality-static test is green.

### ✓ DONE — Phase 5 — Eval churn

**Goal:** Remove evals that exercise the deleted auto-injection pipeline. Strip recall instrumentation from evals whose primary subject is something else.

Verified eval impact:
- `evals/eval_memory_recall.py` — entire eval is built on `MemoryRecallState.recall_count` firing through the auto-injection path (lines 5, 11-12, 253). **Action: delete the file.** A replacement eval that exercises proactive `memory_search` triggering is authored in D2 (Phase 4 task 4 of D2's plan).
- `evals/eval_compaction_flow_quality.py:829-845, 1146-1154` — instrumentation reads `memory_recall_state.recall_count` and `last_recall_user_turn` to assert the auto-recall fired before/after compaction. **Action: delete those instrumentation blocks**; the eval's primary subject is compaction quality, not recall, so the rest of the eval remains valid.
- `evals/eval_memory_extraction_flow.py:12, 530-531` — flow comment and one instrumentation block reference `_recall_for_context` and `state.last_recall_user_turn`. **Action: delete the recall-debounce instrumentation lines and update the flow comment** to describe the new on-demand path (extraction → save_memory → DB index → next turn → agent calls `knowledge_search`/`memory_search` proactively when relevant).

Tasks:

1. `git rm evals/eval_memory_recall.py`. Update any eval index/runner that lists it.
2. Delete recall-instrumentation blocks in `eval_compaction_flow_quality.py` and `eval_memory_extraction_flow.py`. Run each eval after edit to confirm the rest of the runner is intact.

**Acceptance:** `grep -rn "MemoryRecallState\|memory_recall_state\|_recall_for_context\|last_recall_user_turn" evals/` returns nothing. Compaction and extraction evals still run end-to-end.

---

## Files affected

| File | Change |
|---|---|
| `co_cli/context/prompt_text.py` | reduce `recall_prompt_text()` to date-only; remove personality + recall branches; delete `_count_user_turns`, `_get_last_user_message` if unused; drop `MemoryRecallState` import |
| `co_cli/agent/_instructions.py` | rename `recall_prompt()` → `date_prompt()` (wrapper now carries date only) |
| `co_cli/agent/_core.py` | update `agent.instructions(recall_prompt)` → `agent.instructions(date_prompt)` at line 156 |
| `co_cli/prompts/_assembly.py` | `build_static_instructions()` calls personality-memories loader and concatenates the block |
| `co_cli/prompts/personalities/_injector.py` | DELETE (merge into `_loader.py`) |
| `co_cli/prompts/personalities/_loader.py` | absorb personality-memories loader; rename function to drop leading underscore |
| `co_cli/tools/knowledge/read.py` | delete `_recall_for_context()`; rewire `_touch_recalled` into `knowledge_search` (post-FTS hit, fire-and-forget) |
| `co_cli/memory/state.py` | DELETE |
| `co_cli/deps.py` | drop `MemoryRecallState` import (line 21) + `memory_recall_state` field on `CoSessionState` (line 106) |
| `co_cli/config/memory.py` | drop `injection_max_chars` field, default constant, and env-var entry if no consumers remain (verify in Phase 2 task 7) |
| `docs/specs/memory-knowledge.md` | sync Sections 2.4 (cache rationale + on-demand recall) and 4 (Files) per Phase 3 |
| `tests/context/test_history.py` | delete personality-recall test; rename + tighten date test |
| `tests/_timeouts.py` | update docstring comment for accuracy |
| `tests/prompts/test_static_instructions.py` | NEW — assert personality memories appear in static instructions |
| `evals/eval_memory_recall.py` | DELETE (replacement out of scope; tracked in D2) |
| `evals/eval_compaction_flow_quality.py` | drop recall-instrumentation blocks at lines 829-845, 1146-1154 |
| `evals/eval_memory_extraction_flow.py` | drop recall-instrumentation lines, update flow comment |

---

## Risks

1. **Behavioral regression: agent stops finding past context.** The agent has been getting recall for free; on-demand requires it to call tools proactively. After D1, both `memory_search` and `knowledge_search` retain their existing schema descriptions which already coach proactive use ("USE THIS PROACTIVELY when..." with hermes-style triggers). Mitigation: production observability — watch tool-call rates after D1 ships. If under-triggering shows up, the D2 disambiguation coaching + proactive-trigger eval will reinforce. As a hot-fix path, add one sentence to `build_static_instructions()` reminding the agent that cross-session context lives behind those two tools.
2. **Personality cache invariant.** Personality memories now load once at agent build time. Users editing personality artifacts mid-session won't see the change until next session. Today's auto-injection wouldn't have caught runtime edits either (process-level cache), so this is no regression — but document it in the spec.
3. **`_touch_recalled` half-life decay regression.** If `compute_confidence` or other ranking logic reads `recall_count`/`last_recalled` from artifact frontmatter, the rewire ensures values keep accruing. Verify in Phase 2 task 5 that the ranking signal still flows. If `_ranking.py` does not consume these fields, the rewire is informational-only telemetry and the cost is negligible.
4. **Test regressions touching `MemoryRecallState`.** Verified before plan finalization: only `tests/context/test_history.py` (two recall tests) and a docstring mention in `tests/_timeouts.py:84` reference deleted symbols — both addressed in Phase 4 task 1. No tests construct `CoSessionState` with explicit `memory_recall_state=...` arguments. Risk is contained.

---

## Open questions / decisions logged

- **Q:** Drop `injection_max_chars` from `MemorySettings`? **A:** Phase 2 task 7 audit — drop only if no other consumers found (likely none).
- **Q:** Rename `recall_prompt()` to `date_prompt()` after the gut? **A:** Yes — name should reveal current role. Done in Phase 2 task 6.
- **Q:** Where does `_load_personality_memories()` end up after the move? **A:** Merge into `co_cli/prompts/personalities/_loader.py`, drop leading underscore on the function.
- **Q:** What happens to `_touch_recalled` when `_recall_for_context` is deleted? **A:** Rewire into `knowledge_search` after FTS5 hits, fire-and-forget. Preserves per-artifact telemetry. Phase 2 task 5.
- **Q:** What about the obsolete `evals/eval_memory_recall.py`? **A:** Delete in Phase 5. Replacement (proactive-trigger eval) is authored in D2.

---

## Delivery Summary — 2026-04-25

| Task | done_when | Status |
|------|-----------|--------|
| Phase 1 | `recall_prompt_text()` returns only date; personality memories appear in static prompt | ✓ pass |
| Phase 2 | No `_recall_for_context` / `MemoryRecallState` references; `_touch_recalled` wired into `knowledge_search` | ✓ pass |
| Phase 3 | `memory-knowledge.md` Sections 2.4 and 4 reflect on-demand recall and cache rationale; expanded to fix `core-loop.md`, `personality.md`, `prompt-assembly.md`, `tui.md` | ✓ pass |
| Phase 4 | 56 tests pass; `test_dynamic_instruction_is_date_only` and `test_static_instructions_includes_personality_memories` green | ✓ pass |
| Phase 5 | `grep -rn "MemoryRecallState\|memory_recall_state\|_recall_for_context\|last_recall_user_turn" evals/` returns nothing; `eval_memory_recall.py` deleted | ✓ pass |

**Tests:** scoped (touched files) — 56 passed, 0 failed (context + prompts tests); 130 passed, 0 failed (knowledge + memory tests)
**Doc Sync:** fixed — `memory-knowledge.md`, `core-loop.md`, `personality.md`, `prompt-assembly.md`, `tui.md`, `_assembly.py` docstring

**Overall: DELIVERED**
All five phases shipped. Cache-poisoning fixed: personality memories moved to static prompt; dynamic-instructions block reduced to date-only suffix. Auto-injection pipeline deleted. `_touch_recalled` rewired into `knowledge_search` fire-and-forget. `MemoryRecallState`, `_injector.py`, `state.py`, and `injection_max_chars` removed cleanly. Stale spec references purged across 5 spec files.

---

## Review verdict

(to be appended by `/review-impl` after delivery)

---

## Implementation Review — 2026-04-25

### Evidence Table

| Task | done_when | Spec Fidelity | Key Evidence (file:line) |
|------|-----------|---------------|--------------------------|
| Phase 1: recall_prompt_text date-only | Returns only `f"Today is {date.today().isoformat()}."` | PASS | `co_cli/context/prompt_text.py:33-40` — function body is a single date return; no personality branch |
| Phase 1: Personality memories in static prompt | `build_static_instructions()` calls `load_personality_memories()` | PASS | `co_cli/prompts/_assembly.py:141-143` — inline `load_personality_memories()` call under personality guard |
| Phase 1: `load_personality_memories()` in `_loader.py` (no leading underscore) | Public function present | PASS | `co_cli/prompts/personalities/_loader.py:40` — `def load_personality_memories()` |
| Phase 1: `_injector.py` deleted | File does not exist | PASS | `ls` confirms no such file at `co_cli/prompts/personalities/_injector.py` |
| Phase 1: `invalidate_personality_cache()` deleted | No reference found in repo | PASS | `grep -rn "invalidate_personality_cache" co_cli/ tests/` — zero hits |
| Phase 2: `_recall_for_context()` deleted | Zero references in source | PASS | `grep -rn "_recall_for_context" co_cli/ tests/ evals/` — zero hits |
| Phase 2: `MemoryRecallState` deleted | Zero references in source | PASS | `grep -rn "MemoryRecallState" co_cli/ tests/ evals/` — zero hits |
| Phase 2: `memory_recall_state` field removed from `CoSessionState` | Field absent | PASS | `co_cli/deps.py:92-114` — `CoSessionState` has no `memory_recall_state` field |
| Phase 2: `date_prompt` renamed from `recall_prompt` | Wrapper renamed, registration updated | PASS | `co_cli/agent/_instructions.py:8` — `async def date_prompt`; `co_cli/agent/_core.py:126,156` — imports and registers `date_prompt` |
| Phase 2: `_touch_recalled` wired into `knowledge_search` fire-and-forget | `asyncio.create_task` call present post-FTS hit | PASS | `co_cli/tools/knowledge/read.py:476-479` — `_recall_task = asyncio.create_task(_touch_recalled(hit_paths, ctx))` with done-callback |
| Phase 2: `injection_max_chars` removed | Not present in `MemorySettings` or env map | PASS | `co_cli/config/memory.py` — only `recall_half_life_days` and `extract_every_n_turns` remain |
| Phase 2: `_count_user_turns` / `_get_last_user_message` deleted | Zero references in `prompt_text.py` | PASS | `grep -n "_count_user_turns\|_get_last_user_message" co_cli/context/prompt_text.py` — zero hits |
| Phase 4: old personality-recall test deleted | `test_recall_prompt_text_includes_personality_memories` absent | PASS | `grep -n "test_recall_prompt_text_includes_personality_memories" tests/context/test_history.py` — zero hits |
| Phase 4: `test_dynamic_instruction_is_date_only` added and tightened | Uses exact equality assertion | PASS | `tests/context/test_history.py:1257-1273` — `assert result == expected` (exact match, not substring) |
| Phase 4: `tests/prompts/test_static_instructions.py` exists with new test | File and test present | PASS | `tests/prompts/test_static_instructions.py:19` — `test_static_instructions_includes_personality_memories` |
| Phase 4: No test references deleted symbols | Zero stale test references | PASS | `grep -rn "MemoryRecallState\|_recall_for_context\|last_recall_user_turn" tests/` — zero hits |
| Phase 5: `evals/eval_memory_recall.py` deleted | File does not exist | PASS | `ls` confirms no such file |
| Phase 5: No recall-state references in evals | Zero hits for deleted symbols | PASS | `grep -rn "MemoryRecallState\|memory_recall_state\|_recall_for_context\|last_recall_user_turn" evals/` — zero hits |
| Phase 5: `_timeouts.py` docstring updated | `date_prompt_text` replaces `recall_prompt_text` | PASS | `tests/_timeouts.py:84` — `date_prompt_text` in `FILE_DB_TIMEOUT_SECS` docstring |

### Issues Found & Fixed

No issues found. All phased requirements verified with file:line evidence.

One observation (non-blocking): the function `recall_prompt_text()` in `co_cli/context/prompt_text.py` retains its original name (the plan only required renaming the wrapper `recall_prompt` → `date_prompt` in `_instructions.py`). The underlying function name is an implementation detail, not part of the rename scope. The module docstring at `prompt_text.py:8` accurately describes it as "returns today's date". The test at `test_history.py:47` imports and exercises it by its current name. No action required.

### Tests Result

**643 passed, 0 failed** — full suite run in 230.59s.
Log: `.pytest-logs/YYYYMMDD-HHMMSS-review-impl.log`

### Doc Sync Result

Specs updated as part of delivery (Phase 3): `memory-knowledge.md`, `core-loop.md`, `personality.md`, `prompt-assembly.md`, `tui.md`. No further inaccuracies detected in the D1-relevant sections during review.

### Behavioral Verification Result

`uv run co config` — system starts healthy:
- LLM: Online (Ollama)
- Shell: Active
- Google: Configured
- MCP Servers: 1 ready
- Database: Active
No errors, no import failures.

### Overall: PASS

All five phases delivered cleanly. Cache-poisoning fix verified: `recall_prompt_text()` returns date-only (`prompt_text.py:40`); personality memories injected in static prompt (`_assembly.py:141-143`). Auto-injection pipeline fully deleted with zero stale references. `_touch_recalled` rewired fire-and-forget into `knowledge_search` (`read.py:476-479`). `MemoryRecallState`, `_injector.py`, `state.py`, and `injection_max_chars` removed with zero remaining references. 643 tests pass. System healthy.
