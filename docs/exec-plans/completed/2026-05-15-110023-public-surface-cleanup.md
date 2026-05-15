# public-surface-cleanup

## Problem

A set of visibility/public-surface anti-patterns surfaced during the `## 4. Public Interface` spec pass. Each is a place where the codebase's public surface doesn't match its on-disk visibility hints (`_` prefix = package-private per CLAUDE.md). Three are genuine cross-package leaks requiring code fixes; one is a naming ambiguity requiring a module docstring clarification.

## Status

Open — ready for `/orchestrate-dev public-surface-cleanup`.

## Tasks

### ✓ DONE T1 — Rename `co_cli/skills/_lint.py` → `co_cli/skills/lint.py`

**Finding:** `co_cli/commands/skills.py:13` imports `lint_skill` from the private module `co_cli/skills/_lint.py`. Cross-package import of a `_prefix.py` module violates the visibility rule. Fix: drop the underscore.

**Files:**
- `co_cli/skills/_lint.py` → `co_cli/skills/lint.py` (git mv)
- `co_cli/commands/skills.py:13` — `from co_cli.skills._lint import lint_skill` → `from co_cli.skills.lint import lint_skill`
- `tests/test_flow_skill_bundled_library.py:10` — update import
- `tests/test_flow_skill_lint.py:1,5` — update docstring + import

**Done when:** `grep -rn "skills._lint\b" co_cli tests` returns nothing; `uv run pytest tests/test_flow_skill_lint.py` passes.

---

### ✓ DONE T2 — Route `commands/resume.py` through `compaction.py`'s public re-export

**Finding:** `co_cli/commands/resume.py:8` imports `TODO_SNAPSHOT_PREFIX` directly from `co_cli/context/_compaction_markers.py`. `co_cli/context/compaction.py` already re-exports this symbol (lines 39–66) — `resume.py` reaches around the public facade.

**Files:**
- `co_cli/commands/resume.py:8` — `from co_cli.context._compaction_markers import TODO_SNAPSHOT_PREFIX` → `from co_cli.context.compaction import TODO_SNAPSHOT_PREFIX`
- `tests/test_flow_session_persistence.py:34` — same redirect (test should consume public surface)
- Leave `tests/test_flow_compaction_todo_format.py:10` unchanged — it tests the marker module directly, so the private import is correct there.

**Done when:** `grep -rn "_compaction_markers" co_cli/commands` returns nothing; `tests/test_flow_session_persistence.py` rehydration tests still pass.

---

### ✓ DONE T3 — Make `run_dream_cycle.miner_tool` positional-required

**Finding:** `co_cli/memory/dream.py:444` declares `miner_tool` as required-keyword-only (after `*`). Required kw-only parameters are rare in this codebase and a stumble for new callers; `miner_tool` has no meaningful default so making it positional removes the required-kwonly footgun.

**Current signature:**
```python
async def run_dream_cycle(
    deps: CoDeps,
    dry_run: bool = False,
    *,
    miner_tool: Any,
    timeout_secs: float = _DREAM_CYCLE_TIMEOUT_SECS,
) -> DreamResult:
```

**New signature:**
```python
async def run_dream_cycle(
    deps: CoDeps,
    miner_tool: Any,
    dry_run: bool = False,
    *,
    timeout_secs: float = _DREAM_CYCLE_TIMEOUT_SECS,
) -> DreamResult:
```

**Files:**
- `co_cli/memory/dream.py:444` — update signature as above
- `co_cli/main.py:268` — `await run_dream_cycle(deps, miner_tool=knowledge_manage)` → `await run_dream_cycle(deps, knowledge_manage)`
- `co_cli/commands/knowledge.py:139` — `await run_dream_cycle(ctx.deps, dry_run=dry_run, miner_tool=knowledge_manage)` → `await run_dream_cycle(ctx.deps, knowledge_manage, dry_run=dry_run)`
- Search `tests/` and `evals/` for `run_dream_cycle(` and update any remaining call sites

**Done when:** `grep -rnE "run_dream_cycle\(" co_cli tests evals` shows all callers use positional `miner_tool`; `tests/memory/test_knowledge_dream_cycle.py` passes.

---

### ✓ DONE T4 — Document `SkillConfig` vs `SkillsSettings` in the module docstring

**Finding:** `co_cli/skills/skill_types.py` defines `SkillConfig` (frozen dataclass — a loaded-skill record). `co_cli/config/skills.py` defines `SkillsSettings` (pydantic `BaseModel` — config/settings). The similar names invite confusion; a module docstring clarification prevents future misattribution. Renaming is out of scope — it touches every importer for marginal gain.

**Files:**
- `co_cli/skills/skill_types.py` — extend the module docstring (~2 lines) to clarify that `SkillConfig` is a loaded-skill record, not a settings model, and point to `SkillsSettings` in `co_cli/config/skills.py` for config.

**Done when:** the module docstring explains the contrast; no other code touched.

## Out of Scope

**Spec-only fixes (handled by `/sync-doc` after this plan ships):**
- `tui.md` §4: replace phantom `_build_completer_words` with `build_completer_entries` + `refresh_completer`
- `core-loop.md` §4: drop `_collect_deferred_tool_approvals` (module-private; only called inside `co_cli/context/orchestrate.py`)
- `personality.md` §4: drop `_sync_canon_store` (module-private; only called inside `co_cli/bootstrap/core.py:386`)
- `self-planning.md` §4: drop `_gather_session_todos` and `_rehydrate_todos` (module-private); public surface is `gather_compaction_context`
- `tools.md` §4: drop `_delegate_agent` / `_run_agent_attempt` (internal helpers; only called inside `co_cli/tools/agents/delegation.py`)

**Retracted findings (never implement):**
- `_build_completer_words` rename — symbol does not exist; phantom spec citation
- `_sync_canon_store` rename — correctly module-private
- `parse_session_filename` tuple reorder — current `(uuid8, created_at)` order matches consumer usage

## Acceptance Criteria

- `grep -rn "skills._lint\b" co_cli tests` returns nothing (T1)
- `grep -rn "_compaction_markers" co_cli/commands` returns nothing (T2)
- `run_dream_cycle` signature is `(deps, miner_tool, dry_run=False, *, timeout_secs=...)` (T3)
- `co_cli/skills/skill_types.py` docstring distinguishes `SkillConfig` from `SkillsSettings` (T4)
- `scripts/quality-gate.sh full` passes

## References

- CLAUDE.md visibility rule: `_prefix.py` modules are package-private; if imported outside the package, drop the underscore.
- `agent_docs/spec-conventions.md` §4 Public Interface — definition of what belongs in a spec's public-interface table.
- `agent_docs/code-conventions.md` — naming conventions.

## Delivery Summary — 2026-05-15

| Task | done_when | Status |
|------|-----------|--------|
| T1 | `grep -rn "skills._lint\b" co_cli tests` returns nothing; `test_flow_skill_lint.py` passes | ✓ pass |
| T2 | `grep -rn "_compaction_markers" co_cli/commands` returns nothing; rehydration tests pass | ✓ pass |
| T3 | all callers use positional `miner_tool`; `tests/memory/test_knowledge_dream_cycle.py` passes | ✓ pass (note: `test_knowledge_dream_cycle.py` does not exist — Dev-2 ran full memory/knowledge flow tests: 31/31 passed) |
| T4 | module docstring explains contrast | ✓ pass |

**Tests:** scoped — 29 passed (T1: 23, T2: 6 via session_persistence, shared total)
**Doc Sync:** fixed — `docs/specs/dream.md` §4 `run_dream_cycle` signature updated (miner_tool positional)

**Overall: DELIVERED**
All four visibility/surface fixes applied; lint clean; scoped tests green; doc sync clean.

## Implementation Review — 2026-05-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | `grep -rn "skills._lint\b"` returns nothing; `test_flow_skill_lint.py` passes | ✓ pass | `co_cli/skills/lint.py` exists (145 lines); `commands/skills.py:14` imports `from co_cli.skills.lint`; grep returns nothing; 15 tests green in 0.03s |
| T2 | `grep -rn "_compaction_markers" co_cli/commands` returns nothing; rehydration tests pass | ✓ pass | `resume.py:8` imports `from co_cli.context.compaction import TODO_SNAPSHOT_PREFIX`; `compaction.py:39-65` re-exports via `__all__`; grep returns nothing; 6 tests green |
| T3 | all callers use positional `miner_tool`; memory/knowledge flow tests pass | ✓ pass | `dream.py:444-450` signature `(deps, miner_tool, dry_run=False, *, timeout_secs=...)`; `main.py:268` and `knowledge.py:139` both positional; `grep miner_tool=` returns nothing |
| T4 | module docstring explains contrast | ✓ pass | `skill_types.py:1-6` — docstring states `SkillConfig` is frozen dataclass for loaded-skill records, not a settings model; points to `SkillsSettings` in `co_cli.config.skills` |

### Issues Found & Fixed

No issues found. All adversarial checks confirmed PASS. Undeclared diff files (`session_review.py`, `config/skills.py`, `orchestrate.py`, `deps.py`, `test_flow_skill_protocol.py`) belong to plan 3.5c — co-mingled staging, not scope creep.

### Tests
- Command: `uv run pytest -v`
- First run: 1 failure — `test_thrash_counter_not_incremented_for_reported_driven_compaction` timed out at 60.001s (Ollama model cold under full-suite GPU load; `ensure_ollama_warm` module state marked warm from earlier test but model evicted between tests). Root cause: infrastructure, not delivery. Confirmed non-deterministic: re-ran isolated → 3.89s PASS.
- Second run (model warm): **404 passed, 0 failed**
- Log: `.pytest-logs/20260515-141703-review-impl-2.log`

### Behavioral Verification
- `uv run co --help`: ✓ system starts cleanly, all commands registered
- Import smoke check: `lint_skill`, `TODO_SNAPSHOT_PREFIX`, `run_dream_cycle`, `SkillConfig` all resolve; `run_dream_cycle` params confirmed `['deps', 'miner_tool', 'dry_run', 'timeout_secs']`
- No user-facing behavior changed by T1–T4 (all changes are internal import routing, signature shape, and docstring) — full behavioral verification not required.

### Overall: PASS
All 4 tasks implemented correctly, lint clean, 404/404 tests green, public surface verified.
