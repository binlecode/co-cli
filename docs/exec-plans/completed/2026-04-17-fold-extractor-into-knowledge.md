# Plan: Fold Extractor into Knowledge Package (Phase 3)

**Task type: refactor (mechanical move)**

## Context

**Prerequisite:** Phase 2 (`rename-memory-tools-to-knowledge`) is DELIVERED as of 2026-04-17. After Phase 2, `co_cli/memory/` contains exactly three artifacts:
- `__init__.py` (one-line docstring)
- `_extractor.py` (207 lines — the post-turn knowledge-extraction engine)
- `prompts/knowledge_extractor.md` (extraction prompt)

`co_cli/tools/memory.py` no longer imports from `co_cli.memory`. The only remaining imports into `co_cli.memory._extractor` are:
- `co_cli/knowledge/_dream.py:39` — `_tag_messages`
- `co_cli/main.py:108` — `fire_and_forget_extraction`
- `co_cli/main.py:183` — `drain_pending_extraction`
- `tests/test_extractor_integration.py:19` — `_run_extraction_async`
- `tests/test_extractor_window.py:18` — several symbols
- `evals/eval_memory_extraction_flow.py:52` — `drain_pending_extraction`, `fire_and_forget_extraction`

**Why this is Phase 3:** The extractor operates exclusively on `KnowledgeArtifact` objects stored in `co_cli/knowledge/`. Its physical home is `co_cli/memory/` only for historical reasons. Moving it completes the module boundary cleanup without changing any tool schema, LLM behavior, or user-visible interface.

## Problem & Outcome

**Problem:** `co_cli/memory/` now exists solely to house a file that belongs in `co_cli/knowledge/`. The package name misleads contributors — the extractor writes knowledge artifacts, not episodic memories. Any future work touching the extraction pipeline must know to look in `co_cli/memory/`, which is counterintuitive after Phase 2.

**Outcome:** After this refactor:
- `co_cli/memory/` is deleted entirely.
- `co_cli/knowledge/_extractor.py` is the canonical home for post-turn extraction logic.
- `co_cli/knowledge/prompts/knowledge_extractor.md` is the canonical home for the extraction prompt.
- All six import sites updated to `from co_cli.knowledge._extractor import ...`.
- `co_cli/tools/memory.py` remains — it still contains `search_memories` (the genuine memory tool) and is correctly named.

## Scope

**In scope:**
- `git mv co_cli/memory/_extractor.py co_cli/knowledge/_extractor.py`
- `git mv co_cli/memory/prompts/knowledge_extractor.md co_cli/knowledge/prompts/knowledge_extractor.md`
- Delete `co_cli/memory/prompts/` (now empty), `co_cli/memory/__init__.py`, `co_cli/memory/` (now empty).
- Update 6 import sites to `from co_cli.knowledge._extractor import ...`:
  - `co_cli/knowledge/_dream.py`
  - `co_cli/main.py` (×2)
  - `tests/test_extractor_integration.py`
  - `tests/test_extractor_window.py`
  - `evals/eval_memory_extraction_flow.py`
- Full grep audit: `grep -rn "co_cli.memory\|co_cli/memory" co_cli/ tests/ evals/ docs/specs/` must return zero matches after the move (except `tools/memory.py` own module docstring, which is fine).
- Sync affected specs after delivery (`cognition.md`, `tools.md`).

**Out of scope:**
- Renaming any function inside `_extractor.py`.
- Changing `co_cli/tools/memory.py` (it is correctly placed and named).
- Renaming `co_cli/knowledge/_extractor.py` to `extractor.py` (visibility cleanup is a separate concern).
- Any changes to `_dream.py` logic or `main.py` logic beyond the import line.

## Behavioral Constraints

- **No behavior change.** This is a file move with import updates. Signatures, logic, side effects are untouched.
- **No schema change.** No tools are renamed or re-registered.
- **Spec sync is an output, not a task.** `sync-doc` runs after delivery and updates `cognition.md` / `tools.md` in-place.

## Regression Surface

- **6 import sites** (listed above) — mechanical update, no logic change.
- **Prompt path**: `_extractor.py` loads `knowledge_extractor.md` at runtime via a path relative to itself (`Path(__file__).parent / "prompts" / "knowledge_extractor.md"`). After the move the relative path resolves correctly — verify with a smoke test.
- **`prompts/` directory**: `co_cli/knowledge/prompts/` already exists (contains `dream_merge.md`, `dream_miner.md`). The move is a simple file addition.

## Implementation Plan

### TASK-1 — Move `_extractor.py` and its prompt, update all imports

**files:**
- `co_cli/memory/_extractor.py` → `co_cli/knowledge/_extractor.py` (move)
- `co_cli/memory/prompts/knowledge_extractor.md` → `co_cli/knowledge/prompts/knowledge_extractor.md` (move)
- `co_cli/knowledge/_dream.py` (import update)
- `co_cli/main.py` (import updates ×2)
- `tests/test_extractor_integration.py` (import update)
- `tests/test_extractor_window.py` (import update)
- `evals/eval_memory_extraction_flow.py` (import update)

Steps:
1. `git mv co_cli/memory/_extractor.py co_cli/knowledge/_extractor.py`
2. `git mv co_cli/memory/prompts/knowledge_extractor.md co_cli/knowledge/prompts/knowledge_extractor.md`
3. Update all 6 import sites from `co_cli.memory._extractor` → `co_cli.knowledge._extractor`.
4. Delete `co_cli/memory/prompts/` (empty), `co_cli/memory/__init__.py`, `co_cli/memory/` directory.
5. Verify the prompt path inside `_extractor.py` resolves correctly under the new location (check the `Path(__file__).parent` logic).

**done_when:** `grep -rn "co_cli\.memory\._extractor\|co_cli/memory/_extractor\|from co_cli\.memory" co_cli/ tests/ evals/` returns zero matches AND `uv run python -c "from co_cli.knowledge._extractor import fire_and_forget_extraction; print('ok')"` exits 0.

**success_signal:** N/A (mechanical — no behavioral surface change).

**prerequisites:** []

---

### TASK-2 — Full test suite + prompt-path smoke test

**files:** none (verification only)

1. Run: `mkdir -p .pytest-logs && uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-fold-extractor.log`
2. Smoke-test the prompt path: `uv run python -c "from co_cli.knowledge._extractor import _load_extraction_prompt; print(_load_extraction_prompt()[:80])"` (adjust function/accessor name to match actual code — the goal is to prove the prompt file loads from its new location).

**done_when:** `uv run pytest` exits 0 AND the prompt-path smoke test loads at least 1 byte without error.

**prerequisites:** [TASK-1]

---

## Testing

- **Full suite:** `uv run pytest` exits 0.
- **Import audit:** `grep -rn "co_cli\.memory\._extractor\|co_cli/memory" co_cli/ tests/ evals/` returns zero matches.
- **Directory audit:** `ls co_cli/memory/` returns "No such file or directory".
- **Prompt path:** prompt file loads without `FileNotFoundError` from its new location under `co_cli/knowledge/prompts/`.
