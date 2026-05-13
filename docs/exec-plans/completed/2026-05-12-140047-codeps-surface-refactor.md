# Plan: CoDeps Surface Refactor

Task type: refactor

---

## Context

`CoDeps` is the runtime dependency carrier threaded through every tool and orchestration layer in the system. It contains three logically distinct tiers: injected service handles, bootstrap-frozen constants, and grouped mutable state (`CoSessionState` and `CoRuntimeState`). The separation between `CoSessionState` (tool-visible) and `CoRuntimeState` (orchestration-internal) is the primary design boundary — it determines what tools are allowed to read/write and what the engine keeps private.

Two misplacements were identified during design review:

1. **`persisted_message_count` sits in `CoSessionState`** despite being used only by `main.py` (the orchestration loop). No tool reads or writes it. It is persistence bookkeeping — tracking how many messages have been flushed to disk so `persist_session_history()` knows where to start the next append. It belongs in `CoRuntimeState`.

2. **`file_read_mtimes` and `file_partial_reads` sit as raw dicts/sets flat on `CoDeps`** rather than in `CoSessionState`. This is an intentional escape hatch to enable reference-sharing across delegation agents (`fork_deps` passes them by reference). However, the mechanism is ad-hoc: bare mutable containers shared by reference alongside the otherwise-immutable service handles. The principled solution is to wrap them in a handle object (`FileReadTracker`) — the same pattern used for `resource_locks: ResourceLockStore` — so that the sharing is explicit and the mutation surface is encapsulated.

**`session_path` stays in `CoSessionState`.** Research confirmed that tools legitimately use it: `recall.py` uses it to exclude the current session from search results; `delegation.py` and `capabilities.py` derive a short session ID (`stem[-8:]`) from it. It is not purely persistence infrastructure — it is the session identity handle.

**`model_max_ctx` and `spill_threshold_tokens`** are bootstrap-frozen scalars used by orchestration and one tool (lifecycle.py OTEL). They are correctly placed as flat fields on `CoDeps` — no change.

**Workflow artifact hygiene:** No stale exec-plans found with this slug.

---

## Problem & Outcome

**Problem:** `CoSessionState` contains `persisted_message_count`, a field owned and used exclusively by the orchestration layer. Tools can read and write it even though they have no business doing so. Separately, `file_read_mtimes` and `file_partial_reads` are raw mutable containers floating directly on `CoDeps` with no encapsulation — their reference-sharing semantics are implicit and the mutation contract is unguarded.

**Failure cost:** A tool that inadvertently zeroes or inflates `persisted_message_count` would cause session persistence to silently corrupt (overwrite already-persisted messages or skip new ones). The wrong surface creates a pit-of-failure — any tool author can reach the field without understanding the invariant. For the file tracker fields, ad-hoc dict access means there is no single place to enforce the staleness-detection contract.

**Outcome:** `persisted_message_count` lives in `CoRuntimeState` where only orchestration code reaches it. `file_read_mtimes` and `file_partial_reads` are replaced by `file_tracker: FileReadTracker` on `CoDeps`, a handle shared by reference like `resource_locks`. The surface boundary between tool-visible and orchestration-internal state is clean and enforced by struct membership.

---

## Scope

**In scope:**
- Move `persisted_message_count` from `CoSessionState` to `CoRuntimeState`; update all call sites in `main.py`
- Introduce `FileReadTracker`; replace the two flat fields on `CoDeps`; update all call sites in `read.py` and `write.py`
- Docstring updates to `CoSessionState`, `CoRuntimeState`, and `fork_deps` that reflect the corrected design

**Out of scope:**
- `session_path` — correctly placed, no change
- `model_max_ctx`, `spill_threshold_tokens` — correctly placed, no change
- `tool_registry` fork semantics — documentation-only concern, bundled into TASK-1 docstring updates
- Any behavior change in session persistence, file read/write, or approval mechanics

---

## Behavioral Constraints

1. All file-write staleness checks must continue to raise the same errors for unread, partial-read, and stale-file conditions — the `FileReadTracker` API must expose each condition separately, not collapse them.
2. `persisted_message_count` in `CoRuntimeState` must NOT be added to `reset_for_turn()` — it is cross-turn state that accumulates across the session; resetting it per-turn would break append-only persistence.
3. `fork_deps` must still reset `persisted_message_count` to 0 for delegation agents (already implicit: `CoRuntimeState` is always constructed fresh in `fork_deps`).
4. `FileReadTracker` must be shared by reference in `fork_deps` — the same instance, not a copy — so cross-agent staleness detection remains intact.
5. No test may be modified to weaken the behavioral assertion it makes. Tests that call `persist_session_history()` directly (passing `persisted_message_count` as a positional argument) are unaffected by the struct move and must not be touched.

---

## High-Level Design

### TASK-1: `persisted_message_count` → `CoRuntimeState`

Remove `persisted_message_count: int = 0` from `CoSessionState`. Add it as a cross-turn field in `CoRuntimeState`, in the same section as `message_count_at_last_compaction` (both track cross-turn message counts for the orchestration layer). It must NOT appear in `reset_for_turn()`.

Update `CoSessionState` docstring: note that `session_path` is the session identity handle — not just a persistence path — explaining why it belongs in the tool-visible surface (tools derive session ID and use it for recall self-exclusion).

Update `CoRuntimeState` docstring: add `persisted_message_count` to the cross-turn list.

Update `fork_deps` docstring: add a note that `tool_registry` is intentionally excluded — delegation agents construct their own registry.

Four call sites in `main.py`:
- Line 92–93: `deps.session.session_path = persist_session_history(session_path=deps.session.session_path, ..., persisted_message_count=deps.session.persisted_message_count, ...)`  
  → `deps.runtime.persisted_message_count` on both the read and the assignment
- Line 98: `deps.session.persisted_message_count = len(turn_result.messages)` → `deps.runtime.persisted_message_count`
- Lines 210–216: same pattern (compaction branch)
- Line 222: same pattern (compaction recovery branch)

### TASK-2: `FileReadTracker` handle

**New class** `FileReadTracker` in `co_cli/tools/file_read_tracker.py` (flat under `tools/`, matching `resource_lock.py` placement):

```python
@dataclass
class FileReadTracker:
    _mtimes: dict[str, float] = field(default_factory=dict)
    _partial_reads: set[str] = field(default_factory=set)

    def record_read(self, path_key: str, mtime: float, partial: bool = False) -> None:
        self._mtimes[path_key] = mtime
        if partial:
            self._partial_reads.add(path_key)
        else:
            self._partial_reads.discard(path_key)

    def is_read(self, path_key: str) -> bool:
        return path_key in self._mtimes

    def is_partial(self, path_key: str) -> bool:
        return path_key in self._partial_reads

    def is_stale(self, path_key: str, current_mtime: float) -> bool:
        # Only meaningful when is_read() is True — caller must check is_read() first.
        return self._mtimes.get(path_key) != current_mtime

    def is_read_and_stale(self, path_key: str, current_mtime: float) -> bool:
        # For guards where the file may never have been read — combines the is_read check.
        return path_key in self._mtimes and self._mtimes[path_key] != current_mtime

    def update_mtime(self, path_key: str, mtime: float) -> None:
        self._mtimes[path_key] = mtime
```

**Two distinct staleness guard patterns in `write.py`:**

- `_check_patch_preconditions` (write.py:257–261): the `is_read` guard fires first on a separate line; `is_stale` is only reached for files that are already known to have been read. Use bare `is_stale` here.
- `file_write` guard (write.py:512–513): a compound `path_key in file_read_mtimes AND mtime mismatch` — allows writes to never-read files but rejects stale ones. Use `is_read_and_stale` here to preserve the exact semantics.

**`deps.py` changes:**
- Remove `file_read_mtimes: dict[str, float]` and `file_partial_reads: set[str]` from `CoDeps`
- Add `file_tracker: FileReadTracker = field(default_factory=FileReadTracker)` (no TYPE_CHECKING guard — it is a concrete type, not a service handle)
- `fork_deps`: replace `file_read_mtimes=base.file_read_mtimes, file_partial_reads=base.file_partial_reads` with `file_tracker=base.file_tracker` (same instance — reference sharing)
- Update `fork_deps` docstring: replace `file_read_mtimes` and `file_partial_reads` entries with `file_tracker`

**`read.py` changes (3 sites, all near line 517–528):**
```python
# Before:
ctx.deps.file_read_mtimes[path_key] = st.st_mtime
if partial:
    ctx.deps.file_partial_reads.add(path_key)
else:
    ctx.deps.file_partial_reads.discard(path_key)

# After:
ctx.deps.file_tracker.record_read(path_key, st.st_mtime, partial=partial)
```

**`write.py` changes (~6 sites):**
```python
# _check_patch_preconditions guards (is_read fires first on its own line):
if not ctx.deps.file_tracker.is_read(path_key):      # was: path_key not in file_read_mtimes
if ctx.deps.file_tracker.is_partial(path_key):       # was: path_key in file_partial_reads
if ctx.deps.file_tracker.is_stale(path_key, safe_mtime(resolved)):   # was: mtime comparison (is_read already confirmed)

# file_write compound guard (allows never-read writes; rejects stale reads):
if ctx.deps.file_tracker.is_read_and_stale(path_key, safe_mtime(resolved)):  # was: compound and-check

# Mtime updates after write:
ctx.deps.file_tracker.update_mtime(path_key, safe_mtime(resolved))   # was: ctx.deps.file_read_mtimes[...] = ...
```

---

## Implementation Plan

### ✓ DONE TASK-1 — Move `persisted_message_count` to `CoRuntimeState`; update docstrings

```
files:
  - co_cli/deps.py
  - co_cli/main.py
  - evals/eval_compaction_proactive.py

done_when:
  uv run pytest tests/ -x passes AND
  grep "persisted_message_count" co_cli/deps.py shows the field under CoRuntimeState class only (not under CoSessionState)

success_signal: N/A
```

Steps:
1. `deps.py`: Remove `persisted_message_count: int = 0` from `CoSessionState`.
2. `deps.py`: Add `persisted_message_count: int = 0` to `CoRuntimeState` in the cross-turn section alongside `message_count_at_last_compaction`. Do NOT add to `reset_for_turn()`.
3. `deps.py`: Update `CoRuntimeState` docstring cross-turn list to include `persisted_message_count`.
4. `deps.py`: Update `CoSessionState` docstring: clarify `session_path` is the session identity handle (tools use `session_path.stem[-8:]` for session ID and `session_path` for recall self-exclusion).
5. `deps.py`: Update `fork_deps` docstring: note `tool_registry` is intentionally excluded — delegation agents construct their own via `build_tool_registry()`.
6. `main.py`: Update all 5 call sites from `deps.session.persisted_message_count` → `deps.runtime.persisted_message_count`. Enumerate explicitly: `main.py:95` (read in `persist_session_history` arg), `main.py:98` (write after turn), `main.py:213` (read in compaction branch), `main.py:216` (write in compaction branch), `main.py:222` (write in else-branch of `if outcome.compaction_applied:` inside `_apply_command_outcome`).
7. `evals/eval_compaction_proactive.py`: Update 2 call sites from `deps.session.persisted_message_count` → `deps.runtime.persisted_message_count`. Enumerate explicitly: `eval_compaction_proactive.py:418` (read in `persist_session_history` arg), `eval_compaction_proactive.py:421` (write after turn).

---

### ✓ DONE TASK-2 — Introduce `FileReadTracker`; remove flat fields from `CoDeps`

```
prerequisites: [TASK-1]

files:
  - co_cli/tools/file_read_tracker.py   (new)
  - co_cli/deps.py
  - co_cli/tools/files/read.py
  - co_cli/tools/files/write.py

done_when:
  uv run pytest tests/ -x passes AND
  grep "file_read_mtimes\|file_partial_reads" co_cli/deps.py co_cli/tools/files/read.py co_cli/tools/files/write.py returns nothing

success_signal: N/A
```

Steps:
1. Create `co_cli/tools/file_read_tracker.py` with the `FileReadTracker` dataclass (API as described in High-Level Design, including `is_read_and_stale`).
2. `deps.py`: Remove `file_read_mtimes` and `file_partial_reads` fields from `CoDeps`. Add `file_tracker: FileReadTracker = field(default_factory=FileReadTracker)`. Import `FileReadTracker` directly (not under TYPE_CHECKING).
3. `deps.py` `fork_deps`: replace the two fields with `file_tracker=base.file_tracker`. Update docstring.
4. `read.py`: Replace 3 access sites with `ctx.deps.file_tracker.record_read(path_key, mtime, partial=...)`.
5. `write.py`: Replace all access sites — use `is_read`, `is_partial`, `is_stale` for `_check_patch_preconditions` guards; use `is_read_and_stale` for the `file_write` compound guard (write.py:512-513); use `update_mtime` for all three mtime write-back sites: `write.py:374` (`_apply_v4a_patch` loop), `write.py:467` (`file_patch` replace mode), `write.py:519` (`file_write`).

---

## Testing

This is a pure refactor — no behavior changes. Verification strategy:

- **Regression gate:** `uv run pytest tests/ -x` must pass after each task. No new tests required.
- **TASK-1 specific:** `test_flow_session_persistence.py` and `test_flow_compaction_session_rewrite.py` call `persist_session_history()` with `persisted_message_count` as a keyword argument — they are unaffected by the struct move and serve as regression coverage for the persistence logic itself.
- **TASK-2 specific:** No existing test exercises the `file_read` → `file_write`/`file_patch` staleness guard path end-to-end. The regression gate (full test suite) does not cover guard-logic correctness for the `FileReadTracker` API. Dev must verify the two distinct write.py guard patterns manually or by inspection after substitution — do not rely on pytest to catch a mis-applied `is_stale` vs `is_read_and_stale`.
- **No test modifications allowed** unless a test was directly constructing `CoSessionState(persisted_message_count=N)` — none found in the codebase.

---

## Open Questions

None. All questions answerable by code inspection were resolved before drafting:
- Is `session_path` tool-visible? Yes — verified in `recall.py:67,247`, `delegation.py:57`, `capabilities.py:99`.
- Are there tests that directly set `persisted_message_count` via `deps.session`? No — tests call `persist_session_history()` directly.
- Are `file_read_mtimes`/`file_partial_reads` accessed in any test? No — all access goes through the tool functions.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev codeps-surface-refactor`

---

## Delivery Summary — 2026-05-12

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | pytest -x passes; `persisted_message_count` in deps.py under CoRuntimeState only | ✓ pass |
| TASK-2 | pytest -x passes; `file_read_mtimes\|file_partial_reads` absent from deps.py, read.py, write.py | ✓ pass |

**Tests:** scoped — 368 passed, 0 failed (run twice: after TASK-1 and after TASK-2)
**Doc Sync:** fixed (`system.md`: `file_read_mtimes` → `file_tracker`, `workspace_root` → `workspace_dir`; `tools.md`: guard diagram updated to `file_tracker.is_partial` / `is_stale` / `is_read_and_stale`)

**Overall: DELIVERED**
All tasks passed done_when, lint clean, tests green both passes, doc sync fixed 2 stale field references.

---

## Implementation Review — 2026-05-12

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | pytest -x passes; `persisted_message_count` under CoRuntimeState only | ✓ pass | deps.py:190 — field in CoRuntimeState cross-turn block; deps.py:114–135 — absent from CoSessionState; main.py:95,98,213,216,222 — all 5 sites migrated; eval_compaction_proactive.py:418,421 — both sites migrated |
| TASK-2 | pytest -x passes; `file_read_mtimes\|file_partial_reads` absent from deps.py, read.py, write.py | ✓ pass | file_read_tracker.py:4–31 — all 6 API methods present with type hints; deps.py:227 — `file_tracker` field with `default_factory`; deps.py:310 — `file_tracker=base.file_tracker` reference sharing; read.py:523 — `record_read` replaces 3-line pattern; write.py:257–261 — `is_read`/`is_partial`/`is_stale` three separate guards; write.py:511 — `is_read_and_stale` compound guard; write.py:374,467,516 — `update_mtime` at all three sites |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `FileReadTracker` implemented as plain class instead of `@dataclass` as specified | file_read_tracker.py:4 | minor | Accepted as intentional improvement — plain class avoids exposing private `_mtimes`/`_partial_reads` in `__init__` signature, which `@dataclass` would require; behavior is identical |
| `file_tracker` field has `repr=False` not in spec | deps.py:227 | minor | Accepted — consistent with every other large/mutable handle on `CoDeps` (`resource_locks`, `memory_store`, `model`, `tool_registry`); no correctness impact |

### Tests
- Command: `uv run pytest -v`
- Result: 368 passed, 0 failed
- Log: `.pytest-logs/20260512-230841-review-impl.log`

### Doc Sync
- Scope: full — tasks touch shared module `CoDeps` and rename public API fields
- Result: clean — `system.md:181` shows `file_tracker`; `tools.md:148,151` shows `file_tracker.is_partial` / `is_stale` / `is_read_and_stale`; no stale references in active specs

### Behavioral Verification
- No user-facing surface changed — `success_signal: N/A` for both tasks
- `uv run co --help`: system imports and starts cleanly
- Runtime invariant check: `CoRuntimeState.persisted_message_count` present, absent from `CoSessionState`; all `FileReadTracker` API contracts verified (`is_read`, `is_partial`, `is_stale`, `is_read_and_stale`, `update_mtime`, `record_read`)

### Overall: PASS
Pure refactor delivered cleanly — struct boundaries correct, API contracts verified, full suite green, doc sync complete.
