# Plan: Move Distiller Extraction Task to deps.runtime

Task type: code-refactor

## Context

`co_cli/knowledge/_distiller.py:127` declares `_in_flight: asyncio.Task[None] | None = None`
at module scope. Three functions read/write the slot:
- `fire_and_forget_extraction` (`_distiller.py:180-204`) — sets the slot.
- `_on_extraction_done` (`_distiller.py:170-177`) — clears via `global _in_flight`.
- `drain_pending_extraction` (`_distiller.py:207-225`) — awaits, then clears.

`extract_at_compaction_boundary` (`_distiller.py:228-261`) deliberately bypasses the slot —
it drains first and then awaits `_run_extraction_async` directly, on the assumption that
compaction-boundary extraction must run inline regardless of cadence state.

Callers:
- Foreground cadence: `main.py:124` (`fire_and_forget_extraction`), `main.py:180`
  (`drain_pending_extraction`).
- Compaction boundaries: `compaction.py:202`, `compaction.py:286`
  (`extract_at_compaction_boundary`).
- Tests: `tests/knowledge/test_distiller_window.py:192-194,325-327`.
- Evals: `evals/eval_memory_extraction_flow.py:189-205,389-411`.

Sub-agents (`agent/_core.py:162-174`) do not register `summarize_history_window` as a
history processor, so they never reach the compaction-boundary path. The cadence path is
foreground-only. So today's call graph is sequential — the module-level slot works
correctly for current callers.

## Problem & Outcome

**Problem:** Process-global mutable state on a module imported once. The CLAUDE.md tool-state
rule names the same hazard for tool modules; the rationale ("imported once, shared across
all runs in the same process") applies equally here. Concrete consequences:

1. *Cross-loop bleed in tests.* Each pytest-asyncio test runs in its own event loop. A task
   created in loop A but not drained survives in `_in_flight` across loop teardown. A
   subsequent test in loop B that calls `fire_and_forget_extraction` either silently skips
   (if the stale task reports `not done()`) or awaits a task tied to a closed loop. Today
   every test drains explicitly — the property is fragile, not enforced.
2. *Future-caller silent drop.* If any future flow (parallel tool that runs an extractor,
   per-signal mining, sub-agent that forks deps and extracts) calls
   `fire_and_forget_extraction` while another extraction is in flight, the second call is
   silently dropped. Single-flight is correct for cadence-gated fire-and-forget; it is not
   correct as a global invariant.
3. *Spirit of the CLAUDE.md rule.* The rule exists to prevent module state hazards
   regardless of where the module sits in the package layout.

**Failure cost today:** Low — current foreground-only callers serialize naturally and tests
drain. The hazard is latent.

**Outcome:** The in-flight task ref is owned by `CoDeps.runtime` (per-deps slot). Each
session and each forked sub-agent deps gets its own slot. No module-level state in
`_distiller.py`. Behavior under current call graph is identical; future concurrent callers
do not collide.

## Scope

**In:**
- `co_cli/deps.py` — add `extraction_task` field to `CoRuntimeState`.
- `co_cli/knowledge/_distiller.py` — remove module-global `_in_flight`; thread `deps`
  through `drain_pending_extraction` and `_on_extraction_done`.
- `co_cli/main.py` — update `drain_pending_extraction` call site to pass `deps`.
- `tests/knowledge/test_distiller_window.py` — update `drain_pending_extraction` call sites
  to pass `deps`.
- `evals/eval_memory_extraction_flow.py` — update `drain_pending_extraction` call sites to
  pass `deps`.

**Out:**
- No changes to extraction semantics, single-flight behavior, or compaction-boundary bypass.
- No changes to `last_extracted_message_idx`/`last_extracted_turn_idx` placement (these
  remain on `CoSessionState` — they are persistent cursors, not transient task refs).
- No spec updates (`/sync-doc` handles those post-delivery if needed).
- No changes to `_dream.py` (uses `build_transcript_window` only — does not touch the slot).

## Behavioral Constraints

1. Single-flight semantics preserved: a second `fire_and_forget_extraction` call while a
   prior task is still running is still skipped — the check just reads `deps.runtime.extraction_task`
   instead of the module global. Cadence-gated extraction tolerates skip.
2. Compaction-boundary bypass preserved: `extract_at_compaction_boundary` still calls
   `drain_pending_extraction` first, then awaits `_run_extraction_async` directly without
   touching `deps.runtime.extraction_task`. The done callback only fires for tasks created
   via `fire_and_forget_extraction`, so the slot semantics remain correct.
3. Sub-agent isolation preserved: `fork_deps` (`deps.py:236-271`) already creates a fresh
   `CoRuntimeState`, so a forked sub-agent never shares the parent's task slot.
4. `reset_for_turn` (`deps.py:152-158`) must NOT clear `extraction_task`. The task spans
   turn-N's fire and (potentially) turn-N+1's drain — it is cross-turn state, sibling to
   `compaction_failure_count` and `consecutive_low_yield_proactive_compactions`.
5. `_on_extraction_done` must clear the slot only if the slot still references *this* task.
   If a later caller has already replaced the slot (impossible under current single-flight
   logic but cheap to defend against), we must not stomp the new ref.
6. No new error paths. Existing CancelledError / Exception handling in
   `_run_extraction_async` is unchanged.

## High-Level Design

### `CoRuntimeState` field addition

```python
# In co_cli/deps.py, CoRuntimeState dataclass
extraction_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
```

Field placement: cross-turn group (alongside `compaction_failure_count`, `agent_depth`,
`consecutive_low_yield_proactive_compactions`). NOT touched by `reset_for_turn`.

`fork_deps` requires no change — `CoRuntimeState(agent_depth=...)` already produces a fresh
default which is None for the new field. Sub-agent extraction tasks (if ever introduced)
would live on the sub-agent's runtime, not the parent's.

`asyncio` import is already present in the file (verify; otherwise add at top).

### `_distiller.py` rewrite

```python
# Remove (delete line 127):
# _in_flight: asyncio.Task[None] | None = None

# _on_extraction_done — bind deps via closure in fire_and_forget_extraction
def _make_done_callback(deps: "CoDeps") -> Callable[[asyncio.Task[None]], None]:
    def _on_done(task: asyncio.Task[None]) -> None:
        # Only clear if the slot still references this task (defensive — Constraint 5)
        if deps.runtime.extraction_task is task:
            deps.runtime.extraction_task = None
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.debug("Extraction task exception: %s", exc)
    return _on_done


def fire_and_forget_extraction(
    delta: list,
    deps: "CoDeps",
    frontend: "Frontend | None" = None,
    *,
    cursor_start: int,
    advance_cursor: bool = True,
) -> None:
    """Launch extraction as a background task. Skips if one is already running."""
    existing = deps.runtime.extraction_task
    if existing is not None and not existing.done():
        logger.debug("Extraction already in progress, skipping")
        return

    task = asyncio.get_running_loop().create_task(
        _run_extraction_async(
            delta,
            deps,
            frontend,
            cursor_start=cursor_start,
            advance_cursor=advance_cursor,
        ),
        name="memory_extraction",
    )
    deps.runtime.extraction_task = task
    task.add_done_callback(_make_done_callback(deps))


async def drain_pending_extraction(deps: "CoDeps", timeout_ms: int = 10_000) -> None:
    """Await the in-flight extraction task with a timeout. Cancel on timeout."""
    task = deps.runtime.extraction_task
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_ms / 1000)
    except TimeoutError:
        logger.debug("Drain timeout — cancelling extraction")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except Exception:
        logger.debug("Drain failed", exc_info=True)
    finally:
        if deps.runtime.extraction_task is task:
            deps.runtime.extraction_task = None


async def extract_at_compaction_boundary(...):
    # First call updated to pass deps
    await drain_pending_extraction(deps)
    # ... rest unchanged
```

### Call-site updates

| File | Line | Change |
|------|------|--------|
| `co_cli/main.py` | 180 | `await drain_pending_extraction(deps)` (was `drain_pending_extraction()`) |
| `co_cli/knowledge/_distiller.py` | 247 | `await drain_pending_extraction(deps)` |
| `tests/knowledge/test_distiller_window.py` | 194 | `await drain_pending_extraction(deps, timeout_ms=1000)` |
| `tests/knowledge/test_distiller_window.py` | 327 | `await drain_pending_extraction(deps, timeout_ms=5_000)` |
| `evals/eval_memory_extraction_flow.py` | 205 | `await drain_pending_extraction(deps, timeout_ms=...)` |
| `evals/eval_memory_extraction_flow.py` | 405 | `await drain_pending_extraction(deps, timeout_ms=...)` |

`_drain_and_cleanup` in `main.py:176` already takes `deps` — pass it through.

## Implementation Plan

### TASK-1 — Add `extraction_task` field to `CoRuntimeState` ✓ DONE

**files:**
- `co_cli/deps.py`

**done_when:** `CoRuntimeState` declares `extraction_task: asyncio.Task[None] | None` with
default `None`. `reset_for_turn` does NOT touch the new field. `asyncio` import present.
Type-checking pass: `uv run pyright co_cli/deps.py` clean.

**success_signal:** `python -c "from co_cli.deps import CoRuntimeState; r = CoRuntimeState(); assert r.extraction_task is None"`
exits 0.

### TASK-2 — Rewrite `_distiller.py` to use `deps.runtime.extraction_task` ✓ DONE

**files:**
- `co_cli/knowledge/_distiller.py`

**done_when:**
- Module-level `_in_flight` is removed.
- `fire_and_forget_extraction` reads/writes `deps.runtime.extraction_task`.
- `_on_extraction_done` is replaced by a closure factory that captures `deps` and only
  clears the slot when it still holds the same task ref.
- `drain_pending_extraction` accepts `deps` as first positional arg, reads/clears
  `deps.runtime.extraction_task`.
- `extract_at_compaction_boundary` calls `drain_pending_extraction(deps)` (single update).
- `grep -n "_in_flight\|global _in_flight" co_cli/knowledge/_distiller.py` returns nothing.

**success_signal:** `uv run pytest tests/knowledge/test_distiller_window.py -x` passes after
TASK-3's call-site updates land. (This task and TASK-3 must land together — split for
review clarity, applied as one diff.)

### TASK-3 — Update non-test call sites and tests/evals ✓ DONE

**files:**
- `co_cli/main.py`
- `tests/knowledge/test_distiller_window.py`
- `evals/eval_memory_extraction_flow.py`

**done_when:**
- All four `drain_pending_extraction(...)` call sites listed in the High-Level Design table
  pass `deps` as the first positional arg.
- `grep -rn "drain_pending_extraction()" --include='*.py' .` returns nothing (no zero-arg
  calls remain).
- `uv run pytest tests/knowledge/test_distiller_window.py -x` passes (existing assertions
  unchanged — only the function-call signature is updated).
- Full suite still green: `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`.

**success_signal:** Cadence flow under `co chat` still extracts knowledge after N turns
without changes to user-visible behavior. Compaction flow still extracts and pins cursor.

### TASK-4 — Add regression test for cross-deps slot isolation ✓ DONE

**files:**
- `tests/knowledge/test_distiller_window.py` (one new test added)

**done_when:**
A new async test in the file demonstrates that two `CoDeps` instances each hold their own
`extraction_task` slot. Sketch:

```python
@pytest.mark.asyncio
async def test_extraction_task_is_per_deps(tmp_path: Path) -> None:
    """Two CoDeps instances must not share the in-flight extraction slot."""
    deps_a = _make_test_deps(tmp_path / "a")
    deps_b = _make_test_deps(tmp_path / "b")
    msg = [ModelRequest(parts=[UserPromptPart(content="X")])]

    # Fire on deps_a — deps_b's slot must remain None
    fire_and_forget_extraction(msg, deps=deps_a, cursor_start=0)
    assert deps_a.runtime.extraction_task is not None
    assert deps_b.runtime.extraction_task is None

    # Drain via deps_a only
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        await drain_pending_extraction(deps_a, timeout_ms=5_000)
    assert deps_a.runtime.extraction_task is None
```

**success_signal:** `uv run pytest tests/knowledge/test_distiller_window.py::test_extraction_task_is_per_deps -x`
passes. Behavior under deletion: if TASK-2 reverts to module-global state, this test fails
because `deps_b.runtime.extraction_task` would also be set after firing on `deps_a`.

## Testing

Per-task scoped runs during dev:
- `uv run pytest tests/knowledge/test_distiller_window.py -x`

Pre-ship full run (timestamped log per CLAUDE.md):
- `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`

Manual smoke:
- `uv run co chat`, run 3+ turns, verify cadence extraction fires (logs: `co.memory.extraction` span).
- Trigger compaction (large transcript or `/compact`), verify cursor pin and extraction.

## Open Questions

None — design is mechanical. The only judgment call (where the field lives) resolved to
`CoRuntimeState` over `CoSessionState`: the task ref is transient orchestration state and
sits naturally next to `compaction_failure_count`. Persistent extraction cursors stay on
`CoSessionState`.

---

## Delivery Summary (2026-04-25)

Delivered via `/deliver` (solo path, no orchestrate/review-impl gates).

**Diff scope:** 5 files, +88 / -43.
- `co_cli/deps.py` — added `extraction_task` to `CoRuntimeState` (cross-turn, not in `reset_for_turn`); imported `asyncio`.
- `co_cli/knowledge/_distiller.py` — removed module-level `_in_flight`; threaded `deps` through `fire_and_forget_extraction`, `drain_pending_extraction`, and `extract_at_compaction_boundary`; introduced `_make_extraction_done_callback` factory with identity check; applied identity check in `drain` finally block.
- `co_cli/main.py` — updated `drain_pending_extraction(deps)` call site; merged the two `if deps is not None` blocks in `_drain_and_cleanup`.
- `tests/knowledge/test_distiller_window.py` — updated 4 call sites; dropped now-unused `SilentFrontend` local + import; added `test_extraction_task_slot_is_per_deps` regression test.
- `evals/eval_memory_extraction_flow.py` — updated 4 call sites.

**Beyond-plan cleanups bundled into TASK-2/TASK-3** (per conversation):
- Dropped dead `frontend: Frontend | None` parameter from all three distiller functions (never read in any body) and 4 call sites.
- Renamed local `_model` → `model` in `_run_extraction_async` (leading underscore meaningless on a local).
- Applied identity-check pattern in `drain_pending_extraction` finally block (matches `_on_done` for consistency).

**Quality gates:**
- `scripts/quality-gate.sh lint --fix` → clean.
- `tests/knowledge/test_distiller_window.py -x -v` → 12 passed.
- `scripts/quality-gate.sh full` → 634 passed in 3:49.

**Version:** 0.8.5 → 0.8.6 (refactor / hazard-reduction → feature parity per even-patch convention).

**Behavior preserved:**
- Single-flight cadence semantics unchanged (slot read/write moved from module global to `deps.runtime`).
- Compaction-boundary bypass preserved: drain first, then await `_run_extraction_async` directly without populating the slot.
- Sub-agent isolation: `fork_deps` already constructs fresh `CoRuntimeState` so forked deps get an independent slot — no extra wiring needed.
