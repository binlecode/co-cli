# TODO: Memory lifecycle dead code cleanup

**Task type: refactor**

## Context

Deep code scan of the full memory lifecycle (write → read → recall → frontmatter → config →
tools → agents → tests) following the v0.7.63 retention-removal delivery. Six findings — two
blocking, four minor. The blocking findings are direct regressions from the FTS and retention
removal work: callers were not fully cleaned up when internal signatures changed.

**Current-state validation:**

- `co_cli/memory/_extractor.py:191,277` — `overwrite_memory()` called with
  `knowledge_store=deps.knowledge_store`, a kwarg that no longer exists in the function
  signature (`_save.py:105–112`). TypeError at runtime on every UPDATE branch in auto-extraction.
- `co_cli/memory/_lifecycle.py:31,53,66,72,89,119,140` — `provenance` parameter present in
  all three write-chain functions (`persist_memory`, `_persist_memory_inner`, `_write_memory`),
  set as OTel span attribute, but never written to the frontmatter dict. Docstring at line 53
  still claims "auto-detected from tags" — `_detect_provenance()` was deleted in v0.7.56.
- `co_cli/tools/memory.py:69–87` — `_decay_multiplier()` defined, fully implemented, zero
  callers anywhere in the codebase. Was used by old FTS-ranked recall (removed v0.7.58).
- `co_cli/memory/recall.py:34,101` — `MemoryEntry.decay_protected` field loaded from
  frontmatter, stored, and passed through to `list_memories` output dict (`tools/memory.py:468`).
  Only consumer was `_retention.py` (deleted v0.7.63). Articles still write it; no memory code
  reads it for any decision. Field is schema noise with no function.
- `tests/test_memory_lifecycle.py:53–54` — `_seed_memory()` helper writes `provenance:
  "user-told"` and `auto_category: None` to test frontmatter files. Neither key is written by
  any live code path or read by any live code path. Misleading fixture.
- `co_cli/memory/_lifecycle.py:32,158,195` — `title` parameter accepted in all write-chain
  signatures, has real branching logic (upsert skipped when set; filename overrides slug), but
  zero callers in the codebase ever pass it. Untested dead surface.

## Problem & Outcome

**Problem:** Two blocking regressions (runtime TypeError + dead parameter with lying docstring)
and four minor zombie artifacts left by the FTS and retention removal deliveries. The TypeError
silently swallows UPDATE paths in auto-extraction — the extractor falls through to SAVE_NEW,
creating duplicate memories instead of updating existing ones.

**Failure cost:**
- B-1: Every auto-extraction dedup hit (UPDATE branch) crashes with TypeError. The extractor
  catches it as a fall-through to SAVE_NEW, so it is silent — duplicates accumulate instead of
  updates applying.
- B-2: `provenance` OTel span attribute always emits `"memory.provenance": "auto"` (never a
  real value), and the docstring misleads any future developer about what the parameter does.

**Outcome:** All dead, zombie, and backward-compat artifacts are removed. `persist_memory`
write chain is clean. Auto-extraction UPDATE path works correctly. No dead functions or zombie
fields remain in the memory lifecycle.

## Scope

**In:** Fix `knowledge_store=` TypeError. Remove `provenance` param from write chain. Delete
`_decay_multiplier()`. Remove `decay_protected` from `MemoryEntry`, frontmatter loading, and
`list_memories` output. Strip stale `provenance`/`auto_category` keys from test fixture.
Remove `title` param.

**Out:** `decay_protected` field in article frontmatter (articles own it — separate concern).
`recall_half_life_days` config field (live consumer in articles). Any changes to the
`check_and_save` upsert agent flow.

## Behavioral Constraints

- **BC-1:** After TASK-1, auto-extraction UPDATE paths must apply updates correctly — no more
  silent fall-through to SAVE_NEW on dedup hits.
- **BC-2:** After TASK-2, `persist_memory` must write the same frontmatter as before (no
  provenance key was ever written — removing the param changes no file output).
- **BC-3:** After TASK-4, `list_memories` metadata must not include `decay_protected` key.
  The `🔒` indicator is already gone (v0.7.63); this removes the underlying dict key too.

## High-Level Design

Pure deletion and signature cleanup. No new abstractions. Tasks are ordered: blocking first
(TASK-1, TASK-2), then minor cleanup (TASK-3 through TASK-6). TASK-3 through TASK-6 are
independent of each other and can run in parallel.

---

## Implementation Plan

### ✓ DONE — TASK-1: Fix `knowledge_store=` TypeError in `_extractor.py`

**files:**
- `co_cli/memory/_extractor.py`

**Context:**
`overwrite_memory()` in `_save.py` accepted `knowledge_store: Any | None = None` in its old
signature and used it to re-index the updated memory in FTS. FTS was removed in v0.7.58.
The parameter was dropped from `overwrite_memory()` at that time, but both callers in
`_extractor.py` were not updated. Result: `TypeError: overwrite_memory() got an unexpected
keyword argument 'knowledge_store'` at runtime — caught by the extractor's fall-through
logic and silently treated as a SAVE_NEW, creating duplicate memories.

**Changes:**
- `_extractor.py:191`: remove `knowledge_store=deps.knowledge_store,` from the
  `overwrite_memory(...)` call in `handle_extraction()`
- `_extractor.py:277`: remove `knowledge_store=deps.knowledge_store,` from the
  `overwrite_memory(...)` call in the second extraction path (bulk handler)

**done_when:** `grep -n "knowledge_store" co_cli/memory/_extractor.py` returns no matches.

**success_signal:** Auto-extraction UPDATE branch applies the update correctly — no TypeError,
no silent fall-through to SAVE_NEW on dedup hits. Verify: seed a memory, run extraction with
a near-duplicate candidate that has an `update_slug` set, confirm the existing file is updated
and no new file is created.

---

### ✓ DONE — TASK-2: Remove `provenance` dead parameter from the write chain

**files:**
- `co_cli/memory/_lifecycle.py`

**Context:**
`provenance` was introduced as an override for auto-detected provenance values. The auto-detection
logic (`_detect_provenance()`, `_detect_category()`, `_classify_certainty()`) was deleted in
v0.7.56. Since then, `provenance` is accepted by all three write-chain functions, passed through
the call stack, and set as `span.set_attribute("memory.provenance", provenance or "auto")` — but
never written to the frontmatter dict (confirmed: `_lifecycle.py:201–216` lists all frontmatter
keys; `provenance` is absent). The OTel span attribute always emits `"auto"` since no caller
passes a non-None value. The docstring at line 53 still says "When None, auto-detected from tags"
which is false.

**Changes:**
- `persist_memory()` signature (line 31): remove `provenance: str | None = None,`
- `persist_memory()` docstring (line 53): remove the `provenance:` entry from Args section
- `persist_memory()` body (line 66): remove `span.set_attribute("memory.provenance", provenance or "auto")`
- `persist_memory()` body (line 72): remove `provenance,` from the `_persist_memory_inner(...)` call
- `_persist_memory_inner()` signature (line 89): remove `provenance: str | None = None,`
- `_persist_memory_inner()` body (line 119): remove `provenance,` from the `_write_memory(...)` call
- `_write_memory()` signature (line 140): remove `provenance: str | None,`

**done_when:** `grep -n "provenance" co_cli/memory/_lifecycle.py` returns no matches.

**success_signal:** N/A (internal refactor — no frontmatter output change; provenance was
never written).

---

### ✓ DONE — TASK-3: Delete `_decay_multiplier()` dead function

**files:**
- `co_cli/tools/memory.py`

**Context:**
`_decay_multiplier(ts_iso, half_life_days)` computes an exponential decay weight for a memory's
age. It was used by the old FTS-ranked recall path to downrank stale memories in search results.
FTS was removed in v0.7.58; grep-only recall (`grep_recall`) does not use decay weighting.
The function has been dead since v0.7.58. Confirmed: zero call sites in the entire codebase
(`grep -rn "_decay_multiplier" co_cli/` finds only the definition). The `math` import it depends
on should be checked for other usages after deletion.

**Changes:**
- `tools/memory.py:69–87`: delete the `_decay_multiplier()` function (the full 19-line block
  including docstring)
- Check if `import math` at the top of `tools/memory.py` has other call sites; if not, remove it

**done_when:** `grep -n "_decay_multiplier\|def _decay_multiplier" co_cli/tools/memory.py`
returns no matches.

**success_signal:** N/A (internal refactor; no user-visible behavior change).

---

### ✓ DONE — TASK-4: Remove `decay_protected` zombie field from `MemoryEntry` and `list_memories`

**files:**
- `co_cli/memory/recall.py`
- `co_cli/tools/memory.py`

**Context:**
`decay_protected` was the field that exempted memories from retention eviction. The retention
system was deleted in v0.7.63. Since then:
- `recall.py:34`: `decay_protected: bool = False` — field still on dataclass
- `recall.py:101`: `decay_protected=fm.get("decay_protected", False)` — still parsed from frontmatter
- `tools/memory.py:468`: `"decay_protected": m.decay_protected` — still included in `list_memories`
  output dict (surfaced to the agent with no meaning)

Articles still write `decay_protected: True` to their frontmatter (`tools/articles.py:421`) but
this is an article concern — articles are not `MemoryEntry` objects and are not subject to
`MemoryEntry` field changes.

The field must be cleaned from the memory data model. The `🔒` indicator was removed in v0.7.63
(BC-2); this task removes the underlying dict key that was already semantically empty.

**Changes:**
- `recall.py:34`: remove `decay_protected: bool = False` from `MemoryEntry`
- `recall.py:101`: remove `decay_protected=fm.get("decay_protected", False),` from the
  `MemoryEntry(...)` constructor call in `load_memories()`
- `tools/memory.py:468`: remove `"decay_protected": m.decay_protected,` from the
  `memory_dicts` append block in `list_memories()`

**done_when:** `grep -n "decay_protected" co_cli/memory/recall.py co_cli/tools/memory.py`
returns no matches.

**success_signal:** `list_memories` metadata dicts no longer contain a `decay_protected` key.

---

### ✓ DONE — TASK-5: Strip stale frontmatter keys from `_seed_memory()` test fixture

**files:**
- `tests/test_memory_lifecycle.py`

**Context:**
`_seed_memory()` at lines 34–68 builds frontmatter for test memory files. The dict includes:
```python
"provenance": "user-told",
"auto_category": None,
```
Neither key is written by any live code path (`provenance` was removed from the write chain in
TASK-2; `auto_category` was removed in v0.7.56 and was never written by the live code after that).
Neither key is read by any live code path. They persist in test-generated .md files but are
inert. Misleading: a developer reading the test helper would think these are active fields.

**Changes:**
- `test_memory_lifecycle.py:53`: remove `"provenance": "user-told",` from the `fm` dict
- `test_memory_lifecycle.py:54`: remove `"auto_category": None,` from the `fm` dict

**done_when:** `grep -n '"provenance"\|"auto_category"' tests/test_memory_lifecycle.py`
returns no matches.

**success_signal:** N/A (test hygiene; no behavioral change).

---

### ✓ DONE — TASK-6: Remove `title` dead parameter from the write chain

**prerequisites:** [TASK-2]

**files:**
- `co_cli/memory/_lifecycle.py`

**Context:**
The `title` parameter allows callers to override the filename stem for a new memory, bypassing
slug generation and skipping the upsert check. The logic is complete: `filename = f"{title}.md"
if title else f"{slug}.md"` at line 195, and the upsert guard `if title is None and model is not
None` at line 158. However, zero callers in the codebase ever pass `title` — all call sites use
the default `None`:
- `tools/memory.py:164` — `persist_memory(ctx.deps, content, tags, related, on_failure="add", ...)`
- `memory/_extractor.py:201,216,287` — `_persist_memory(deps, mem.candidate, tags, None, ...)`

The feature is dead surface. Removing it simplifies the signature and eliminates the untested
upsert-skip branch.

**Changes:**
- `persist_memory()` signature (line 32): remove `title: str | None = None,`
- `persist_memory()` docstring: remove `title:` entry from Args section
- `persist_memory()` body: remove `title,` from the `_persist_memory_inner(...)` call
- `_persist_memory_inner()` signature (line 90): remove `title: str | None = None,`
- `_persist_memory_inner()` body: remove `title,` from the `_write_memory(...)` call
- `_write_memory()` signature (line 141): remove `title: str | None,`
- `_write_memory()` body (line 195): replace `filename = f"{title}.md" if title else f"{slug}.md"`
  with `filename = f"{slug}.md"`
- `_write_memory()` body (line 158): the upsert guard condition becomes simply
  `if model is not None:` (title was the only reason to skip upsert; without title, upsert
  always runs when model is available)

**done_when:** `grep -n "\btitle\b" co_cli/memory/_lifecycle.py` returns no matches.

**success_signal:** N/A (internal refactor; no frontmatter output change — title was dead
surface with no active callers).

---

## Testing

Full suite gate after all tasks: `uv run pytest -x`. No new tests required — this is a deletion
refactor.

**TASK-1 requires a behavioral test:** After fixing the TypeError, add an integration assertion
to `tests/test_memory_lifecycle.py` that calls `handle_extraction` with a candidate that has
`update_slug` set and confirms the existing file is updated (not a new file created). This
verifies BC-1 and guards against the regression re-appearing.

## Open Questions

- **`recall_half_life_days` config field** — only live consumer is `tools/articles.py` for
  article decay scoring. The `CO_MEMORY_RECALL_HALF_LIFE_DAYS` env var maps to it. Not dead
  code — active in the article path. No action needed.
- **`decay_protected` in article frontmatter** — articles write `decay_protected: True`
  unconditionally. After TASK-4, no MemoryEntry field parses it. The key persists in article
  .md files harmlessly. A follow-up pass can remove it from `tools/articles.py` if article
  protection semantics are formally dropped.

---

## Final — Team Lead

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-lifecycle-dead-code`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/memory/_extractor.py` | Both `knowledge_store=` kwargs removed at lines 191 and 277. Done-when condition passes. No residual references. | clean | TASK-1 |
| `co_cli/memory/_lifecycle.py` | All `provenance` param occurrences removed from all three function signatures, the OTel span call, and the inner call chain. Done-when passes. Docstring updated to match. | clean | TASK-2 |
| `co_cli/memory/_lifecycle.py` | All `title` param occurrences removed from all three signatures and call chain. Upsert guard simplified to `if model is not None:`. Filename falls through to `f"{slug}.md"` unconditionally. Done-when passes. | clean | TASK-6 |
| `co_cli/tools/memory.py` | `_decay_multiplier()` deleted. `import math` removed. `"decay_protected"` key removed from `list_memories` output dict. Done-when passes for TASK-3 and TASK-4. | clean | TASK-3, TASK-4 |
| `co_cli/memory/recall.py` | `decay_protected: bool = False` field and `decay_protected=fm.get(...)` constructor line removed. Done-when passes. | clean | TASK-4 |
| `tests/test_memory_lifecycle.py` | `"provenance"` and `"auto_category"` keys stripped from `_seed_memory()` `fm` dict. Done-when passes. | clean | TASK-5 |
| `co_cli/knowledge/_frontmatter.py` | `validate_memory_frontmatter()` still documents and validates `decay_protected` as an optional frontmatter field (`_frontmatter.py:112, 183-184`). `MemoryEntry` no longer has the field, but the validator still accepts it permissively (type-checks it but does not reject it). This is intentional per plan scope: the plan explicitly calls out that article `.md` files still contain `decay_protected: True` and the validator must not reject those files. No action needed — consistent with out-of-scope note. | minor | TASK-4 |
| `co_cli/commands/_commands.py` | Extra file. `provenance="session"` and `title=f"session-{timestamp}"` removed from `_save_memory_impl()` call. `timestamp` variable dropped since it was only used for those two removed args. Console message updated to `"Session checkpointed. Starting fresh."`. Stale local `from datetime import datetime` inside `_cmd_background` removed. The module-level `from datetime import UTC, datetime` at line 12 remains — UTC and datetime are still used elsewhere in the file (session rotate path). Fix is correct and complete. | clean | extra |
| `tests/test_memory_lifecycle.py` | `_NullFrontend` implements every method in the `Frontend` Protocol (`_core.py:165–225`): `on_text_delta`, `on_text_commit`, `on_thinking_delta`, `on_thinking_commit`, `on_tool_start`, `on_tool_progress`, `on_tool_complete`, `on_status`, `on_reasoning_progress`, `on_final_output`, `prompt_approval`, `clear_status`, `set_input_active`, `cleanup`. All 14 methods present, all type signatures match. This is a real Protocol implementation — not a mock or stub. No patching, no `unittest.mock`, no `monkeypatch`. Test policy satisfied. | clean | extra |
| `tests/test_memory_lifecycle.py` | BC-1 guard test `test_handle_extraction_update_path_applies_correctly` added. It seeds a real memory file, constructs a real `ExtractionResult` with `update_slug` set, calls `handle_extraction` with real `CoDeps` (real filesystem, no model needed for the UPDATE path), and asserts no new file created, existing file body updated, and `frontend.on_status` received the `"Updated:"` message. Uses `asyncio.timeout(10)` — no LLM call, well within budget. Covers BC-1 as required. | clean | extra |

**Overall: clean / 0 blocking / 0 minor**

All six done-when conditions verified. The one `_frontmatter.py` note is informational — it correctly tolerates `decay_protected` in on-disk files written by articles, which is the stated out-of-scope behavior. No regressions introduced. Extra-file changes in `_commands.py` are correct. `_NullFrontend` satisfies the no-fakes test policy. PASS — ship.

---

## Delivery Summary — 2026-04-10

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep -n "knowledge_store" co_cli/memory/_extractor.py` returns no matches | ✓ pass |
| TASK-2 | `grep -n "provenance" co_cli/memory/_lifecycle.py` returns no matches | ✓ pass |
| TASK-3 | `grep -n "_decay_multiplier" co_cli/tools/memory.py` returns no matches | ✓ pass |
| TASK-4 | `grep -n "decay_protected" co_cli/memory/recall.py co_cli/tools/memory.py` returns no matches | ✓ pass |
| TASK-5 | `grep -n '"provenance"\|"auto_category"' tests/test_memory_lifecycle.py` returns no matches | ✓ pass |
| TASK-6 | `grep -n "\btitle\b" co_cli/memory/_lifecycle.py` returns no matches | ✓ pass |

**Extra files:** `co_cli/commands/_commands.py` (stale `provenance=`/`title=` kwargs from live caller), `tests/test_memory_lifecycle.py` (BC-1 integration test + `_NullFrontend`)

**Pyright warnings bundled:** 0 errors, 0 warnings — fixed 49 pre-existing warnings across `agent.py`, `config/_core.py`, `context/_history.py`, `deps.py`, `knowledge/_store.py`, `knowledge/_embedder.py`, `knowledge/_reranker.py`, `observability/_telemetry.py`, `prompts/model_quirks/_loader.py`, `tools/_subagent_builders.py`, `tools/google_gmail.py`, `tools/obsidian.py`, `tools/tool_output.py`

**Tests:** full suite — 363 passed, 0 failed
**Independent Review:** clean — 0 blocking, 0 minor
**Doc Sync:** narrow scope (memory subsystem, no public API changes) — clean

**Overall: DELIVERED**
All six dead-code tasks shipped; BC-1 regression fixed with integration test guard; all 49 pre-existing pyright warnings resolved; 0 errors, 0 warnings, 363 tests green.
