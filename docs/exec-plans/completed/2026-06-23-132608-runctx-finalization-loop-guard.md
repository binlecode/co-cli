# REPL loop-boundary guard for cross-Context run-context finalization

## Context

A streaming turn can leave pydantic-ai's separate-Context `wrap_task` (capability chain + `_streaming_handler`) to be garbage-collected while still suspended inside `set_current_run_context`. Its `finally: _CURRENT_RUN_CONTEXT.reset(token)` (`pydantic_ai/_run_context.py:152`) then runs in a foreign `contextvars.Context`, raising:

```
ValueError: <Token ...> was created in a different Context
```

surfaced as `Exception ignored in: <coroutine object CombinedCapability.wrap_model_request ...>` (the GC-finalizer signature) and, in the user's session, escalating to prompt_toolkit's `Unhandled exception in event loop / Press ENTER to continue` — pausing/breaking the REPL.

**Reproduction-first investigation (completed, this session):** drove the real `run_turn` + capability chain across 9+ faithful scenarios — single/double cancel, drop-without-await, abandon-CM-without-`__aexit__`, fire-and-forget shutdown, and the **real `app.run_async()` REPL with EOF mid-turn** — all clean. asyncio's task machinery (`_cancel_all_tasks` at shutdown, the async-generator finalizer) always drains `wrap_task` *in its own Context*. The `ValueError` only fires when `wrap_task`'s coroutine is closed by **raw cyclic GC in a foreign Context** rather than by asyncio's in-Context task cancellation. The exact trigger was not staged. The candidate space is **in-process only**: an in-process executor thread (`asyncio.to_thread`) or pure single-loop cyclic-GC timing, where a `gc.collect()` during a *later* turn closes an orphaned `wrap_task` coroutine in that turn's Context. (The dream daemon is **not** a candidate: it is a fully detached separate process — `subprocess.Popen` + `setsid`, `daemons/dream/_process.py:73`, not the `asyncio.run` at `process.py:64` which executes inside that child process — so it shares no object graph or `ContextVar` instance with the main process.)

**Decisions carried in from that investigation:**
- The stall timeout and the orchestrate streaming loop are exonerated (a prior fix premised on them was withdrawn — `asyncio.timeout` issues a single one-shot cancel, verified against `cpython/asyncio/timeouts.py:121`).
- The proximate cause is an upstream pydantic-ai gap: the unguarded `reset` in `set_current_run_context`. It is byte-identical in 1.107.0 (verified), so the separately-owned version bump will not fix it. An upstream issue is the real fix but is not co's to ship.
- The orphan is **functionally harmless** — the run has already completed/abandoned; only the contextvar GC cleanup fails. The sole damage is the exception escaping co's event loop to the UI.

co installs **no `sys.unraisablehook` and no `loop.set_exception_handler`** (grep-verified), so this benign GC-cleanup noise falls through to Python's default unraisable printer and prompt_toolkit's default loop handler — which is why it reaches the user as a crash.

## Problem & Outcome

**Problem:** A harmless, non-deterministic, upstream-rooted GC-finalization `ValueError` escapes co's REPL event loop and crashes/pauses the interactive session.

**Outcome:** The specific cross-Context run-context-reset `ValueError` is recognized at co's REPL boundary, debug-logged, and suppressed — it never reaches prompt_toolkit's crash UI or spams stderr. Every other exception is handled exactly as before (delegated to the prior default). The REPL stays alive.

**Failure cost:** Without the guard, a rare cross-loop GC interleaving crashes the whole interactive session mid-use with an opaque contextvar error and a `Press ENTER to continue` stall — non-deterministic, hard to attribute, and trust-eroding. The guard is the only co-controlled lever, since the root reset is upstream and the trigger resists reproduction.

## Scope

**In scope:**
- Install a `sys.unraisablehook` around the REPL's `asyncio.run` (`_start_chat`) that suppresses + debug-logs the specific run-context-reset `ValueError` from a finalized `wrap_model_request`/run-context coroutine, and delegates everything else to the prior hook. Restore the prior hook in `finally`.
- A narrow matcher scoped tightly to this signature.

**Out of scope:**
- A `loop.set_exception_handler` guard in `_chat_loop`. **Cut at Gate 1** (was TASK-2). The GC-finalizer that produces this signature routes to `sys.unraisablehook`, not the loop callback path (reproduced end-to-end, see Context). And a co-installed loop handler at the top of `_chat_loop` is shadowed for the entire live-REPL duration anyway: prompt_toolkit's `app.run_async` installs its own `loop.set_exception_handler(self._handle_exception)` (`prompt_toolkit/application/application.py:826-834`), and that handler (`application.py:1006-1028` — the `Unhandled exception in event loop` / `Press ENTER to continue` printer) never delegates to the prior handler. So co's handler could never fire while the REPL is live. If a genuine loop-path occurrence is ever observed, the correct fix is `app.run_async(set_exception_handler=False)` with co's delegating handler owning the loop (and replicating prompt_toolkit's `run_in_terminal` print for non-matching errors) — a materially bigger change, its own decision, deferred.
- The pydantic-ai version bump (separate team).
- Patching vendored pydantic-ai's unguarded `reset` (upstream; file an issue separately).
- Draining `runtime.turn_task` on exit / any change to the turn-dispatch lifecycle (the investigation showed single-loop teardown already drains cleanly; not the trigger).
- Chasing/fixing the cross-loop dream-daemon trigger (deferred unless it recurs).
- Any change to `orchestrate.py` / the stall timeout.

## Behavioral Constraints

- **Narrow suppression only.** The matcher must fire only for `ValueError` whose message indicates a contextvars token `was created in a different Context` originating from pydantic-ai's run-context var. Any broader `ValueError` (or other exception) must pass through to the prior hook unchanged. A false-positive suppression that hides a real error is worse than the bug. (Verified: a `ValueError("... was created in a different Context")` *without* the `current_run_context` var name delegates to the prior hook — the two-substring AND does real narrowing.)
- **Delegation, not replacement.** The hook captures the prior `sys.unraisablehook` and calls it for non-matching cases — no behavior change for anything else (including the existing `KeyboardInterrupt` safety net at `_start_chat`).
- **Restore on exit.** The process-global `sys.unraisablehook` is restored in a `finally` so the guard does not leak past the REPL (matters for tests and for `co` subcommands sharing the process).
- **Debug-level logging.** Suppressed events are logged at debug (not warning) — they are expected, benign GC noise, and must not become console clutter.

## High-Level Design

A single narrow predicate identifies the signature:

```python
def _is_runctx_finalization_error(exc: BaseException | None) -> bool:
    return (
        isinstance(exc, ValueError)
        and "was created in a different Context" in str(exc)
        and "current_run_context" in str(exc)
    )
```

(The `ValueError` message embeds the `ContextVar name='pydantic_ai.current_run_context'`, so both substrings are present — keeping the match scoped to this exact upstream issue rather than any "different Context" error.)

Logging uses `logging.getLogger(__name__)` inline, matching existing `main.py` style (no module-level `logger` exists there — CD-m-1).

**`sys.unraisablehook`** (the GC-finalization path from the traceback) — installed via a small `@contextmanager` so install/restore is one testable unit (CD-m-2), wrapped around the REPL's `asyncio.run` in `_start_chat`:

```python
@contextmanager
def _runctx_finalization_guard():
    prior = sys.unraisablehook
    def _hook(unraisable):
        if _is_runctx_finalization_error(unraisable.exc_value):
            logging.getLogger(__name__).debug(
                "suppressed benign pydantic-ai run-context GC finalization "
                "(upstream: unguarded ContextVar reset): %r", unraisable.exc_value)
            return
        prior(unraisable)
    sys.unraisablehook = _hook
    try:
        yield
    finally:
        sys.unraisablehook = prior

# in _start_chat:
with _runctx_finalization_guard():
    try:
        asyncio.run(_chat_loop(...))
    except KeyboardInterrupt:
        pass
```

The predicate and the guard installer live alongside the REPL entry in `main.py` — they are REPL-boundary policy, not agent-loop logic.

The end-to-end mechanism is reproduced (this session): a coroutine suspended inside the real `set_current_run_context` body, its token set in a child `contextvars.Context`, then closed by `gc.collect()` from the main context, raises the `ValueError` and routes it to `sys.unraisablehook` with the exception on `unraisable.exc_value` — and the guard above suppresses exactly that, delegates a plain `ValueError`, and restores the prior hook on exit.

## Tasks

### ✓ DONE TASK-1 — Narrow predicate + sys.unraisablehook guard in `_start_chat`
- **files:** `co_cli/main.py`
- **done_when:** `_start_chat` wraps `asyncio.run` in the `_runctx_finalization_guard()` contextmanager (installs the suppressing `sys.unraisablehook`, restores the prior hook in `finally`); the `_is_runctx_finalization_error` predicate exists; debug log message records the upstream-issue note so the guard is self-documenting/removable (PO-m-1); `uv run pytest tests/test_runctx_finalization_guard.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-guard.log` passes (predicate + hook cases from TASK-2).
- **success_signal:** A simulated run-context GC-finalization `ValueError` is swallowed (debug-logged) instead of printed; an unrelated `ValueError` still reaches the prior hook.
- **prerequisites:** none

### ✓ DONE TASK-2 — Functional test: narrow suppression, pass-through preserved
- **files:** `tests/test_runctx_finalization_guard.py` (new)
- **done_when:** tests assert (a) the unraisablehook suppresses a synthesized run-context-reset `ValueError` and delegates a plain `ValueError`/other exception to the prior hook; (b) a `ValueError` whose message contains `was created in a different Context` but *not* the `current_run_context` var name delegates to the prior hook (no false positive); (c) the prior `sys.unraisablehook` is restored after `_start_chat`'s guarded block exits; `uv run pytest tests/test_runctx_finalization_guard.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-guard.log` passes.
- **success_signal:** The guard provably suppresses only the target signature and leaves all other error handling intact.
- **prerequisites:** TASK-1

## Testing

- Functional, observable behavior only: feed synthesized exceptions through the installed hook and assert suppression (target signature) vs delegation (everything else), and hook restoration. No structural assertions.
- The real cross-Context GC orphan is non-deterministic and was not reproducible (see Context), so it is deliberately NOT asserted — the test exercises the guard's contract directly, which is the behavior this plan adds.
- Full suite at `/review-impl` and `/ship`.

## Open Questions

- **Upstream issue (follow-up, owner: maintainer):** file a pydantic-ai issue for the unguarded `_CURRENT_RUN_CONTEXT.reset(token)` in `set_current_run_context` (should swallow `ValueError` on cross-Context finalization). Record the issue URL in the guard's debug-log message / a one-line comment so the guard is self-documenting and can be removed once upstream ships a fix. Not a code task in this plan; tracked here so the guard does not silently become permanent dead-weight.

## Decisions

Both reviewers approved on C1 with `Blocking: none` (stop condition met — no C2). Core Dev empirically verified the two load-bearing runtime assumptions against CPython 3.12.12: the contextvars-reset `ValueError` message embeds `pydantic_ai.current_run_context` (via the Token repr), so the two-substring matcher fires; and a coroutine `close()` during cyclic GC routes to `sys.unraisablehook` with the `ValueError` on `unraisable.exc_value`.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-m-1 | adopt | `main.py` has no module-level `logger`; existing style is inline `logging.getLogger(__name__)`. Bare `logger.debug` would `NameError`. | HLD snippets now use `logging.getLogger(__name__).debug(...)` inline. |
| CD-m-2 | adopt | Extracting install+restore into a `@contextmanager` makes the hook-restore functionally testable without standing up the full REPL. | HLD now wraps the hook in `_runctx_finalization_guard()` contextmanager; TASK-1 done_when updated; TASK-2(c) asserts restore via that seam. |
| PO-m-1 | adopt | Keeps "narrow suppression" from silently becoming "suppress forever" — the upstream fix needs a durable thread. No implementation-scope expansion. | Added Open Questions entry to file the upstream issue and record its URL in the guard's debug message/comment; TASK-1 done_when notes the self-documenting log. |
| PO-m-2 | adopt (partial) | Debug-level is correct (benign noise); `%r` of the exc is already grep-correlatable. A counter/first-occurrence marker is ceremony not worth its cost. | No code change beyond the `%r` log already specified; recorded that debug-level + correlatable message is the deliberate choice. |
| CD note (SimpleNamespace) | adopt | Test can synthesize `unraisable` via `types.SimpleNamespace(exc_value=<real ValueError>)` since the hook reads only `.exc_value`. | Folded into TASK-2 approach (no separate row). |
| G1 — cut loop handler | adopt | The original TASK-2 (`loop.set_exception_handler` in `_chat_loop`) was cut at Gate 1. End-to-end repro confirms this signature routes to `sys.unraisablehook`, not the loop callback path; and prompt_toolkit's `app.run_async` installs `self._handle_exception` via `set_exception_handler_ctx` (`application.py:826-834`), shadowing any co handler set before `app.run_async()` for the whole live-REPL duration — and that handler never delegates to the prior. So co's loop handler could never fire while the REPL is live. Source-verified; the prior "Loop-handler restore — confirm (no change)" analysis missed the prompt_toolkit shadow. | Removed TASK-2 + its HLD block + the loop-handler test case; recorded the redesign path (`set_exception_handler=False` + co-owned delegating handler) as deferred Out-of-scope. |

## Delivery Summary — 2026-06-23

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_start_chat` wraps `asyncio.run` in `_runctx_finalization_guard()`; predicate exists; self-documenting debug log; guard test passes | ✓ pass |
| TASK-2 | tests assert narrow suppression, pass-through (plain/non-ValueError/different-Context-without-runctx-var), hook restoration | ✓ pass |

**Tests:** scoped — 5 passed, 0 failed (`tests/test_runctx_finalization_guard.py`)
**Doc Sync:** clean (private REPL-boundary guard — no public API/schema/spec change)

Implementation notes:
- `co_cli/main.py`: added `import sys`, extended `from contextlib import ... contextmanager`, added `_is_runctx_finalization_error` predicate + `_runctx_finalization_guard()` contextmanager, wrapped the `asyncio.run`/`KeyboardInterrupt` block in `_start_chat` with the guard.
- Test added one extra case beyond the 5 done_when bullets (`test_non_value_error_is_delegated`) to also exercise non-`ValueError` delegation — same file, no scope expansion.
- No `# noqa`/`# type: ignore` added (an initial lambda+noqa draft was refactored to a `_RecordingHook` class to keep the suite clean).

**Overall: DELIVERED**
All tasks passed done_when, lint clean, scoped tests green, doc sync not required.

## Implementation Review — 2026-06-23

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_start_chat` wraps `asyncio.run` in `_runctx_finalization_guard()`; predicate exists; self-documenting debug log; guard test passes | ✓ pass | `main.py:770-784` predicate (two-substring AND); `main.py:787-816` contextmanager (captures prior, installs `_hook`, delegates non-matching, restores in `finally`); `main.py:797-808` debug log records the upstream-issue note; `main.py:835-839` `_start_chat` wraps the `asyncio.run`/`KeyboardInterrupt` block |
| TASK-2 | tests assert narrow suppression, pass-through (plain/non-ValueError/different-Context-without-runctx-var), hook restoration; test passes | ✓ pass | `test_runctx_finalization_guard.py` — 5 tests: suppress target (real cross-Context error), delegate plain `ValueError`, delegate non-`ValueError`, delegate "different Context" *without* `current_run_context` (false-positive guard), restore prior hook on exit |

Stub-litmus: every test asserts observed delegation/suppression; `test_different_context_without_runctx_var_is_delegated` is load-bearing (drop the `current_run_context` substring check and it fails). No mocks/fakes — real `ValueError`/`contextvars`, `SimpleNamespace(exc_value=...)` faithful to the hook contract (reads only `.exc_value`), `_RecordingHook` is a real recorder. TASK-2(c) asserts restore via the contextmanager seam (CD-m-2-approved).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Trailing comment on `pass` (CLAUDE.md: no trailing comments) — pre-existing, on a line re-indented by this change | main.py:839 | minor | Moved comment above `pass` |

### Tests
- Command: `uv run pytest -v`
- Result: 832 passed, 0 failed (179.73s)
- Log: `.pytest-logs/<ts>-review-impl.log`; scoped guard log `.pytest-logs/<ts>-guard.log` (5 passed)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads; `chat` command present)
- Guard suppression/delegation: ✓ verified via the 5-test repro feeding synthesized exceptions through the installed hook (the real cross-Context GC trigger is non-deterministic and deliberately not asserted, per plan). Interactive `co chat` REPL is LLM-mediated → non-gating.
- `success_signal`: ✓ verified — simulated run-context finalization `ValueError` swallowed (debug-logged) while unrelated `ValueError` reaches the prior hook; guard provably narrows to the exact signature.

### Overall: PASS
The narrow `sys.unraisablehook` guard is implemented as specified, suppresses only the target upstream signature, delegates everything else, restores the prior hook on exit; full suite green, lint clean, boot smoke passes.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev runctx-finalization-loop-guard`
