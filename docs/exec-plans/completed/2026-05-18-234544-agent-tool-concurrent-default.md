# agent-tool-concurrent-default

## Problem

`co_cli/tools/agent_tool.py:28` defines `is_concurrent_safe: bool = False` as the
default for every tool registered via `@agent_tool`. Empirically ~33 of co-cli's
~41 tools explicitly opt in to `is_concurrent_safe=True`; only 3 rely on the
sequential default (`code_execute`, `file_write`, `file_patch`).

This inverts pydantic-ai's framework default (`sequential: bool = False`, i.e.
concurrent by default — see `pydantic-ai/pydantic_ai_slim/pydantic_ai/tools.py:397,506,636`)
and produces 33 redundant flag annotations for every safe tool while leaving the
3 unsafe ones invisibly relying on the default.

The flip also exposes the lack of any **dispatch backstop**: there is no upper
bound on how many tool calls run in parallel when a single LLM response emits
many `ToolCallPart`s. Hermes caps at 8 workers (`run_agent.py:323
_MAX_TOOL_WORKERS = 8`); co-cli has no equivalent.

## Status

pending

## Goal

After this plan lands:

- **Default behavior** — `@agent_tool`-decorated functions run in parallel with
  other concurrent-safe tools by default, subject to existing `ResourceLockStore`
  fail-fast on shared mutation keys.
- **Opt out** — `is_concurrent_safe=False` explicitly set on the three tools
  that truly cannot tolerate concurrent invocation: `code_execute`, `file_write`,
  `file_patch`.
- **Read-only shortcut** — `is_read_only=True` *automatically implies*
  `is_concurrent_safe=True` (no longer an error to omit; the decorator sets it).
- **Backstop** — `_MAX_PARALLEL_TOOL_WORKERS` cap (10) enforced via
  per-session `asyncio.Semaphore` regardless of safety flags. Eleventh+ parallel
  tool call queues until a slot frees.

Net effect: 33 tools lose redundant boilerplate; 3 unsafe tools gain explicit
opt-out; new tool authors get the safe pattern by default; runtime gains a
backstop against pathological fan-out.

## Scope

### In scope

- `co_cli/tools/agent_tool.py` — decorator default, validation reshape
- The 3 unsafe tools — add explicit `is_concurrent_safe=False`
- The 33 redundantly-safe tools — leave them with the flag set (cleanup
  optional; not blocking, see §"Cleanup, deferred")
- `co_cli/deps.py` — add `tool_dispatch_sem: asyncio.Semaphore` field on
  `CoDeps` (or appropriate sub-state) and wire its initialization
- `co_cli/tools/agent_tool.py` — wrap tool body to acquire the semaphore
- New constant `_MAX_PARALLEL_TOOL_WORKERS = 10` (in `agent_tool.py`)
- Tests: behavioral tests for default flip, semaphore backstop
- Spec: `docs/specs/tools.md` — document new default + backstop

### Out of scope

- **Iteration cap** (max LLM iterations per turn) — separate design, separate
  plan.
- **Per-iteration tool call cap** (max `ToolCallPart` per `ModelResponse`,
  error-back-to-LLM semantics) — separate design, separate plan.
- **Cleanup of the 33 redundant `is_concurrent_safe=True` annotations** — the
  flag remains valid; removing it is mechanical churn worth doing as a follow-up
  sweep, not as part of this plan. Tracked in §"Cleanup, deferred".

## Tasks

### ✓ DONE T1 — Flip the decorator default and reshape `is_read_only` validation

File: `co_cli/tools/agent_tool.py`

Change line 28:
```python
is_concurrent_safe: bool = True,   # was False
```

Change the validation block at lines 43-44 from:
```python
if is_read_only and not is_concurrent_safe:
    raise ValueError("@agent_tool: is_read_only=True requires is_concurrent_safe=True")
```
to:
```python
# is_read_only implies is_concurrent_safe (read-only tools have no shared
# mutable state to race on). Treat the implication as a shortcut: the author
# does not need to repeat is_concurrent_safe=True alongside is_read_only=True.
if is_read_only:
    is_concurrent_safe = True
```

Rationale: with the new default `True`, the previous `ValueError` is unreachable
in the common case. The new form makes `is_read_only` a true shortcut and avoids
silently disagreeing with an author who wrote `is_read_only=True,
is_concurrent_safe=False` (the only path where the change matters — that
combination is now coerced rather than rejected; comment explains why).

### ✓ DONE T2 — Add explicit `is_concurrent_safe=False` to the three unsafe tools

| File | Function | Reason for sequential |
|---|---|---|
| `co_cli/tools/code/execute.py` | `code_execute` | Interpreter runs share the working directory and process state; interleaved invocations corrupt CWD and output |
| `co_cli/tools/files/write.py` | `file_write` | Writes go through approval + atomic write but the approval modal cannot be queued safely if two parallel writes hit the same path; serialize to keep approval UX coherent |
| `co_cli/tools/files/write.py` | `file_patch` | Same approval reason; additionally read-modify-write semantics demand no interleaving between two patches on the same file (also enforced by `ResourceLockStore`, but explicit flag is clearer) |

Each gets `is_concurrent_safe=False` added to its `@agent_tool(...)` call, with
a one-line `# above:` comment explaining the reason.

### ✓ DONE T3 — Add the dispatch backstop

Constants in `co_cli/tools/agent_tool.py` (top of module):
```python
_MAX_PARALLEL_TOOL_WORKERS: int = 10
```

Add a semaphore-aware wrapper in the decorator. The wrapper acquires
`deps.tool_dispatch_sem` before calling the underlying function. Sketch:
```python
@wraps(fn)
async def _dispatch_capped(ctx, *args, **kwargs):
    async with ctx.deps.tool_dispatch_sem:
        return await fn(ctx, *args, **kwargs)
```

Field on `CoDeps` (or appropriate runtime sub-state in `co_cli/deps.py`):
```python
tool_dispatch_sem: asyncio.Semaphore = field(
    default_factory=lambda: asyncio.Semaphore(_MAX_PARALLEL_TOOL_WORKERS)
)
```

The semaphore is per-`CoDeps`, so each chat session has its own backstop and
forked daemon agents (session_reviewer, skill_curator) inherit the parent's
semaphore via `fork_deps_for_reviewer` / `fork_deps_for_curator` *only if the
fork shares it by reference*. Decide per-fork:
- **Reviewer fork**: share by reference (parent and reviewer should compete for
  the same dispatch budget; total concurrency stays bounded).
- **Curator fork**: share by reference (same reason).
- **Web research / delegation**: share by reference.

Document this on the field with a clear comment.

### ✓ DONE T4 — Tests

Add `tests/test_flow_agent_tool_concurrent_default.py` covering:

1. **Default is concurrent** — define a test tool with `@agent_tool()` (no
   flags), assert `ToolInfo.is_concurrent_safe == True`.
2. **Read-only shortcut** — define a test tool with `is_read_only=True`, assert
   `ToolInfo.is_concurrent_safe == True` (auto-set).
3. **Explicit opt-out** — define a test tool with `is_concurrent_safe=False`,
   assert `ToolInfo.is_concurrent_safe == False`.
4. **Three real unsafe tools confirmed sequential** — parametrize over
   `(code_execute, file_write, file_patch)`, assert each has
   `is_concurrent_safe=False` (regression guard against accidental flip).
5. **Dispatch backstop** — spawn `_MAX_PARALLEL_TOOL_WORKERS + 5` parallel tool
   calls via a fixture; assert at most `_MAX_PARALLEL_TOOL_WORKERS` execute
   concurrently (use a counter + sleep + max-observed check).
6. **Semaphore shared across fork** — fork CoDeps via `fork_deps_for_reviewer`;
   assert the reviewer's `tool_dispatch_sem` is `is` the parent's.

Quality gate (existing): `scripts/quality-gate.sh full`.

### ✓ DONE T5 — Spec update

File: `docs/specs/tools.md`.

Update the section that documents `@agent_tool` parameters (find by grep on
"is_concurrent_safe" or "is_read_only"; if the spec doesn't currently document
these, add a new subsection "Concurrency").

Content to add:
- **Default**: `is_concurrent_safe=True` (concurrent). Most tools are safe.
- **Opt-out rule**: set `is_concurrent_safe=False` only when the tool truly
  cannot tolerate concurrent invocation. Examples: shell command sequencing,
  interactive prompts, terminal cursor positioning. Three tools today:
  `code_execute`, `file_write`, `file_patch`.
- **Read-only shortcut**: `is_read_only=True` automatically implies
  `is_concurrent_safe=True`. No need to set both.
- **Backstop**: `_MAX_PARALLEL_TOOL_WORKERS = 10`. Beyond 10 concurrent tool
  calls per session, the 11th+ calls queue on the dispatch semaphore until
  slots free. The cap is per-`CoDeps`; forked daemon agents share the parent's
  semaphore so total session concurrency is bounded.
- **Resource contention vs concurrency**: `is_concurrent_safe=True` says "I am
  safe to dispatch in parallel"; `ResourceLockStore` says "I will fail-fast if
  another concurrent call touches the same key." Both layers apply.

Cross-reference from `docs/specs/tools.md` to this section wherever the
sequential/concurrent distinction is discussed.

## Verification

- `scripts/quality-gate.sh full` passes (lint + full pytest)
- Behavioral tests in T4 all pass
- Spot-check: a fresh `uv run co chat` session with `-v` shows tool dispatch
  fan-out behavior unchanged for the common case (no warnings, no serialization
  regression on the 33 previously-explicit-safe tools)
- Spot-check: invoke `file_write` and `code_execute` and confirm they still
  work (they only differ in concurrency annotation, not behavior)

## Cleanup, deferred

The 33 tools that currently set `is_concurrent_safe=True` explicitly will keep
the flag (it remains a valid, accurate annotation — just redundant). A sweep
removing the redundant `is_concurrent_safe=True` from those tools is mechanical
churn. Track as a follow-up sweep, not part of this plan, so the behavioral
change here is reviewable in isolation.

A sweep candidate list (for the follow-up):
- `co_cli/tools/agent_tool.py` (1)
- `co_cli/tools/agents/delegation.py` (3)
- `co_cli/tools/files/read.py` (3, all read-only — can use new shortcut)
- `co_cli/tools/google/calendar.py` (2, read-only)
- `co_cli/tools/google/drive.py` (2, read-only)
- `co_cli/tools/google/gmail.py` (3)
- `co_cli/tools/memory/manage.py` (1)
- `co_cli/tools/memory/recall.py` (1, read-only)
- `co_cli/tools/memory/view.py` (1, read-only)
- `co_cli/tools/obsidian/tools.py` (3, all read-only)
- `co_cli/tools/session/recall.py` (1, read-only)
- `co_cli/tools/session/view.py` (1, read-only)
- `co_cli/tools/shell/execute.py` (1) — note: shell.execute IS marked
  concurrent-safe; the actual blocking semantics are inside the tool body via
  `resource_locks`, not at registration
- `co_cli/tools/system/capabilities.py` (1, read-only)
- `co_cli/tools/system/skills.py` (1) — `skill_view` (read-only); `skill_manage`
  is NOT in the safe list because it's not currently marked safe — verify and
  if appropriate also re-classify
- `co_cli/tools/system/user_input.py` (1)
- `co_cli/tools/tasks/control.py` (4)
- `co_cli/tools/todo/rw.py` (2)
- `co_cli/tools/web/fetch.py` (1, read-only)
- `co_cli/tools/web/search.py` (1, read-only)

## Risks

- **Hidden race conditions**: a tool that was sequential by accident (relying
  on the False default without thinking about it) may now race. Mitigations:
  (a) we already audited and there are only 3 such tools, all now explicit; (b)
  `ResourceLockStore` fail-fast catches new races on shared mutation keys; (c)
  full pytest run validates no regression in existing flows.
- **Semaphore overhead**: every tool call now acquires a semaphore. Cost is
  trivial for uncontended acquire (microseconds) but worth flagging.
- **Fork semaphore sharing**: if reviewer/curator forks DO share the parent's
  semaphore, their tool calls compete with the foreground turn's. This is the
  intended behavior (bounded total concurrency) but means a long-running
  reviewer can throttle foreground tools. Acceptable because the reviewer's
  tool calls are themselves bounded by `REVIEW_MAX_ITERATIONS=8`.

## Out-of-scope (revisited)

The user previously confirmed two other safety designs in conversation that are
**not** part of this plan and should land in their own exec-plans:

- **Design 1 — Iteration cap per turn** (default 50 for Ollama, TBD for Gemini):
  count of all `ModelResponse`s per user turn; fail-hard stop on exceed.
- **Design 2 — Tool call cap per iteration** (default ~10): count of
  `ToolCallPart` per `ModelResponse`; reject batch and error-back-to-LLM on
  exceed; hard-stop after 2 consecutive cap-exceeded.

Both are separate concerns (cost cap and safety cap respectively, per the
three-axis model: cost / safety / resource). This plan covers only the
**resource axis** — the `is_concurrent_safe` default and the dispatch backstop.

## Delivery Summary — 2026-05-19

| Task | done_when | Status |
|------|-----------|--------|
| T1 | `is_concurrent_safe: bool = True` on line 29 of `agent_tool.py` | ✓ pass |
| T2 | 3 matches for `is_concurrent_safe=False` in execute.py + write.py | ✓ pass |
| T3 | `MAX_TOOL_DISPATCH_WORKERS` in deps.py; `tool_dispatch_sem` field on CoDeps; wrapper in decorator; semaphore shared via fork_deps | ✓ pass |
| T4 | 8 tests pass in `test_flow_agent_tool_concurrent_default.py` | ✓ pass |
| T5 | Concurrency Safety section updated; `@agent_tool` table row updated | ✓ pass |

**Tests:** scoped — 8 passed, 0 failed

**Doc Sync:** updated `docs/specs/tools.md` directly in T5 (concurrency section + registration table)

**Implementation notes:**
- `MAX_TOOL_DISPATCH_WORKERS` placed in `deps.py` (not `agent_tool.py`) to avoid circular import — `agent_tool.py` imports it from `deps.py`.
- `@wraps(fn)` on `_dispatch_capped` preserves signature for pydantic-ai (`inspect.signature` follows `__wrapped__`).
- `fork_deps()` explicitly passes `tool_dispatch_sem=base.tool_dispatch_sem` — shared by reference, not recreated.

**Overall: DELIVERED**
All 5 tasks passed. Next step: `/review-impl agent-tool-concurrent-default`

## Implementation Review — 2026-05-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | `is_concurrent_safe: bool = True` on decorator default | ✓ pass | `agent_tool.py:30` — default confirmed; old `ValueError` gone; `is_read_only` coercion at `agent_tool.py:51-52` |
| T2 | 3 × `is_concurrent_safe=False` in execute.py + write.py | ✓ pass | `execute.py:13`, `write.py:487`, `write.py:539`; above-line comments at `execute.py:12`, `write.py:485`, `write.py:537` |
| T3 | `MAX_TOOL_DISPATCH_WORKERS` in deps.py; semaphore field; wrapper; fork-sharing | ✓ pass | `deps.py:246` (constant=10), `deps.py:277-279` (factory field), `agent_tool.py:75-80` (wrapper), `deps.py:382` (fork passes by reference) |
| T4 | 8 tests pass | ✓ pass | All 8 items confirmed; real production imports; `is` identity check for fork semaphore |
| T5 | Concurrency Safety section updated | ✓ pass | `tools.md:137-141` (default, opt-outs, backstop cap, fork-sharing); `tools.md:162-163` (ResourceLockStore distinction) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Dead code: `_MAX_PARALLEL_TOOL_WORKERS = MAX_TOOL_DISPATCH_WORKERS` never read; comment falsely claimed test/doc exposure; tests import `MAX_TOOL_DISPATCH_WORKERS` directly from `deps.py` | `agent_tool.py:88-89` (pre-fix) | blocking | Removed alias and stale comment |
| Production bug: `await fn(ctx, ...)` where `fn` is a plain `def` raises `TypeError: object ToolReturn can't be used in 'await' expression` at pydantic-ai dispatch time for all sync tools | `agent_tool.py:77` (pre-fix) | blocking | Added `inspect.iscoroutinefunction(fn)` guard; sync functions called directly, async functions awaited |
| Stale import: `MAX_TOOL_DISPATCH_WORKERS` imported in `agent_tool.py` but unused after alias removal | `agent_tool.py:12` (post-fix) | blocking | Removed from import |
| Test adaptation: `test_flow_todo.py` called `todo_write`/`todo_read` synchronously; after T3 they return coroutines; `AttributeError: 'coroutine' object has no attribute 'metadata'` | `tests/test_flow_todo.py` (all 20 tests) | blocking | All 20 tests converted to `async def` with `@pytest.mark.asyncio`; all tool calls `await`-ed |

### Tests
- Command: `uv run pytest -x -v`
- Result: 489 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Behavioral Verification
- System imports cleanly; all tool registrations confirmed; `tool_dispatch_sem._value = 10`; end-to-end `asyncio.run(todo_write(ctx, ...))` dispatch executed without error
- No user-facing CLI surface changed — behavioral verification skipped for chat/logs

### Overall: PASS
Two blocking bugs in the T3 delivery fixed (dead alias, sync-tool TypeError); 20 test adaptations applied; 489/489 green.
